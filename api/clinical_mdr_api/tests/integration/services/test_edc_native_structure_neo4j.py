"""Actual ODM create/read/relationship closure and EDC candidate projection.

Uses the existing empty collector-owned Community fixture. Source values here
are authored synthetic data; optional JSON output feeds CSL's real builder.
"""

# pytest fixtures are injected by name; private projection is the exporter path.
# pylint: disable=redefined-outer-name,unused-import,protected-access

import json
import os
from pathlib import Path

import pytest
from neomodel import db
from starlette_context import request_cycle_context

from clinical_mdr_api.models.odms.common_models import OdmTranslatedTextModel
from clinical_mdr_api.models.odms.form import OdmFormItemGroupPostInput
from clinical_mdr_api.models.odms.item import OdmItemPatchInput, OdmItemPostInput
from clinical_mdr_api.models.odms.item_group import (
    OdmItemGroupItemPostInput,
    OdmItemGroupPostInput,
)
from clinical_mdr_api.services.integrations.edc_export import EdcExportService
from clinical_mdr_api.services.integrations.edc_native_odm_candidates import (
    read_study_odm_candidates,
)
from clinical_mdr_api.services.odms.forms import OdmFormService
from clinical_mdr_api.services.odms.item_groups import OdmItemGroupService
from clinical_mdr_api.services.odms.items import OdmItemService
from clinical_mdr_api.tests.integration.services.test_native_item_observation_neo4j import (
    native,
)
from clinical_mdr_api.tests.integration.utils.data_library import (
    STARTUP_CODMDT_CODELIST,
)
from clinical_mdr_api.tests.integration.utils.utils import TestUtils
from common.auth.dependencies import dummy_access_token_claims, dummy_auth_object
from common.exceptions import BusinessLogicException

pytestmark = pytest.mark.estate_fixture


def test_native_odm_exact_versions_placements_precision_and_group_metadata(native):
    original_ids = [
        row[0] for row in db.cypher_query("MATCH (n) RETURN elementId(n)")[0]
    ]
    try:
        with request_cycle_context(
            {
                "auth": dummy_auth_object(
                    dummy_access_token_claims(user_id="structure-custody-fixture")
                )
            }
        ):
            TestUtils.create_dummy_user("structure-custody-fixture")
            TestUtils.create_library()
            db.cypher_query(STARTUP_CODMDT_CODELIST)
            namespace = TestUtils.create_odm_vendor_namespace(
                name="x360i",
                prefix="xthreesixtyi",
                url="https://360i.example.org/odm/v1",
            )
            attribute = TestUtils.create_odm_vendor_attribute(
                name="ext",
                vendor_namespace_uid=namespace.uid,
                compatible_types=["ItemGroupDef"],
                data_type="string",
            )
            form = TestUtils.create_odm_form(
                name="Structure custody",
                oid="F.STRUCTURE",
                repeating="Yes",
                approve=False,
                translated_texts=[
                    OdmTranslatedTextModel(
                        text_type="Description", language="en", text="Form instructions"
                    )
                ],
            )
            groups = []
            items = []
            for index, suffix in enumerate(["A-B", "A_B"]):
                item = OdmItemService().create(
                    OdmItemPostInput(
                        library_name="Sponsor",
                        name=f"Native result {index}",
                        oid=f"I.{suffix}",
                        datatype_uid="float_uid",
                        length=8,
                        significant_digits=3,
                        prompt="",
                        comment="",
                        sds_var_name="",
                        translated_texts=[
                            OdmTranslatedTextModel(
                                text_type="Description",
                                language="en",
                                text="Measured result",
                            )
                        ],
                    )
                )
                OdmItemService().approve(item.uid)
                group = OdmItemGroupService().create(
                    OdmItemGroupPostInput(
                        library_name="Sponsor",
                        sdtm_domain_uids=[],
                        name=f"Measurements {index}",
                        oid=f"G.{suffix}",
                        repeating="Yes" if index else "No",
                        translated_texts=[
                            OdmTranslatedTextModel(
                                text_type="Description",
                                language="en",
                                text=f"Group instructions {index}",
                            ),
                            OdmTranslatedTextModel(
                                text_type="osb:CompletionInstructions",
                                language="en",
                                text="Repeat for each specimen.",
                            ),
                        ],
                        vendor_attributes=[
                            {
                                "uid": attribute.uid,
                                "value": json.dumps(
                                    {
                                        "subtitle": "",
                                        "instructions": "Repeat for each specimen.",
                                    }
                                ),
                            }
                        ],
                    )
                )
                OdmItemGroupService().add_items(
                    group.uid,
                    [
                        OdmItemGroupItemPostInput(
                            uid=item.uid,
                            order_number=3,
                            mandatory="No",
                            vendor={"attributes": []},
                        )
                    ],
                    preserve_order=True,
                )
                with pytest.raises(
                    BusinessLogicException, match="ODM_ITEM_REFERENCE_DUPLICATE"
                ):
                    OdmItemGroupService().add_items(
                        group.uid,
                        [
                            OdmItemGroupItemPostInput(
                                uid=item.uid,
                                order_number=order,
                                mandatory="Yes",
                                vendor={"attributes": []},
                            )
                            for order in [1, 2]
                        ],
                        override=True,
                        preserve_order=True,
                    )
                saved_items = OdmItemGroupService().get_by_uid(group.uid).items
                assert len(saved_items) == 1 and saved_items[0].order_number == 3
                OdmItemGroupService().approve(group.uid)
                groups.append(group)
                items.append(item)
            OdmFormService().add_item_groups(
                form.uid,
                [
                    OdmFormItemGroupPostInput(
                        uid=group.uid,
                        order_number=index + 1,
                        mandatory="No",
                        vendor={"attributes": []},
                    )
                    for index, group in enumerate(groups)
                ],
                preserve_order=True,
            )
            with pytest.raises(
                BusinessLogicException, match="ODM_ITEM_GROUP_REFERENCE_DUPLICATE"
            ):
                OdmFormService().add_item_groups(
                    form.uid,
                    [
                        OdmFormItemGroupPostInput(
                            uid=groups[0].uid,
                            order_number=order,
                            mandatory="Yes",
                            vendor={"attributes": []},
                        )
                        for order in [1, 2]
                    ],
                    override=True,
                    preserve_order=True,
                )
            assert [
                group.uid for group in OdmFormService().get_by_uid(form.uid).item_groups
            ] == [group.uid for group in groups]
            OdmFormService().approve(form.uid)

            # Seed only the study's selected-activity link. All ODM definitions,
            # versions, ref edges and values above are written by native services.
            db.cypher_query(
                """
                MATCH (:StudyRoot {uid:$study})-[:LATEST]->(study:StudyValue)
                CREATE (study)-[:HAS_STUDY_ACTIVITY_INSTANCE]->(:StudyActivityInstance {uid:'structure-selection'})
                  -[:HAS_SELECTED_ACTIVITY_INSTANCE]->(activity:ActivityInstanceValue)
                CREATE (:ActivityInstanceRoot {uid:'structure-activity'})-[:HAS_VERSION {version:'1.0',status:'Final'}]->(activity)
                WITH activity
                UNWIND $items AS itemUid
                MATCH (:OdmItemRoot {uid:itemUid})-[:HAS_VERSION {version:'1.0'}]->(item:OdmItemValue)
                CREATE (activity)-[:CONTAINS_ACTIVITY_ITEM]->(activityItem:ActivityItem)
                CREATE (item)-[:LINKS_TO_ACTIVITY_ITEM]->(activityItem)
            """,
                {
                    "study": native.params["nativeStudyId"],
                    "items": [item.uid for item in items],
                },
            )
            candidates = read_study_odm_candidates(
                native.params["nativeStudyId"],
                None,
                form_reader=OdmFormService().get_by_uid,
                group_reader=OdmItemGroupService().get_by_uid,
                item_reader=OdmItemService().get_by_uid,
            )
            assert len(candidates["candidates"]) == 1
            candidate = candidates["candidates"][0]
            assert candidate["nativeVersion"] == "1.0"
            assert [row["record"]["version"] for row in candidate["groups"]] == [
                "1.0",
                "1.0",
            ]
            exporter = EdcExportService()
            exporter._native_form_candidates = candidates["candidates"]
            projected = exporter._forms(None, None, native.params["nativeStudyId"])[0]
            # Current native Draft content must not replace the exact Final value
            # still linked by the study's selected activity and group ITEM_REF.
            original = (
                OdmItemService().get_by_uid(items[0].uid, version="1.0").model_dump()
            )
            OdmItemService().create_new_version(items[0].uid)
            patch = {
                key: original.get(key)
                for key in OdmItemPatchInput.model_json_schema()["properties"]
            }
            patch.update(
                name="New unbound draft result",
                datatype_uid="float_uid",
                external_question=None,
                change_description="Owned unbound newer-value regression",
            )
            OdmItemService().edit_draft(items[0].uid, OdmItemPatchInput(**patch))
            reread = read_study_odm_candidates(
                native.params["nativeStudyId"],
                None,
                form_reader=OdmFormService().get_by_uid,
                group_reader=OdmItemGroupService().get_by_uid,
                item_reader=OdmItemService().get_by_uid,
            )
            assert (
                reread["candidates"][0]["groups"][0]["items"][0]["record"]["name"]
                == "Native result 0"
            )
            bound_item = (
                OdmItemGroupService().get_by_uid(groups[0].uid, version="1.0").items[0]
            )
            assert (
                reread["candidates"][0]["groups"][0]["items"][0]["record"]["version"]
                == bound_item.version
            )
            assert (
                bound_item.version != OdmItemService().get_by_uid(items[0].uid).version
            )
            # The source-import compatibility path reads the persisted group
            # extension without depending on a whole source form archive.
            legacy = EdcExportService()._forms({form.uid})[0][0]
            assert [section["subtitle"] for section in legacy["sections"]] == ["", ""]
            assert [section["instructions"] for section in legacy["sections"]] == [
                "Repeat for each specimen."
            ] * 2

        assert len(projected) == 1
        result = projected[0]
        assert result["repeating"] is True
        assert [group["repeating"] for group in result["sections"]] == [False, True]
        assert [group["description"] for group in result["sections"]] == [
            "Group instructions 0",
            "Group instructions 1",
        ]
        assert [group["instructions"] for group in result["sections"]] == [
            "Repeat for each specimen."
        ] * 2
        assert [group["order"] for group in result["sections"]] == [1, 2]
        assert len({group["id"] for group in result["sections"]}) == 2
        assert len({field["refKey"] for field in result["fields"]}) == 2
        assert [field["section"] for field in result["fields"]] == [
            group["id"] for group in result["sections"]
        ]
        for field in result["fields"]:
            assert field["significantDigits"] == 3
            assert field["length"] == 8
            assert field["label"] == ""
            assert field["required"] is False
            assert field["order"] == 3
        if output := os.environ.get("OSB_NATIVE_STRUCTURE_OUTPUT"):
            Path(output).write_text(
                json.dumps(result, ensure_ascii=False), encoding="utf-8"
            )
    finally:
        extra_ids = [
            row[0]
            for row in db.cypher_query(
                "MATCH (n) WHERE NOT elementId(n) IN $original RETURN elementId(n)",
                {"original": original_ids},
            )[0]
        ]
        db.cypher_query(
            "MATCH (n) WHERE elementId(n) IN $owned DETACH DELETE n",
            {"owned": extra_ids},
        )
