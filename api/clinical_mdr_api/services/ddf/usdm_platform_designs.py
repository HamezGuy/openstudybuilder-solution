"""Descriptive multi-design USDM for one platform study version (decision DD-4).

One document holds the master design and one design for every native subpart
pinned to the selected master version. Each subpart is mapped by the existing
single-design mapper with identifiers scoped by its native study UID, so no
mapping code is duplicated and no identifier is rewritten after the fact. Only
the subpart's design and the study version resources it carries are merged.

A draft composition is descriptive and selects nothing. For a locked platform
version every design is pinned to an exact study value version, and the export
selects all of them for one executable release (decision DD-4) in the shape of
the CSL C11 design set (`csl-platform-release-designs/1`, CSL 291e9ab): the
master keyed `master` and each subpart keyed `subpart:<study uid>`, each with
its arms as OSB arm uid, OSB branch arm uid and USDM StudyArm id. CSL reads the
same facts from the document itself and signs the set into the release; this
selection states what OSB releases, and why it selects nothing when it cannot.
Mapping completeness of each design is reported beside it, never hidden.
"""

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import quote
from uuid import NAMESPACE_URL, uuid5

from clinical_mdr_api.services.ddf.usdm_mapping_context import (
    MappingContext,
    finalize_document,
    native_json,
    source_extension,
)
from clinical_mdr_api.services.ddf.usdm_utils import (
    UUID_ENTITY_CLASSES,
    UUID_ID_NAMESPACE,
    IdManager,
)

DesignLevel = Literal["master", "subpart"]
DESIGN_SCOPE_KIND = "designScope"
EXECUTABLE_RELEASE_CONTRACT = "csl-platform-release-designs/1"
NATIVE_EXTENSION = "https://openstudybuilder.org/usdm/extensions/native/"
NATIVE_VALUE_SHAPE = "https://openstudybuilder.org/usdm/extensions/native-value-shape"
DRAFT_NOT_EXECUTABLE = (
    "A draft platform version is descriptive and selects no executable design; "
    "lock the master study to pin every design."
)
# StudyVersion fields that identify the master version itself. A subpart's
# values for these fields describe the subpart study, not the platform version.
VERSION_IDENTITY_FIELDS = frozenset(
    {
        "id",
        "instanceType",
        "versionIdentifier",
        "rationale",
        "extensionAttributes",
        "titles",
        "studyIdentifiers",
        "referenceIdentifiers",
        "dateValues",
        "amendments",
        "businessTherapeuticAreas",
        "notes",
        "studyDesigns",
    }
)


class DesignScopedIdManager(IdManager):
    """Prefix every identifier of one mapping run with its design scope key."""

    def __init__(self, scope_key: str):
        if not isinstance(scope_key, str) or not scope_key.strip() or ":" in scope_key:
            raise ValueError("A design scope key must be a nonblank native UID")
        super().__init__()
        self.scope_key = scope_key

    def get_id(self, entity_class: str, original_sb_id: str | None = None) -> str:
        base = super().get_id(entity_class, original_sb_id)
        if entity_class in UUID_ENTITY_CLASSES:
            # These classes require UUIDs. Derive a distinct, stable UUID per scope.
            return str(
                uuid5(
                    NAMESPACE_URL, f"{UUID_ID_NAMESPACE}/scope/{self.scope_key}/{base}"
                )
            )
        return f"{self.scope_key}:{base}"


@dataclass(frozen=True)
class PlatformDesignSource:
    """One exact native study version and its single-design mapping report."""

    level: DesignLevel
    study_uid: str
    study_value_version: str | None
    report: dict[str, Any]
    parent_study_uid: str | None = None
    subpart_acronym: str | None = None

    def scope(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "studyUid": self.study_uid,
            "studyValueVersion": self.study_value_version,
            "parentStudyUid": self.parent_study_uid,
            "subpartAcronym": self.subpart_acronym,
        }


def _single_design(source: PlatformDesignSource) -> tuple[dict, dict, dict | None]:
    """One version and at most one design; a study without design content has none."""
    document = deepcopy(source.report["document"])
    study = document["study"]
    versions = study.get("versions") or []
    if len(versions) != 1:
        raise ValueError(
            f"Native study {source.study_uid} must map to exactly one study version"
        )
    designs = versions[0].get("studyDesigns") or []
    if len(designs) > 1:
        raise ValueError(
            f"Native study {source.study_uid} must map to at most one study design"
        )
    return document, versions[0], designs[0] if designs else None


def _absent(context: MappingContext, source: PlatformDesignSource) -> None:
    context.unresolved(
        "USDM_PLATFORM_DESIGN_ABSENT",
        f"study/{source.study_uid}",
        "study/versions/0/studyDesigns",
        f"The {source.level} study version has no design content to export.",
        "Author the design (arms, epochs) of this study version; nothing was inferred.",
    )


def _distinct_name(item: dict, target: list) -> dict:
    """Disambiguate an added resource's name, never one already in the document.

    The convention is the one finalize_document applies within one design, so
    the master design and its resources keep their single-design bytes.
    """
    copied = deepcopy(item)
    names = {
        (existing.get("instanceType"), existing.get("name"))
        for existing in target
        if isinstance(existing, dict)
    }
    if (
        isinstance(copied.get("name"), str)
        and (
            copied.get("instanceType"),
            copied["name"],
        )
        in names
    ):
        if copied.get("label") is None:
            copied["label"] = copied["name"]
        copied["name"] = f'{copied["name"]} [{copied["id"]}]'
    return copied


def _merge(target: list, items: list, context: MappingContext, path: str) -> None:
    """Append resources by identity; an identical resource is kept once."""
    by_id = {
        item["id"]: item
        for item in target
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    for item in items:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            existing = by_id.get(item["id"])
            if existing is None:
                copied = _distinct_name(item, target)
                target.append(copied)
                by_id[item["id"]] = copied
            elif existing != item:
                context.unresolved(
                    "USDM_PLATFORM_RESOURCE_ID_COLLISION",
                    path + "/" + item["id"],
                    path,
                    "Two designs carry different resources with the same identifier.",
                    "Resolve the authored identifier; neither resource was dropped from its own design report.",
                )
        elif item not in target:
            target.append(deepcopy(item))


def _native(target: dict, kind: str) -> dict | None:
    """The one native source extension of `kind` on a USDM object, if any."""
    url = NATIVE_EXTENSION + quote(kind, safe="")
    found = [
        item
        for item in target.get("extensionAttributes") or []
        if isinstance(item, dict) and item.get("url") == url
    ]
    if len(found) > 1:
        raise ValueError(f"Two native {kind} extensions on {target.get('id')}")
    return found[0] if found else None


def _native_text(extension: dict | None, *path: str) -> str | None:
    """A text member of a native extension tree, by its field path."""
    current = extension
    for name in path:
        if current is None:
            return None
        url = current["url"] + "/" + quote(name, safe="")
        current = next(
            (
                item
                for item in current.get("extensionAttributes") or []
                if isinstance(item, dict) and item.get("url") == url
            ),
            None,
        )
    value = None if current is None else current.get("valueString")
    return value if isinstance(value, str) and value else None


def _design_arms(design: dict) -> list[dict[str, Any]] | None:
    """Each USDM arm as its native identity; None when one cannot be named."""
    arms = []
    for arm in design.get("arms") or []:
        native, branch = _native(arm, "studyArm"), _native(arm, "studyBranchArm")
        if native is not None and branch is None:
            arm_uid, branch_uid = _native_text(native, "arm_uid"), None
        elif branch is not None and native is None:
            arm_uid = _native_text(branch, "arm_root", "arm_uid")
            branch_uid = _native_text(branch, "branch_arm_uid")
            if branch_uid is None:
                return None
        else:
            return None
        if arm_uid is None:
            return None
        arms.append(
            {
                "osbArmUid": arm_uid,
                "osbBranchArmUid": branch_uid,
                "usdmArmId": arm["id"],
            }
        )
    return arms


def _release_scope(source: PlatformDesignSource) -> dict[str, Any]:
    scope = {
        "level": source.level,
        "studyUid": source.study_uid,
        "studyValueVersion": source.study_value_version,
    }
    if source.level == "subpart":
        scope["parentStudyUid"] = source.parent_study_uid
    return scope


def executable_release(
    sources: list[PlatformDesignSource],
    design_ids: dict[str, str | None],
    document: dict[str, Any],
    issues: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Select every design for one release, or state exactly why none is selected."""
    reasons = []
    if any(source.study_value_version is None for source in sources):
        reasons.append(DRAFT_NOT_EXECUTABLE)
    if any(design_ids[source.study_uid] is None for source in sources):
        reasons.append(
            "A design scope has no design content (USDM_PLATFORM_DESIGN_ABSENT)."
        )
    if any(
        issue.get("code") == "USDM_PLATFORM_RESOURCE_ID_COLLISION" for issue in issues
    ):
        reasons.append(
            "Two designs carry different resources with one identifier "
            "(USDM_PLATFORM_RESOURCE_ID_COLLISION)."
        )
    designs = {
        design["id"]: design
        for design in document["study"]["versions"][0].get("studyDesigns") or []
    }
    selected = []
    owners: dict[str, str] = {}
    for source in sources:
        design = designs.get(design_ids[source.study_uid] or "")
        if design is None:
            continue
        arms = _design_arms(design)
        if arms is None:
            reasons.append(
                f"A StudyArm of {source.study_uid} names no native arm or branch arm."
            )
            continue
        key = "master" if source.level == "master" else f"subpart:{source.study_uid}"
        for arm in arms:
            if owners.setdefault(arm["osbArmUid"], key) != key:
                reasons.append(
                    f"Native arm {arm['osbArmUid']} sits in two designs; one arm "
                    "belongs to exactly one design."
                )
        selected.append(
            {
                "designKey": key,
                "designScope": _release_scope(source),
                "usdmDesignId": design["id"],
                "mappingState": source.report.get("mappingReport", {}).get("state"),
                "arms": arms,
            }
        )
    if reasons:
        return {"selected": False, "reason": " ".join(dict.fromkeys(reasons))}
    return {
        "selected": True,
        "contract": EXECUTABLE_RELEASE_CONTRACT,
        "masterLineageId": sources[0].study_uid,
        "designs": selected,
    }


def compose_platform_document(
    master: PlatformDesignSource, subparts: list[PlatformDesignSource]
) -> dict[str, Any]:
    """Merge single-design reports into one descriptive platform document."""
    if master.level != "master" or any(item.level != "subpart" for item in subparts):
        raise ValueError("A platform has one master design and only subpart designs")
    keys = [item.study_uid for item in subparts]
    if len(set(keys)) != len(keys) or master.study_uid in keys:
        raise ValueError("Each design scope must be a distinct native study")
    document, version, master_design = _single_design(master)
    context = MappingContext(allow_incomplete=True)
    identifiers = DesignScopedIdManager("platform")

    def scope_extension(source: PlatformDesignSource) -> dict[str, Any]:
        return native_json(
            source_extension(
                identifiers, DESIGN_SCOPE_KIND, source.study_uid, source.scope()
            )
        )

    version["studyDesigns"] = version.get("studyDesigns") or []
    design_ids: dict[str, str | None] = {master.study_uid: None}
    if master_design is None:
        _absent(context, master)
    else:
        master_design.setdefault("extensionAttributes", []).append(
            scope_extension(master)
        )
        design_ids[master.study_uid] = master_design["id"]
    for source in subparts:
        sub_document, sub_version, design = _single_design(source)
        design_ids[source.study_uid] = None
        if design is None:
            _absent(context, source)
        else:
            design.setdefault("extensionAttributes", []).append(scope_extension(source))
            design = _distinct_name(design, version["studyDesigns"])
            version["studyDesigns"].append(design)
            design_ids[source.study_uid] = design["id"]
        for field, items in sub_version.items():
            if field in VERSION_IDENTITY_FIELDS or not isinstance(items, list):
                continue
            _merge(
                version.setdefault(field, []),
                items,
                context,
                f"study/versions/0/{field}",
            )
        documents = sub_document["study"].get("documentedBy")
        if isinstance(documents, list):
            _merge(
                document["study"].setdefault("documentedBy", []),
                documents,
                context,
                "study/documentedBy",
            )
    # Cross-design identity and name uniqueness over the whole final document.
    finalize_document(document, context)
    reports = [master, *subparts]
    states = [item.report.get("mappingReport", {}).get("state") for item in reports]
    return {
        "document": document,
        "designScopes": [
            {"designId": design_ids[source.study_uid], **source.scope()}
            for source in reports
        ],
        "mappingReport": {
            "state": (
                "complete"
                if all(state == "complete" for state in states) and not context.issues
                else "incomplete"
            ),
            "studyUid": master.study_uid,
            "studyValueVersion": master.study_value_version,
            "issues": context.issues,
            "designs": [
                {
                    "studyUid": source.study_uid,
                    "studyValueVersion": source.study_value_version,
                    "state": source.report.get("mappingReport", {}).get("state"),
                    "issues": source.report.get("mappingReport", {}).get("issues", []),
                }
                for source in reports
            ],
        },
        "executableRelease": executable_release(
            reports, design_ids, document, context.issues
        ),
    }
