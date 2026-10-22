"""Synthetic platform composition and appendix locks on the owned Community graph."""

# Pytest resolves the imported fixture functions by parameter name.
# pylint: disable=redefined-outer-name

import json
import os
from pathlib import Path

import pytest
from neomodel import db

from clinical_mdr_api.domain_repositories.study_selections.study_version_repository import (
    StudyVersionRepository,
)
from clinical_mdr_api.domains.study_selections.study_arm_origin import (
    STUDY_ARM_ORIGIN_CATALOGUE,
    STUDY_ARM_ORIGIN_CODELIST_UID,
)
from clinical_mdr_api.models.study_selections.study_selection import (
    StudySelectionArmCreateInput,
)
from clinical_mdr_api.routers.ddf import study_definitions
from clinical_mdr_api.services.ddf.usdm_mapping_context import (
    USDMMappingAuthorityRequired,
)
from clinical_mdr_api.services.ddf.usdm_service import USDMService
from clinical_mdr_api.services.studies.study_arm_selection import (
    StudyArmSelectionService,
)
from clinical_mdr_api.tests.integration.services.test_authored_protocol_documents_neo4j import (  # noqa: F401  pylint: disable=unused-import
    _author_first_protocol_documents,
    _create_protocol_study,
    native_visit_graph,
    protocol_client,
    protocol_graph,
)
from clinical_mdr_api.tests.integration.services.test_study_arm_lifecycle_neo4j import (  # noqa: F401  pylint: disable=unused-import
    _fingerprint,
    _lock,
    _unlock,
    lifecycle_client,
)
from clinical_mdr_api.tests.integration.utils.utils import TestUtils
from common.config import settings

pytestmark = [
    pytest.mark.estate_fixture,
    pytest.mark.parametrize(
        "native_visit_graph", ["study-only"], indirect=True, scope="module"
    ),
]


@pytest.fixture
def platform_client(lifecycle_client):
    lifecycle_client.app.include_router(study_definitions.router)
    return lifecycle_client


def _subpart(client, fixture, parent_uid, acronym):
    response = client.post(
        "/studies",
        json={
            "study_parent_part_uid": parent_uid,
            "study_subpart_acronym": acronym,
            "description": f"Synthetic regimen appendix {acronym}",
        },
    )
    assert response.status_code == 201, response.text
    child_uid = response.json()["uid"]
    fixture.auth.user.study_ids.add(child_uid)
    return child_uid


def _pins(fixture, study_uid, version=None):
    db.set_connection(url=fixture.dsn)
    with fixture.principal():
        return StudyVersionRepository().get_subpart_pins(study_uid, version)


def _history(client, study_uid):
    response = client.get(
        f"/studies/{study_uid}/snapshot-history", params={"page_size": 0}
    )
    assert response.status_code == 200, response.text
    return {item["metadata_version"]: item for item in response.json()["items"]}


def test_http_platform_document_composes_exact_subpart_versions(
    protocol_graph, platform_client
):
    fixture, reasons = protocol_graph
    client = platform_client
    parent = _create_protocol_study(fixture, client, title="Synthetic HEALEY-like")
    children = [_subpart(client, fixture, parent, name) for name in ("SYNA", "SYNB")]
    with fixture.principal():
        TestUtils.create_study_arm(
            parent, name="Synthetic shared placebo", short_name="PBO"
        )
        arm = TestUtils.create_study_arm(
            children[0], name="Synthetic regimen A active", short_name="RA"
        ).arm_uid
        TestUtils.create_study_arm(
            children[1], name="Synthetic regimen B active", short_name="RB"
        )
    assert _pins(fixture, parent) == [
        {"study_uid": uid, "study_value_version": None, "subpart_acronym": acronym}
        for uid, acronym in sorted(zip(children, ("SYNA", "SYNB")))
    ]
    # A draft is incomplete, so compare with the existing single-design report.
    db.set_connection(url=fixture.dsn)
    with fixture.principal():
        single = USDMService().get_by_uid_with_report(parent)["document"]
    draft = client.get(f"/studyDefinitions/{parent}/platform-designs")
    assert draft.status_code == 200, draft.text
    body = draft.json()
    assert body["executableRelease"]["selected"] is False
    assert [
        (scope["studyUid"], scope["level"], scope["studyValueVersion"])
        for scope in body["designScopes"]
    ] == [
        (parent, "master", None),
        *((uid, "subpart", None) for uid in sorted(children)),
    ]
    # This minimal fixture authors no study type, so the single-design mapper
    # yields no design. Each design scope reports that, and nothing is invented.
    single_designs = single["study"]["versions"][0].get("studyDesigns") or []
    designs = body["document"]["study"]["versions"][0]["studyDesigns"]
    assert len(designs) == len(single_designs) * 3
    absent = [
        issue
        for issue in body["mappingReport"]["issues"]
        if issue["code"] == "USDM_PLATFORM_DESIGN_ABSENT"
    ]
    assert len(absent) == 3 - len(designs)
    assert [row["studyUid"] for row in body["mappingReport"]["designs"]] == [
        parent,
        *sorted(children),
    ]

    _lock(client, parent, reasons)
    assert {pin["study_value_version"] for pin in _pins(fixture, parent, "1")} == {"1"}
    _unlock(client, parent, reasons)
    response = client.patch(
        f"/studies/{children[0]}/study-arms/{arm}",
        json={"name": "Synthetic regimen A amended"},
    )
    assert response.status_code == 200, response.text
    _lock(client, parent, reasons)
    names = {}
    for version in ("1", "2"):
        pins = {
            pin["study_uid"]: pin["study_value_version"]
            for pin in _pins(fixture, parent, version)
        }
        assert pins == {uid: version for uid in children}
        # The pinned subpart version holds that version's own content.
        arms = client.get(
            f"/studies/{children[0]}/study-arms",
            params={"study_value_version": pins[children[0]]},
        )
        assert arms.status_code == 200, arms.text
        names[version] = sorted(item["name"] for item in arms.json()["items"])
        # The platform route refuses exactly when the single-design mapper does.
        db.set_connection(url=fixture.dsn)
        with fixture.principal():
            try:
                USDMService().get_by_uid_with_report(
                    parent, study_value_version=version
                )
                refusal = None
            except USDMMappingAuthorityRequired as error:
                refusal = error.msg
        response = client.get(
            f"/studyDefinitions/{parent}/platform-designs",
            params={"study_value_version": version},
        )
        if refusal is None:
            assert response.status_code == 200, response.text
            scopes = {
                scope["studyUid"]: scope["studyValueVersion"]
                for scope in response.json()["designScopes"]
            }
            assert scopes == {parent: version, **pins}
        else:
            assert response.status_code == 422, response.text
            assert response.json()["message"] == refusal
    assert names == {
        "1": ["Synthetic regimen A active"],
        "2": ["Synthetic regimen A amended"],
    }


def test_http_appendix_locks_against_the_exact_locked_parent(
    protocol_graph, platform_client
):
    fixture, reasons = protocol_graph
    client = platform_client
    parent = _create_protocol_study(fixture, client, title="Synthetic master")
    content, master_hash = _author_first_protocol_documents(fixture, client, parent)
    child = _subpart(client, fixture, parent, "SYNH")
    _lock(client, parent, reasons)
    _unlock(client, parent, reasons)
    pin = {
        "parent_study_version": "1",
        "master_document_id": content.documents[0].id,
        "master_document_version": content.documents[0].versions[0].version,
        "master_documents_hash": master_hash,
    }
    lock_body = {
        "change_description": "Synthetic appendix lock",
        "reason_for_change_uid": reasons["reason_for_lock_terms"][0].term_uid,
    }
    baseline = _fingerprint(fixture)
    for body, expected in (
        (lock_body, "explicit locked version"),
        (
            {**lock_body, "parent_pin": {**pin, "parent_study_version": "2"}},
            "latest locked",
        ),
        (
            {**lock_body, "parent_pin": {**pin, "master_documents_hash": "0" * 64}},
            "master documents",
        ),
        (
            {
                **lock_body,
                "parent_pin": {**pin, "master_document_id": "authored-arm-0"},
            },
            "master protocol",
        ),
        (
            {**lock_body, "parent_pin": {**pin, "master_document_version": "9.9"}},
            "master protocol",
        ),
    ):
        response = client.post(f"/studies/{child}/locks", json=body)
        assert response.status_code == 400, response.text
        assert expected in response.text
        assert _fingerprint(fixture) == baseline
    response = client.post(
        f"/studies/{parent}/locks", json={**lock_body, "parent_pin": pin}
    )
    assert response.status_code == 400, response.text
    assert _fingerprint(fixture) == baseline

    response = client.post(
        f"/studies/{child}/locks", json={**lock_body, "parent_pin": pin}
    )
    assert response.status_code == 201, response.text
    child_history = _history(client, child)
    assert child_history["2"]["parent_pin"] == {"parent_study_uid": parent, **pin}
    assert child_history["1"]["parent_pin"] is None, "The cascade lock pins nothing"

    # The appendix can be revised on its own clock while the master drafts.
    _unlock(client, child, reasons)
    response = client.post(
        f"/studies/{child}/locks", json={**lock_body, "parent_pin": pin}
    )
    assert response.status_code == 201, response.text

    # The master's next lock composes the appendix as locked, without relocking it.
    _lock(client, parent, reasons)
    assert _pins(fixture, parent, "2") == [
        {"study_uid": child, "study_value_version": "3", "subpart_acronym": "SYNH"}
    ]
    assert _pins(fixture, parent, "1") == [
        {"study_uid": child, "study_value_version": "1", "subpart_acronym": "SYNH"}
    ]
    assert set(_history(client, child)) == {"1", "2", "3"}

    # A locked master's composition is frozen.
    baseline = _fingerprint(fixture)
    response = client.post(
        f"/studies/{child}/unlocks",
        json={
            "reason_for_change_uid": reasons["reason_for_unlock_terms"][0].term_uid,
            "other_reason_for_unlocking": "Synthetic appendix revision",
        },
    )
    assert response.status_code == 400, response.text
    response = client.post(
        "/studies",
        json={
            "study_parent_part_uid": parent,
            "study_subpart_acronym": "SYNLATE",
            "description": "Synthetic late appendix",
        },
    )
    assert response.status_code == 400, response.text
    assert _fingerprint(fixture) == baseline
    response = client.get(f"/studies/{parent}", params={"study_value_version": "1"})
    assert response.status_code == 200, response.text
    assert child in response.json()["study_subpart_uids"]


def test_http_subparts_hold_their_own_arms_never_the_masters(
    protocol_graph, platform_client
):
    """CSL C11 refuses an arm in two designs; native subparts never share one."""
    fixture, _ = protocol_graph
    client = platform_client
    parent = _create_protocol_study(fixture, client, title="Synthetic arm ownership")
    with fixture.principal():
        master_arm = TestUtils.create_study_arm(
            parent, name="Synthetic shared placebo", short_name="PBO"
        ).arm_uid
        master_branch = TestUtils.create_study_branch_arm(
            parent,
            name="Synthetic placebo branch",
            short_name="PBOB",
            study_arm_uid=master_arm,
        ).branch_arm_uid
    # A subpart created after the master has arms starts with none of them.
    child = _subpart(client, fixture, parent, "SYNO")
    response = client.get(f"/studies/{child}/study-arms")
    assert response.status_code == 200, response.text
    assert response.json()["items"] == []
    with fixture.principal():
        # The same authored name gets a new native record of the subpart.
        child_arm = TestUtils.create_study_arm(
            child, name="Synthetic shared placebo", short_name="PBO"
        ).arm_uid
        child_branch = TestUtils.create_study_branch_arm(
            child,
            name="Synthetic placebo branch",
            short_name="PBOB",
            study_arm_uid=child_arm,
        ).branch_arm_uid
    assert {child_arm, child_branch}.isdisjoint({master_arm, master_branch})
    for study_uid, expected in ((parent, master_arm), (child, child_arm)):
        response = client.get(f"/studies/{study_uid}/study-arms")
        assert [item["arm_uid"] for item in response.json()["items"]] == [expected]
    # Every arm and branch arm node belongs to the values of exactly one study.
    db.set_connection(url=fixture.dsn)
    rows, _ = db.cypher_query(
        "MATCH (root:StudyRoot)-[:LATEST|HAS_VERSION]->(:StudyValue)"
        "-[:HAS_STUDY_ARM|HAS_STUDY_BRANCH_ARM]->(arm) "
        "WHERE root.uid IN $uids "
        "RETURN arm.uid, collect(DISTINCT root.uid)",
        {"uids": [parent, child]},
    )
    owners = {uid: set(roots) for uid, roots in rows}
    assert owners == {
        master_arm: {parent},
        master_branch: {parent},
        child_arm: {child},
        child_branch: {child},
    }


@pytest.fixture(scope="module")
def design_terms(protocol_graph):
    """The governed terms a locked design states: study type and arm data origin.

    The interventional study type on its configured codelist (C99077), and one
    arm data origin on the DDF CT codelist the arm origin rule governs (C188727).
    """
    fixture, _ = protocol_graph
    db.set_connection(url=fixture.dsn)
    with fixture.principal():
        TestUtils.create_ct_codelist(
            codelist_uid="C99077",
            name="Synthetic study type",
            submission_value="STYPE",
            extensible=True,
            approve=True,
        )
        TestUtils.create_ct_term(
            codelist_uid="C99077",
            term_uid="C98388",
            concept_id="C98388",
            submission_value="INTERVENTIONAL",
            sponsor_preferred_name="Interventional Study",
            sponsor_preferred_name_sentence_case="interventional study",
        )
        TestUtils.create_ct_catalogue(catalogue_name=STUDY_ARM_ORIGIN_CATALOGUE)
        TestUtils.create_ct_codelist(
            catalogue_name=STUDY_ARM_ORIGIN_CATALOGUE,
            codelist_uid=STUDY_ARM_ORIGIN_CODELIST_UID,
            name="Synthetic study arm data origin type",
            submission_value="ARMORIGIN",
            extensible=True,
            approve=True,
        )
        TestUtils.create_ct_term(
            catalogue_name=STUDY_ARM_ORIGIN_CATALOGUE,
            codelist_uid=STUDY_ARM_ORIGIN_CODELIST_UID,
            term_uid="C176263",
            concept_id="C176263",
            submission_value="SYNTHETIC",
            sponsor_preferred_name="Synthetic data",
            sponsor_preferred_name_sentence_case="synthetic data",
        )
        # A released code needs a CDISC library term in a CDISC package.
        TestUtils.create_library(name="CDISC", is_editable=True)
        # OSB selects arm types from the SDTM CT catalogue's ARMTTP codelist.
        TestUtils.create_ct_codelist(
            catalogue_name=settings.sdtm_ct_catalogue_name,
            codelist_uid="C174222",
            name="Synthetic arm type",
            submission_value=settings.study_arm_type_cl_submval,
            extensible=True,
            approve=True,
        )
        TestUtils.create_ct_term(
            catalogue_name=settings.sdtm_ct_catalogue_name,
            codelist_uid="C174222",
            term_uid="C174266",
            concept_id="C174266",
            library_name="CDISC",
            submission_value="EXPERIMENTAL",
            sponsor_preferred_name="Experimental Arm",
            sponsor_preferred_name_sentence_case="experimental arm",
        )
        # The DDF terminology package that pins the origin and arm type terms.
        # Real packages come from the standards import; this one contains
        # exactly these codelists and terms, as the import writes them.
        package_uid = f"{STUDY_ARM_ORIGIN_CATALOGUE} platform {fixture.nonce}"
        db.cypher_query(
            """
            MATCH (catalogue:CTCatalogue {name: $catalogue})
            CREATE (catalogue)-[:CONTAINS_PACKAGE]->(:CTPackage {
                uid: $package_uid, name: $package_uid, author_id: 'TEST',
                import_date: datetime(), effective_date: date()})
            """,
            {"catalogue": STUDY_ARM_ORIGIN_CATALOGUE, "package_uid": package_uid},
        )
        for codelist_uid, term_uid in (
            (STUDY_ARM_ORIGIN_CODELIST_UID, "C176263"),
            ("C174222", "C174266"),
        ):
            db.cypher_query(
                """
                MATCH (package:CTPackage {uid: $package_uid})
                MATCH (:CTCodelistRoot {uid: $codelist_uid})-[:HAS_ATTRIBUTES_ROOT]->
                      (:CTCodelistAttributesRoot)-[:LATEST]->(codelist:CTCodelistAttributesValue)
                MATCH (:CTTermRoot {uid: $term_uid})-[:HAS_ATTRIBUTES_ROOT]->
                      (:CTTermAttributesRoot)-[:LATEST]->(term:CTTermAttributesValue)
                CREATE (package)-[:CONTAINS_CODELIST]->(package_codelist:CTPackageCodelist {
                    uid: $package_uid + '_' + $codelist_uid})
                      -[:CONTAINS_ATTRIBUTES]->(codelist)
                CREATE (package_codelist)-[:CONTAINS_TERM]->(:CTPackageTerm {uid: $term_uid})
                      -[:CONTAINS_ATTRIBUTES]->(term)
                """,
                {
                    "package_uid": package_uid,
                    "codelist_uid": codelist_uid,
                    "term_uid": term_uid,
                },
            )
    return {
        "study_type": "C98388",
        "arm_origin": "C176263",
        "arm_type": "C174266",
        "ddf_package": package_uid,
    }


def _arm(study_uid, terms, name, short_name):
    return (
        StudyArmSelectionService()
        .make_selection(
            study_uid,
            StudySelectionArmCreateInput(
                name=name,
                short_name=short_name,
                arm_type_uid=terms["arm_type"],
                data_origin_type_uid=terms["arm_origin"],
                data_origin_description="Synthetic arm authored for the platform release test.",
            ),
        )
        .arm_uid
    )


def _set_study_type(client, study_uid, term_uid, parent_uid=None):
    body = {
        "current_metadata": {
            "high_level_study_design": {"study_type_code": {"term_uid": term_uid}}
        }
    }
    if parent_uid is not None:
        # An ordinary PATCH reads an omitted parent as removal from the parent.
        body["study_parent_part_uid"] = parent_uid
    response = client.patch(f"/studies/{study_uid}", json=body)
    assert response.status_code == 200, response.text


def test_http_locked_platform_version_is_one_executable_release(
    protocol_graph, platform_client, design_terms
):
    """A locked platform version releases the master and every subpart design."""
    fixture, reasons = protocol_graph
    client = platform_client
    # Package UIDs follow OSB's convention of naming their catalogue, which the
    # lock's existing standard version check relies on.
    parent = _create_protocol_study(
        fixture,
        client,
        title="Synthetic executable platform",
        package_name=f"{settings.sdtm_ct_catalogue_name} platform {fixture.nonce}",
    )
    children = [_subpart(client, fixture, parent, name) for name in ("SYXA", "SYXB")]
    _set_study_type(client, parent, design_terms["study_type"])
    for study_uid in children:
        _set_study_type(
            client, study_uid, design_terms["study_type"], parent_uid=parent
        )
    with fixture.principal():
        for study_uid in (parent, *children):
            TestUtils.create_study_standard_version(
                study_uid=study_uid, ct_package_uid=design_terms["ddf_package"]
            )
        placebo = _arm(parent, design_terms, "Synthetic shared placebo", "PBO")
        arm_a = _arm(children[0], design_terms, "Synthetic regimen A", "RA")
        branch_a = TestUtils.create_study_branch_arm(
            children[0],
            name="Synthetic regimen A high dose",
            short_name="RAH",
            study_arm_uid=arm_a,
        ).branch_arm_uid
        arm_b = _arm(children[1], design_terms, "Synthetic regimen B", "RB")

    draft = client.get(f"/studyDefinitions/{parent}/platform-designs")
    assert draft.status_code == 200, draft.text
    assert draft.json()["executableRelease"]["selected"] is False
    assert "draft" in draft.json()["executableRelease"]["reason"]

    _lock(client, parent, reasons)
    pins = {
        pin["study_uid"]: pin["study_value_version"]
        for pin in _pins(fixture, parent, "1")
    }
    assert pins == {uid: "1" for uid in children}
    response = client.get(
        f"/studyDefinitions/{parent}/platform-designs",
        params={"study_value_version": "1"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    release = body["executableRelease"]
    assert release["selected"] is True, release
    assert release["contract"] == "csl-platform-release-designs/1"
    assert release["masterLineageId"] == parent
    ordered = sorted(children)
    assert [design["designKey"] for design in release["designs"]] == [
        "master",
        *(f"subpart:{uid}" for uid in ordered),
    ]
    assert [design["designScope"] for design in release["designs"]] == [
        {"level": "master", "studyUid": parent, "studyValueVersion": "1"},
        *(
            {
                "level": "subpart",
                "studyUid": uid,
                "studyValueVersion": "1",
                "parentStudyUid": parent,
            }
            for uid in ordered
        ),
    ]
    arms = {
        design["designKey"]: {
            (arm["osbArmUid"], arm["osbBranchArmUid"]) for arm in design["arms"]
        }
        for design in release["designs"]
    }
    assert arms == {
        "master": {(placebo, None)},
        f"subpart:{children[0]}": {(arm_a, None), (arm_a, branch_a)},
        f"subpart:{children[1]}": {(arm_b, None)},
    }
    designs = body["document"]["study"]["versions"][0]["studyDesigns"]
    assert [design["usdmDesignId"] for design in release["designs"]] == [
        design["id"] for design in designs
    ]
    # Evidence for the CSL cross-check: the exact body a release would register.
    target = os.environ.get("OSB_PLATFORM_DESIGNS_EXPORT_PATH", "")
    if target:
        Path(target).write_text(json.dumps(body), encoding="utf-8")
