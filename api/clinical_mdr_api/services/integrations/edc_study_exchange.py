"""Pure V2 exchange assembly and custody checks. No database or release authority.

The inherited canonical definition is never reconstructed from OSB scalars.
Native-only drafts require the actual USDM service document. Historical flat
snapshots are read-only evidence; this module only writes the V2 envelope.
"""

import base64
import gzip
import hashlib
import io
import json
import math
import uuid
import zlib
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal
from itertools import chain
from typing import Any, Iterable

from clinical_mdr_api.services.integrations.canonical_json import canonical_json

MAX_SOURCE_BYTES = 1024 * 1024 * 1024
MAX_TRANSPORT_BYTES = 384 * 1024 * 1024
MAX_LEDGER_BYTES = 2 * 1024 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024
MAX_HASH_DICTIONARY = 1024 * 1024
MAX_PATH_DICTIONARY = 262144
MAX_PATH_DICTIONARY_BYTES = 32 * 1024 * 1024


class StudyExchangeError(ValueError):
    pass


@dataclass(frozen=True)
class EncodedNumber:
    text: str


def _number(text: str):
    value = json.loads(text)
    if isinstance(value, int) and abs(value) > 9_007_199_254_740_991:
        return EncodedNumber(text)
    if not math.isfinite(value) or value == 0 and text.startswith("-") or abs(value) > 9_007_199_254_740_991 and float(value).is_integer() or Decimal(text) != Decimal(canonical_json(value)):
        return EncodedNumber(text)
    return value


def _pairs(entries):
    result = {}
    for key, value in entries:
        if key in result:
            raise StudyExchangeError("EDC_SOURCE_DUPLICATE_PROPERTY:" + key)
        result[key] = value
    return result


def _invalid_constant(value):
    raise StudyExchangeError("EDC_SOURCE_INVALID_NUMBER:" + value)


def _source_json(raw: bytes):
    return json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_int=_number, parse_float=_number, parse_constant=_invalid_constant)


def _strict(value, path=""):
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, (int, float)):
        if (
            isinstance(value, int)
            and abs(value) > 9_007_199_254_740_991
            or not math.isfinite(value)
            or value == 0
            and math.copysign(1, value) < 0
            or abs(value) > 9_007_199_254_740_991
            and float(value).is_integer()
        ):
            raise StudyExchangeError("EDC_NATIVE_NUMBER_UNREPRESENTABLE:" + path)
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _strict(child, f"{path}/{index}")
        return
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for key, child in value.items():
            _strict(child, path + "/" + _escape(key))
        return
    raise StudyExchangeError("EDC_NATIVE_JSON_UNREPRESENTABLE:" + path)


def _bytes(value) -> bytes:
    _strict(value)
    return canonical_json(value).encode("utf-8")


def _hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _escape(key: str) -> str:
    return key.replace("~", "~0").replace("/", "~1")


def _terminals(value, path=""):
    if isinstance(value, EncodedNumber):
        yield path, "encoded-number", {"encoding": "json-number", "text": value.text}
    elif value is None:
        yield path, "null", None
    elif isinstance(value, bool):
        yield path, "boolean", value
    elif isinstance(value, (int, float)):
        yield path, "number", value
    elif isinstance(value, str):
        yield path, "string", value
    elif isinstance(value, (dict, list)):
        if not value:
            yield path, "empty-array" if isinstance(value, list) else "empty-object", value
        elif isinstance(value, list):
            for index, child in enumerate(value):
                yield from _terminals(child, f"{path}/{index}")
        else:
            for key in sorted(value, key=lambda item: item.encode("utf-16-be", errors="surrogatepass")):
                yield from _terminals(value[key], path + "/" + _escape(key))
    else:
        raise StudyExchangeError("EDC_SOURCE_INVALID_JSON:" + path)


def _pointer(root, path: str):
    if path == "":
        return root
    if not isinstance(path, str) or not path.startswith("/"):
        raise StudyExchangeError("EDC_SOURCE_TARGET_POINTER")
    try:
        for key in path[1:].split("/"):
            key = key.replace("~1", "/").replace("~0", "~")
            if isinstance(root, list):
                if not key.isascii() or not key.isdigit() or str(int(key)) != key:
                    raise StudyExchangeError("EDC_SOURCE_TARGET_POINTER")
                root = root[int(key)]
            else:
                root = root[key]
        return root
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise StudyExchangeError("EDC_SOURCE_TARGET_MISSING:" + path) from error


def _unbase64(payload: str) -> bytes:
    raw = base64.b64decode(payload, validate=True)
    if base64.b64encode(raw).decode("ascii") != payload:
        raise StudyExchangeError("EDC_SOURCE_BASE64")
    return raw


def _gunzip(raw: bytes, limit: int) -> bytes:
    if limit < 0 or limit > MAX_SOURCE_BYTES:
        raise StudyExchangeError("EDC_SOURCE_SIZE_LIMIT")
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
            result = stream.read(limit + 1)
    except (OSError, EOFError, zlib.error) as error:
        raise StudyExchangeError("EDC_SOURCE_GZIP_INVALID") from error
    if len(result) > limit:
        raise StudyExchangeError("EDC_SOURCE_SIZE_LIMIT")
    return result


def _payloads(source):
    result = {}
    total = 0
    for entry in source.get("payloads", []):
        raw = _unbase64(entry["payload"])
        total += len(raw)
        if len(raw) != entry["byteLength"] or _hash(raw) != entry["sha256"] or entry["sha256"] in result or total > MAX_TRANSPORT_BYTES:
            raise StudyExchangeError("EDC_SOURCE_PAYLOAD_INTEGRITY")
        result[entry["sha256"]] = entry["payload"]
    return result


def artifact_bytes(artifact, payloads=None) -> bytes:
    payloads = payloads or {}
    length = artifact["byteLength"]
    if not isinstance(length, int) or isinstance(length, bool) or length < 0 or length > MAX_SOURCE_BYTES:
        raise StudyExchangeError("EDC_SOURCE_SIZE_LIMIT")
    encoding = artifact["encoding"]
    payload = payloads[artifact["payload"]] if encoding.endswith("-reference") else artifact["payload"]
    raw = _unbase64(payload)
    if encoding == "json-custody-dag":
        recipe = json.loads(_gunzip(raw, min(MAX_TRANSPORT_BYTES, length * 8 + 65536)))
        if set(recipe) != {"contract", "segments"} or recipe["contract"] != "edc-study-custody-dag/1" or len(recipe["segments"]) > 65536:
            raise StudyExchangeError("EDC_SOURCE_DAG_SHAPE")
        parts = []
        total = 0
        for segment in recipe["segments"]:
            if set(segment) == {"literal"}:
                part = segment["literal"].encode("utf-8")
            elif set(segment) == {"payloadSha256"}:
                part = ('"' + payloads[segment["payloadSha256"]] + '"').encode("ascii")
            else:
                raise StudyExchangeError("EDC_SOURCE_DAG_SEGMENT")
            total += len(part)
            if total > length:
                raise StudyExchangeError("EDC_SOURCE_DAG_LENGTH")
            parts.append(part)
        raw = b"".join(parts)
    elif encoding in {"gzip+base64", "gzip+base64-reference"}:
        raw = _gunzip(raw, length)
    elif encoding not in {"base64", "base64-reference"}:
        raise StudyExchangeError("EDC_SOURCE_ENCODING")
    if len(raw) != length or _hash(raw) != artifact["sha256"]:
        raise StudyExchangeError("EDC_SOURCE_ARTIFACT_INTEGRITY")
    return raw


def ledger_entries(source):
    ledger = source["valueLedger"]
    if isinstance(ledger, list):
        yield from deepcopy(ledger)
        return
    if ledger["encoding"] not in {"gzip-jsonl-chunks", "gzip-jsonl-prefix-chunks", "gzip-jsonl-dictionary-prefix-chunks"} or ledger["byteLength"] > MAX_LEDGER_BYTES or len(ledger["chunks"]) > 8192:
        raise StudyExchangeError("EDC_SOURCE_LEDGER_LIMIT")
    dictionary_encoding = ledger["encoding"] == "gzip-jsonl-dictionary-prefix-chunks"
    prefix_encoding = dictionary_encoding or ledger["encoding"] == "gzip-jsonl-prefix-chunks"
    hashes = []
    path_dictionary = []
    path_dictionary_bytes = 0
    payloads = _payloads(source)
    digest = hashlib.sha256()
    size = count = 0
    for chunk in ledger["chunks"]:
        if not 0 < chunk["byteLength"] <= CHUNK_BYTES:
            raise StudyExchangeError("EDC_SOURCE_LEDGER_CHUNK_LIMIT")
        payload = payloads[chunk["payload"]] if chunk.get("encoding") == "sha256-reference" else chunk["payload"]
        raw = _gunzip(_unbase64(payload), chunk["byteLength"])
        size += len(raw)
        if len(raw) != chunk["byteLength"] or not raw.endswith(b"\n") or size > ledger["byteLength"]:
            raise StudyExchangeError("EDC_SOURCE_LEDGER_LENGTH")
        digest.update(raw)
        previous_path = []
        if prefix_encoding and (not isinstance(chunk.get("sourceArtifactId"), str) or not chunk["sourceArtifactId"]):
            raise StudyExchangeError("EDC_SOURCE_LEDGER_BINDING")
        for line in raw.splitlines():
            entry = json.loads(line)
            count += 1
            if count > ledger["entryCount"]:
                raise StudyExchangeError("EDC_SOURCE_LEDGER_INTEGRITY")
            if prefix_encoding:
                if (not isinstance(entry, list) or len(entry) not in {6, 7}
                    or type(entry[0]) is not int or not 0 <= entry[0] <= len(previous_path)
                    or not isinstance(entry[1], list) or (not dictionary_encoding and any(not isinstance(part, str) for part in entry[1]))
                    or (len(entry) == 7 and (not isinstance(entry[6], dict)
                        or set(entry[6]) & {"sourceArtifactId", "sourcePointer", "type", "valueSha256", "disposition", "targetPointers"}))):
                    raise StudyExchangeError("EDC_SOURCE_LEDGER_PREFIX")
                suffix = []
                for part in entry[1]:
                    if isinstance(part, str):
                        length = len(part.encode("utf-8"))
                        if dictionary_encoding and len(path_dictionary) < MAX_PATH_DICTIONARY and path_dictionary_bytes + length <= MAX_PATH_DICTIONARY_BYTES:
                            path_dictionary.append(part)
                            path_dictionary_bytes += length
                        suffix.append(part)
                    elif dictionary_encoding and type(part) is int and 0 <= part < len(path_dictionary):
                        suffix.append(path_dictionary[part])
                    else:
                        raise StudyExchangeError("EDC_SOURCE_LEDGER_PATH_DICTIONARY")
                previous_path = previous_path[:entry[0]] + suffix
                pointer = "/".join(previous_path)
                if not previous_path or (pointer and not pointer.startswith("/")):
                    raise StudyExchangeError("EDC_SOURCE_LEDGER_PREFIX")
                if dictionary_encoding:
                    token = entry[3]
                    if isinstance(token, str) and len(token) == 71 and token.startswith("sha256:") and all(char in "0123456789abcdef" for char in token[7:]):
                        if len(hashes) < MAX_HASH_DICTIONARY:
                            hashes.append(token)
                    elif type(token) is int and 0 <= token < len(hashes):
                        entry[3] = hashes[token]
                    else:
                        raise StudyExchangeError("EDC_SOURCE_LEDGER_HASH_DICTIONARY")
                entry = {**(entry[6] if len(entry) == 7 else {}), "sourceArtifactId": chunk["sourceArtifactId"],
                         "sourcePointer": pointer, "type": entry[2], "valueSha256": entry[3],
                         "disposition": entry[4], "targetPointers": entry[5]}
            elif "sourceArtifactId" in chunk:
                if entry["sourceArtifactId"] != "":
                    raise StudyExchangeError("EDC_SOURCE_LEDGER_BINDING")
                entry["sourceArtifactId"] = chunk["sourceArtifactId"]
            yield entry
    if size != ledger["byteLength"] or count != ledger["entryCount"] or digest.hexdigest() != ledger["sha256"]:
        raise StudyExchangeError("EDC_SOURCE_LEDGER_INTEGRITY")


def _encode_ledger(entries: Iterable[dict]):
    chunks = []
    parts = []
    size = total = count = 0
    digest = hashlib.sha256()
    previous_path = []
    previous_source = None
    hashes = {}
    path_dictionary = {}
    path_dictionary_bytes = 0

    def flush():
        nonlocal parts, size, previous_path
        if not size:
            return
        chunks.append({"byteLength": size, "payload": base64.b64encode(gzip.compress(b"".join(parts), mtime=0)).decode("ascii"),
                       "sourceArtifactId": previous_source})
        parts, size, previous_path = [], 0, []
        if len(chunks) > 8192:
            raise StudyExchangeError("EDC_SOURCE_LEDGER_CHUNK_LIMIT")

    for entry in entries:
        if not isinstance(entry.get("sourceArtifactId"), str) or not entry["sourceArtifactId"] or not isinstance(entry.get("sourcePointer"), str):
            raise StudyExchangeError("EDC_SOURCE_LEDGER_ENTRY")
        if previous_source != entry["sourceArtifactId"]:
            flush()
            previous_source = entry["sourceArtifactId"]
        path = entry["sourcePointer"].split("/")
        path_tokens = []
        for part in path:
            if part in path_dictionary:
                path_tokens.append(path_dictionary[part])
            else:
                path_tokens.append(part)
                length = len(part.encode("utf-8"))
                if len(path_dictionary) < MAX_PATH_DICTIONARY and path_dictionary_bytes + length <= MAX_PATH_DICTIONARY_BYTES:
                    path_dictionary[part] = len(path_dictionary)
                    path_dictionary_bytes += length
        extra = {key: value for key, value in entry.items() if key not in
                 {"sourceArtifactId", "sourcePointer", "type", "valueSha256", "disposition", "targetPointers"}}
        value_hash = entry["valueSha256"]
        if not isinstance(value_hash, str) or len(value_hash) != 71 or not value_hash.startswith("sha256:") or any(char not in "0123456789abcdef" for char in value_hash[7:]):
            raise StudyExchangeError("EDC_SOURCE_LEDGER_VALUE_HASH")
        hash_token = hashes.get(value_hash, value_hash)
        if value_hash not in hashes and len(hashes) < MAX_HASH_DICTIONARY:
            hashes[value_hash] = len(hashes)

        def encode():
            prefix = 0
            while prefix < len(path) and prefix < len(previous_path) and path[prefix] == previous_path[prefix]:
                prefix += 1
            row = [prefix, path_tokens[prefix:], entry["type"], hash_token, entry["disposition"], entry["targetPointers"]]
            if extra:
                row.append(extra)
            return _bytes(row) + b"\n"

        raw = encode()
        if size + len(raw) > CHUNK_BYTES:
            flush()
            raw = encode()
        if len(raw) > CHUNK_BYTES:
            raise StudyExchangeError("EDC_SOURCE_LEDGER_ENTRY_LIMIT")
        parts.append(raw)
        size += len(raw)
        total += len(raw)
        count += 1
        digest.update(raw)
        previous_path = path
        if total > MAX_LEDGER_BYTES:
            raise StudyExchangeError("EDC_SOURCE_LEDGER_LIMIT")
    flush()
    return {"encoding": "gzip-jsonl-dictionary-prefix-chunks", "sha256": digest.hexdigest(), "byteLength": total, "entryCount": count, "chunks": chunks}


def verify_source_exchange(bundle):
    _strict(bundle)
    if (
        bundle.get("formatVersion") != "2.0"
        or bundle.get("profile", {}).get("id") != "edc-study-exchange/2"
        or bundle["profile"].get("modelVersion") != "4.0.0"
        or bundle["profile"].get("mode") not in {"draft", "release"}
    ):
        raise StudyExchangeError("EDC_SOURCE_EXCHANGE_PROFILE")
    if set(bundle) - {"formatVersion", "profile", "exportedAt", "exportedBy", "sourceStudyName", "definition", "execution", "source", "extensions"}:
        raise StudyExchangeError("EDC_SOURCE_EXCHANGE_SCOPE")
    if bundle["definition"].get("contract") != "edc-study-definition/1" or bundle["definition"].get("modelVersion") != "4.0.0":
        raise StudyExchangeError("EDC_SOURCE_DEFINITION_CONTRACT")
    if set(bundle["execution"]) - {"forms", "visits", "visitFormAssignments", "externalForms", "studyTasks", "studyGroupClasses", "sites", "deviationSpec", "extensions"}:
        raise StudyExchangeError("EDC_SOURCE_EXECUTION_SCOPE")
    source = bundle["source"]
    payloads = _payloads(source)
    ledger = iter(ledger_entries(source))
    artifact_ids = set()
    total = 0
    for artifact in source["artifacts"]:
        if artifact["artifactId"] in artifact_ids:
            raise StudyExchangeError("EDC_SOURCE_ARTIFACT_DUPLICATE")
        artifact_ids.add(artifact["artifactId"])
        total += artifact["byteLength"]
        if total > MAX_SOURCE_BYTES:
            raise StudyExchangeError("EDC_SOURCE_SIZE_LIMIT")
        raw = artifact_bytes(artifact, payloads)
        if "json" not in artifact["mediaType"].lower():
            continue
        for path, kind, value in _terminals(_source_json(raw)):
            entry = next(ledger, None)
            if not entry or (entry["sourceArtifactId"], entry["sourcePointer"], entry["type"], entry["valueSha256"]) != (artifact["artifactId"], path, kind, "sha256:" + _hash(_bytes(value))):
                raise StudyExchangeError("EDC_SOURCE_LEDGER_VALUE:" + path)
            if entry["disposition"] in {"canonical", "execution"}:
                if not entry["targetPointers"] or any(_bytes(_pointer(bundle, target)) != _bytes(value) for target in entry["targetPointers"]):
                    raise StudyExchangeError("EDC_SOURCE_MAPPED_VALUE:" + path)
            elif entry["disposition"] == "normalized":
                if not entry["targetPointers"] or not entry.get("evidenceRef"):
                    raise StudyExchangeError("EDC_SOURCE_NORMALIZATION:" + path)
                for target in entry["targetPointers"]:
                    matching = [
                        row
                        for row in source["normalizations"]
                        if row["sourceArtifactId"] == artifact["artifactId"] and row["sourcePointer"] == path and row["targetPointer"] == target and row["evidenceRef"] == entry.get("evidenceRef")
                    ]
                    if len(matching) != 1 or _bytes(matching[0]["sourceValue"]) != _bytes(value) or _bytes(matching[0]["targetValue"]) != _bytes(_pointer(bundle, target)):
                        raise StudyExchangeError("EDC_SOURCE_NORMALIZATION:" + path)
            elif entry["disposition"] not in {"retained-source", "unresolved"}:
                raise StudyExchangeError("EDC_SOURCE_LEDGER_DISPOSITION")
    if next(ledger, None) is not None:
        raise StudyExchangeError("EDC_SOURCE_LEDGER_EXTRA")
    for definition in [bundle["definition"], *(site["definition"] for site in bundle["execution"].get("sites", []) if "definition" in site)]:
        for reference in definition["sourceArtifacts"]:
            matches = [artifact for artifact in source["artifacts"] if artifact["artifactId"] == reference["artifactId"]]
            if len(matches) != 1 or any(matches[0].get(key) != value for key, value in reference.items()):
                raise StudyExchangeError("EDC_SOURCE_DEFINITION_REFERENCE")


def source_execution(bundle):
    """Private historical snapshot reader; never constructs a flat output."""
    return bundle["execution"] if bundle.get("formatVersion") == "2.0" else bundle


def _native_execution_capacity(execution):
    for form in execution["forms"]["forms"]:
        for field in form.get("fields", []):
            for key, limit in (("unit", 64), ("validationPattern", 1000)):
                value = field.get(key)
                if isinstance(value, str) and len(value) > limit:
                    raise StudyExchangeError(f"EDC_NATIVE_COLUMN_UNREPRESENTABLE:{form.get('refKey')}/{field.get('refKey')}/{key}")


def _artifact(value, system, kind="source-evidence"):
    raw = _bytes(value)
    digest = _hash(raw)
    identity = _hash((system + ":" + digest).encode("utf-8"))
    return {
        "artifactId": str(uuid.UUID(identity[:32], version=8)),
        "kind": kind,
        "sha256": digest,
        "byteLength": len(raw),
        "mediaType": "application/json",
        "sourceSystem": system,
        "encoding": "gzip+base64",
        "payload": base64.b64encode(gzip.compress(raw, mtime=0)).decode("ascii"),
    }


def build_study_exchange(*, study_uid: str, document, execution, native, source_bundle, exported_at: str, disclosure, census, mapping_report=None):
    """Current source is authoritative; native observations remain review data."""
    original = deepcopy(source_bundle)
    inherited = original.get("formatVersion") == "2.0"
    if inherited:
        verify_source_exchange(original)
        result = deepcopy(original)
        prior_entries = ledger_entries(original["source"])
    else:
        if not isinstance(document, dict) or document.get("usdmVersion") != "4.0.0" or not isinstance(document.get("study"), dict):
            raise StudyExchangeError("OSB_USDM_SERVICE_DOCUMENT_REQUIRED")
        _strict(document)
        _native_execution_capacity(execution)
        versions = document["study"].get("versions", [])
        version = versions[0] if len(versions) == 1 else None
        designs = version.get("studyDesigns", []) if version else []
        definition = {
            "contract": "edc-study-definition/1",
            "modelVersion": "4.0.0",
            "document": deepcopy(document),
            "selection": {"versionId": version.get("id") if version else None, "designId": designs[0].get("id") if len(designs) == 1 else None},
            "execution": {
                "identification": {"nativeIdentifier": study_uid},
                "enrollment": {"parameters": {}},
                "facility": {},
                "monitoring": {},
                "notifications": {},
                "milestones": {},
                "extensions": {},
            },
            "sourceArtifacts": [],
            "entityBindings": [],
            "dispositions": [],
            "retainedValues": [],
        }
        result = {
            "formatVersion": "2.0",
            "profile": {"id": "edc-study-exchange/2", "mode": "draft", "modelVersion": "4.0.0"},
            "exportedAt": exported_at,
            "exportedBy": "openstudybuilder-edc-export",
            "definition": definition,
            "execution": deepcopy(execution),
            "source": {"artifacts": [], "normalizations": [], "valueLedger": []},
            "extensions": {},
        }
        if original:
            for key, value in original.items():
                if key not in {"formatVersion", "exportedAt", "exportedBy", "sourceStudyName", "study", "forms", "visits", "visitFormAssignments", "studyGroupClasses", "studyTasks", "_deviationSpec"}:
                    result["extensions"][key] = deepcopy(value)
        prior_entries = []
    observations = {"observedAt": exported_at, "native": deepcopy(native), "proposedExecution": deepcopy(execution), "mappingAuthority": deepcopy(disclosure), "census": deepcopy(census)}
    if mapping_report is not None:
        if (
            not isinstance(mapping_report, dict)
            or mapping_report.get("studyUid") != study_uid
            or mapping_report.get("state") not in {"complete", "incomplete"}
            or not isinstance(mapping_report.get("issues"), list)
        ):
            raise StudyExchangeError("OSB_USDM_MAPPING_REPORT_INVALID")
        observations["mappingReport"] = deepcopy(mapping_report)
    additions = [(observations, "openstudybuilder.edc-native-observations", "source-evidence", None)]
    if not inherited:
        additions.append((document, "openstudybuilder.usdm-service", "usdm-document", "/definition/document"))
    if original:
        additions.append((original, "openstudybuilder.edc-source-snapshot", "study-bundle", None))
    entries = []
    for value, system, kind, target_root in additions:
        artifact = _artifact(value, system, kind)
        if any(row["artifactId"] == artifact["artifactId"] for row in result["source"]["artifacts"]):
            continue
        result["source"]["artifacts"].append(artifact)
        if not inherited:
            result["definition"]["sourceArtifacts"].append({key: value for key, value in artifact.items() if key not in {"payload", "encoding"}})
        for path, value_type, terminal in _terminals(value):
            target = target_root + path if target_root else None
            entries.append(
                {
                    "sourceArtifactId": artifact["artifactId"],
                    "sourcePointer": path,
                    "type": value_type,
                    "valueSha256": "sha256:" + _hash(_bytes(terminal)),
                    "disposition": "canonical" if target else "retained-source",
                    "targetPointers": [target] if target else [],
                }
            )
    # A source release profile remains available in the exact source snapshot.
    # It cannot turn native preview observations into an OSB release.
    result["profile"]["mode"] = "draft"
    previous = result["extensions"].get("_osbExport")
    result["extensions"]["_osbExport"] = {**observations, "sourceProfile": deepcopy(original.get("profile")), **({"previous": previous} if "_osbExport" in result["extensions"] else {})}

    def target_changed(target):
        original_value = _pointer(original, target)
        try:
            current_value = _pointer(result, target)
        except StudyExchangeError:
            return True
        return _bytes(original_value) != _bytes(current_value)

    def retained_prior_entries():
        for entry in prior_entries:
            # The source profile/report may be superseded by this preview.
            # Its exact old value and ledger remain in the archived input;
            # never leave an old mapping falsely pointing at the new report.
            if entry["disposition"] in {"canonical", "execution", "normalized"}:
                changed = any(target_changed(target) for target in entry["targetPointers"])
                if changed:
                    entry = {**entry, "disposition": "retained-source", "targetPointers": []}
            yield entry

    result["source"]["valueLedger"] = _encode_ledger(chain(retained_prior_entries(), entries))
    _strict(result)
    if len(_bytes(result)) > MAX_TRANSPORT_BYTES:
        raise StudyExchangeError("EDC_EXCHANGE_TRANSPORT_LIMIT")
    verify_source_exchange(result)
    return result
