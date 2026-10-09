"""Synthetic master/three-arm authoring through native mapping and real HTTP M11."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from lxml import html
from pydantic import ValidationError

from clinical_mdr_api.domain_repositories.study_selections import (
    study_definition_document_repository as repository_module,
)
from clinical_mdr_api.models.study_selections.study_definition_document import (
    AuthoredProtocolDocuments,
)
from clinical_mdr_api.routers.studies import studies as study_routes
from clinical_mdr_api.routers.studies.study_access import enforce_visible_study
from clinical_mdr_api.services.studies import (
    study_definition_document as service_module,
)
from clinical_mdr_api.services.studies.study_definition_document import (
    assess_authored_documents,
    required_m11_sections,
)
from clinical_mdr_api.tests.fixtures.usdm_m11_source import (
    m11_source_graph,
    m11_source_report,
)
from clinical_mdr_api.tests.fixtures.usdm_native_study import STUDY_UID, VERSION
from clinical_mdr_api.tests.unit.services.test_m11_preview_http import client
from common.auth import rbac
from common.auth.dependencies import security
from common.exception_handlers import register_exception_handlers
from common.exceptions import AlreadyExistsException


def authored_source():
    graph = m11_source_graph()
    arms = [item.arm_uid for item in graph["arms"]]
    assert len(arms) >= 3
    documents, items, bindings, dispositions = [], [], [], []
    ids = ["authored-master", *[f"authored-arm-{index}" for index in range(3)]]

    def code(identifier, value, decode):
        return {
            "id": identifier,
            "code": value,
            "codeSystem": "CDISC",
            "codeSystemVersion": "2025-09-26",
            "decode": decode,
        }

    for index, identity in enumerate(ids):
        contents = []
        for number, title in required_m11_sections().items():
            section_id = f"{identity}-section-{number}"
            item_id = section_id + "-text"
            contents.append(
                {
                    "id": section_id,
                    "name": section_id,
                    "sectionNumber": number,
                    "sectionTitle": title,
                    "displaySectionNumber": True,
                    "displaySectionTitle": True,
                    "contentItemId": item_id,
                }
            )
            items.append(
                {
                    "id": item_id,
                    "name": item_id,
                    "text": f"<p>Synthetic qualification only: <b>{identity}</b> section {number}; test quantity 0 mg/day; requires substantive clinical authorship before use.</p>",
                }
            )
            dispositions.append({"content_id": section_id, "state": "authored"})
        documents.append(
            {
                "id": identity,
                "name": f"Synthetic {'master' if index == 0 else 'arm ' + str(index)} protocol",
                "language": code(identity + "-language", "en", "English"),
                "type": code(identity + "-type", "C70817", "Protocol"),
                "templateName": "ICH M11 2025-11-19",
                "childIds": ids[1:] if index == 0 else [],
                "versions": [
                    {
                        "id": identity + "-version-1",
                        "version": "1.0",
                        "status": code(identity + "-status", "C85255", "Draft"),
                        "contents": contents,
                    }
                ],
            }
        )
        bindings.append(
            {
                "document_id": identity,
                "role": "master-protocol" if index == 0 else "arm-appendix",
                "arm_uids": arms if index == 0 else [arms[index - 1]],
                "master_document_id": None if index == 0 else ids[0],
                "sponsor_organization_id": "authored-sponsor",
            }
        )
    content = AuthoredProtocolDocuments.model_validate(
        {
            "documents": documents,
            "narrative_content_items": items,
            "bindings": bindings,
            "section_dispositions": dispositions,
            "synthetic": True,
            "organizations": [
                {
                    "id": "authored-sponsor",
                    "name": "Synthetic qualification sponsor",
                    "type": code(
                        "authored-sponsor-type", "C54149", "Pharmaceutical Company"
                    ),
                    "identifierScheme": "synthetic-test",
                    "identifier": "SYNTHETIC-ONLY",
                    "legalAddress": {
                        "id": "authored-address",
                        "text": "Synthetic address; not a real sponsor",
                    },
                }
            ],
        }
    )
    return graph, content


def mapped(content=None):
    graph, initial = authored_source()
    content = content or initial
    graph["header"].authored_documents = content.model_dump(mode="json")
    graph["header"].authored_documents_hash = content.content_hash()
    return m11_source_report(graph)


def render(monkeypatch, report, document_id="authored-arm-1", **params):
    api, *_ = client(monkeypatch, report)
    return api.get(
        f"/usdm/v4/studyDefinitions/{STUDY_UID}/m11",
        params={"study_value_version": VERSION, "document_id": document_id, **params},
    )


def test_native_mapper_retains_exact_master_three_arms_items_and_document_references():
    _, content = authored_source()
    study = mapped(content)["document"]["study"]
    assert study["documentedBy"] == content.model_dump(mode="json")["documents"]
    assert (
        study["versions"][0]["narrativeContentItems"]
        == content.model_dump(mode="json")["narrative_content_items"]
    )
    assert study["versions"][0]["documentVersionIds"] == [
        item.versions[0].id for item in content.documents
    ]
    assert assess_authored_documents(content)["section_complete"] is True


def test_real_http_selected_arm_has_all_authored_sections_and_no_other_arm_body(
    monkeypatch,
):
    response = render(
        monkeypatch, mapped(), download=True, require_section_complete=True
    )
    assert response.status_code == 200, response.text
    page = html.fromstring(response.text)
    authored = page.get_element_by_id("m11-authored-document")
    assert authored.get("data-document-id") == "authored-arm-1"
    assert len(authored.xpath("./section")) == len(required_m11_sections())
    assert "authored-arm-1" in authored.text_content()
    assert "authored-arm-2 section" not in authored.text_content()
    assert "0 mg/day" in authored.text_content()
    assert "Synthetic qualification content" in authored.text_content()
    assert "M11_SPONSOR_SOURCE_UNAVAILABLE" not in response.text
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["x-authored-content-sha256"]


def test_authored_export_does_not_require_unused_generated_visuals(monkeypatch):
    api, _, flowchart, figure = client(monkeypatch, mapped())
    flowchart.side_effect = RuntimeError("No generated SoA exists")
    figure.side_effect = RuntimeError("No generated figure exists")
    response = api.get(
        f"/usdm/v4/studyDefinitions/{STUDY_UID}/m11",
        params={"study_value_version": VERSION, "document_id": "authored-master"},
    )
    assert response.status_code == 200, response.text
    assert (
        "authored-master"
        in html.fromstring(response.text)
        .get_element_by_id("m11-authored-document")
        .text_content()
    )
    flowchart.assert_not_called()
    figure.assert_not_called()


def test_selected_document_removed_from_empty_bundle_cannot_fall_back(monkeypatch):
    empty = AuthoredProtocolDocuments(synthetic=True)
    api, _, flowchart, figure = client(monkeypatch, mapped(empty))
    flowchart.side_effect = RuntimeError("Do not fall back to generated SoA")
    figure.side_effect = RuntimeError("Do not fall back to generated figure")
    response = api.get(
        f"/usdm/v4/studyDefinitions/{STUDY_UID}/m11",
        params={"study_value_version": VERSION, "document_id": "removed-document"},
    )
    assert response.status_code == 422, response.text
    flowchart.assert_not_called()
    figure.assert_not_called()


def test_missing_content_blocks_section_complete_export_but_draft_remains_visible(
    monkeypatch,
):
    _, content = authored_source()
    content.section_dispositions.pop()
    report = mapped(content)
    response = render(
        monkeypatch, report, "authored-arm-2", require_section_complete=True
    )
    assert response.status_code == 422
    draft = render(monkeypatch, report, "authored-arm-2")
    assert draft.status_code == 200
    assert "Author disposition unresolved" in draft.text


def test_not_applicable_reason_is_explicit_and_sanitized_authored_html_preserves_bold(
    monkeypatch,
):
    _, content = authored_source()
    section = content.documents[2].versions[0].contents[-1]
    old = section.contentItemId
    section.contentItemId = None
    content.narrative_content_items = [
        item for item in content.narrative_content_items if item.id != old
    ]
    disposition = next(
        item for item in content.section_dispositions if item.content_id == section.id
    )
    disposition.state = "not-applicable"
    disposition.reason = "Synthetic fixture has no clinical references; not for use"
    next(
        item
        for item in content.narrative_content_items
        if item.id.startswith("authored-arm-1")
    ).text += '<script>window.bad=true</script><b onclick="bad()">Retained bold</b>'
    response = render(monkeypatch, mapped(content), require_section_complete=True)
    assert response.status_code == 200, response.text
    assert "Not applicable: Synthetic fixture" in response.text
    assert (
        "window.bad=true" not in response.text
        and 'onclick="bad()"' not in response.text
    )
    assert "<b>Retained bold</b>" in response.text


@pytest.mark.parametrize(
    "change",
    [
        lambda value: value["documents"][1]["versions"][0]["contents"][0].update(
            contentItemId="foreign-item"
        ),
        lambda value: value["bindings"][1].update(master_document_id="foreign-master"),
        lambda value: value["bindings"][1].update(arm_uids=["foreign-arm"]),
        lambda value: value["bindings"][1].update(
            sponsor_organization_id="foreign-sponsor"
        ),
        lambda value: value["documents"][1]["versions"][0]["contents"][0].update(
            nextId="foreign-section"
        ),
        lambda value: value["documents"][1]["versions"][0]["contents"][0].update(
            childIds=[value["documents"][1]["versions"][0]["contents"][0]["id"]]
        ),
        lambda value: value["documents"][1]["versions"][0]["contents"][0].update(
            unsupportedClinicalFact="must not disappear"
        ),
        lambda value: value["documents"][1]["versions"][0]["contents"][0].update(
            instanceType="StudyArm"
        ),
        lambda value: value["documents"][1]["versions"][0]["status"].update(
            code="C25508", decode="Final"
        ),
        lambda value: value["section_dispositions"].append(
            deepcopy(value["section_dispositions"][0])
        ),
        lambda value: value["section_dispositions"][0].update(
            state="not-applicable", reason=""
        ),
    ],
)
def test_invalid_source_relations_and_silent_field_loss_are_rejected(change):
    _, content = authored_source()
    value = content.model_dump(mode="json")
    change(value)
    with pytest.raises(ValidationError):
        AuthoredProtocolDocuments.model_validate(value)


def test_shared_section_descendants_preserve_references_without_path_expansion():
    _, content = authored_source()
    sections = content.documents[0].versions[0].contents[:31]
    for index, section in enumerate(sections):
        section.childIds = [item.id for item in sections[index + 1 : index + 3]]
    source = content.model_dump(mode="json")
    assert (
        AuthoredProtocolDocuments.model_validate(source).model_dump(mode="json")
        == source
    )


def test_section_hierarchy_depth_is_bounded_even_with_cached_descendants():
    _, content = authored_source()
    sections = content.documents[0].versions[0].contents[:34]
    # Reverse traversal visits and caches each short suffix before the root.
    for index, section in enumerate(sections[1:], 1):
        section.childIds = [sections[index - 1].id]
    with pytest.raises(ValidationError, match="supported depth of 32"):
        AuthoredProtocolDocuments.model_validate(content.model_dump(mode="json"))


def test_exact_scope_selected_version_content_and_applicability_are_enforced(
    monkeypatch,
):
    report = mapped()
    assert render(monkeypatch, report, "wrong-document").status_code == 422
    report["document"]["study"]["versions"][0]["narrativeContentItems"][0][
        "text"
    ] = "tampered source"
    assert render(monkeypatch, report).status_code == 422


def test_no_sponsor_is_not_inferred_and_remains_a_completeness_blocker(monkeypatch):
    _, content = authored_source()
    content.bindings[2].sponsor_organization_id = None
    response = render(monkeypatch, mapped(content), require_section_complete=True)
    assert response.status_code == 422


@pytest.mark.parametrize("case", ["address", "entity-only-body", "wrong-heading"])
def test_apparently_filled_but_unresolved_source_cannot_pass_coverage(case):
    _, content = authored_source()
    if case == "address":
        content.organizations[0].legalAddress.text = "   "
        content.organizations[0].legalAddress.lines = ["\t"]
    elif case == "entity-only-body":
        content.narrative_content_items[0].text = "<p>&nbsp;&#160;</p>"
    else:
        content.documents[0].versions[0].contents[
            0
        ].sectionTitle = "Wrong clinical heading"
    assert assess_authored_documents(content)["section_complete"] is False


def test_authored_table_structure_headers_units_and_footnote_text_survive_http(
    monkeypatch,
):
    _, content = authored_source()
    item = next(
        item
        for item in content.narrative_content_items
        if item.id.startswith("authored-arm-1")
    )
    item.text = '<table onclick="unsafe()"><caption>Synthetic doses</caption><thead><tr><th scope="col">Dose</th><th scope="col">Unit</th></tr></thead><tbody><tr><td>0<sup>a</sup></td><td>mg/day</td></tr></tbody></table><p><sup>a</sup>Qualification value only.</p>'
    response = render(monkeypatch, mapped(content))
    assert response.status_code == 200, response.text
    page = html.fromstring(response.text)
    section = page.xpath("//section[@data-content-item-id=$uid]", uid=item.id)[0]
    assert section.xpath(".//th/text()") == ["Dose", "Unit"]
    assert section.xpath(".//td/text()") == ["0", "mg/day"]
    assert section.xpath(".//th/@scope") == ["col", "col"]
    assert "Qualification value only." in section.text_content()
    assert not section.xpath(".//*[@onclick]")


def authoring_api(monkeypatch, *, locked=False, allowed=True):
    graph, content = authored_source()
    repository = Mock()
    repository.get_authored_documents.return_value = None
    metadata = SimpleNamespace(
        version_number=VERSION, study_status="LOCKED" if locked else "DRAFT"
    )
    native_study = SimpleNamespace(
        current_metadata=SimpleNamespace(version_metadata=metadata)
    )
    study_service = SimpleNamespace(
        get_by_uid=Mock(return_value=native_study),
        check_if_study_is_locked=Mock(return_value=locked),
    )
    monkeypatch.setattr(
        service_module, "StudyDefinitionDocumentRepository", lambda: repository
    )
    monkeypatch.setattr(service_module, "StudyService", lambda: study_service)
    monkeypatch.setattr(
        service_module,
        "StudyArmSelectionService",
        lambda: SimpleNamespace(
            get_all_selection=lambda **_: SimpleNamespace(items=graph["arms"])
        ),
    )
    monkeypatch.setattr(service_module, "acquire_write_lock_study_value", Mock())
    monkeypatch.setattr(service_module.db, "_active_transaction", object())

    def save(_study, source, content_hash, _expected, reason):
        repository.get_authored_documents.return_value = SimpleNamespace(
            authored_documents_json=source,
            authored_documents_hash=content_hash,
            authored_documents_reason=reason,
            authored_documents_author="synthetic-author",
        )

    repository.save_authored_documents.side_effect = save
    app = FastAPI()
    app.include_router(study_routes.router, prefix="/studies")
    register_exception_handlers(app, value_error=False)

    async def auth():
        return None

    async def visible():
        if not allowed:
            raise HTTPException(403, "Outside assigned scope")

    for dependency in (
        security.dependency,
        rbac.STUDY_READ.dependency,
        rbac.STUDY_WRITE.dependency,
    ):
        app.dependency_overrides[dependency] = auth
    app.dependency_overrides[enforce_visible_study] = visible
    body = {
        "expected_content_hash": None,
        "expected_study_version": VERSION,
        "reason": "Author synthetic qualification draft",
        "content": content.model_dump(mode="json"),
    }
    return TestClient(app), repository, study_service, body


def test_public_save_and_read_preserve_complete_source_hash_author_reason(monkeypatch):
    api, repository, _, body = authoring_api(monkeypatch)
    response = api.put(f"/studies/{STUDY_UID}/protocol-documents", json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["content"] == body["content"]
    assert (
        result["content_hash"]
        == AuthoredProtocolDocuments.model_validate(body["content"]).content_hash()
    )
    assert result["author_id"] == "synthetic-author"
    assert result["change_reason"] == body["reason"]
    assert result["assessment"]["section_complete"] is True
    assert len(result["content"]["documents"]) == 4
    assert (
        api.get(f"/studies/{STUDY_UID}/protocol-documents").json()["content"]
        == body["content"]
    )
    assert repository.save_authored_documents.call_count == 1


@pytest.mark.parametrize(
    "case, status",
    [("scope", 403), ("locked", 400), ("version", 409), ("arm", 400), ("extra", 400)],
)
def test_public_writes_enforce_scope_lock_version_arm_and_field_integrity(
    monkeypatch, case, status
):
    api, repository, _, body = authoring_api(
        monkeypatch, locked=case == "locked", allowed=case != "scope"
    )
    if case == "version":
        body["expected_study_version"] = "wrong-version"
    if case == "arm":
        body["content"]["bindings"][0]["arm_uids"].append("foreign-arm")
    if case == "extra":
        body["content"]["organizations"][0]["clinicalApproval"] = True
    response = api.put(f"/studies/{STUDY_UID}/protocol-documents", json=body)
    assert response.status_code == status, response.text
    repository.save_authored_documents.assert_not_called()


def test_explicit_historical_document_read_uses_exact_requested_native_version(
    monkeypatch,
):
    api, repository, study_service, _ = authoring_api(monkeypatch)
    response = api.get(
        f"/studies/{STUDY_UID}/protocol-documents",
        params={"study_value_version": "1.0"},
    )
    assert response.status_code == 200
    repository.get_authored_documents.assert_called_once_with(STUDY_UID, "1.0")
    study_service.get_by_uid.assert_called_once_with(
        STUDY_UID, study_value_version="1.0"
    )


def test_native_repository_conflict_prevents_any_new_selection(monkeypatch):
    repository = repository_module.StudyDefinitionDocumentRepository.__new__(
        repository_module.StudyDefinitionDocumentRepository
    )
    repository.author_id = "current-author"
    repository.get_authored_documents = Mock(
        return_value=SimpleNamespace(authored_documents_hash="newer-head")
    )
    monkeypatch.setattr(repository_module, "acquire_write_lock_study_value", Mock())
    node = Mock()
    monkeypatch.setattr(repository_module, "StudyDefinitionDocument", node)
    with pytest.raises(AlreadyExistsException):
        repository.save_authored_documents.__wrapped__(
            repository, STUDY_UID, "{}", "new", "stale", "reason"
        )
    node.assert_not_called()


def test_native_repository_copy_on_write_preserves_header_and_historical_record(
    monkeypatch,
):
    repository = repository_module.StudyDefinitionDocumentRepository.__new__(
        repository_module.StudyDefinitionDocumentRepository
    )
    repository.author_id = "current-author"
    old = repository_module.StudyDefinitionDocument(
        uid="selection-uid",
        protocol_header_major_version=1,
        protocol_header_minor_version=2,
        authored_documents_json="old-content",
        authored_documents_hash="old-hash",
    )
    old.element_id_property = "native-before-id"
    repository.get_authored_documents = Mock(return_value=old)
    value, root, after = Mock(), Mock(), Mock()
    node = Mock(return_value=SimpleNamespace(save=lambda: after))
    versioning = Mock()
    monkeypatch.setattr(repository_module, "acquire_write_lock_study_value", Mock())
    monkeypatch.setattr(
        repository_module.db, "cypher_query", Mock(return_value=([[root, value]], []))
    )
    monkeypatch.setattr(repository_module, "StudyDefinitionDocument", node)
    monkeypatch.setattr(
        repository_module, "_manage_versioning_with_relations", versioning
    )
    repository.save_authored_documents.__wrapped__(
        repository, STUDY_UID, "new-content", "new-hash", "old-hash", "explicit reason"
    )
    assert node.call_args.kwargs["protocol_header_minor_version"] == 2
    assert node.call_args.kwargs["authored_documents_author"] == "current-author"
    assert "element_id_property" not in node.call_args.kwargs
    assert old.__properties__["authored_documents_json"] == "old-content"
    value.has_study_definition_document.disconnect.assert_called_once_with(old)
    value.has_study_definition_document.connect.assert_called_once_with(after)
    assert versioning.call_args.kwargs["before"] is old
    assert versioning.call_args.kwargs["after"] is after
    assert versioning.call_args.kwargs["author_id"] == "current-author"
