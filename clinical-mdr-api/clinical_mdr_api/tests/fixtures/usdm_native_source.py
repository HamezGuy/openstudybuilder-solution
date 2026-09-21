"""Reusable isolated native source for the real USDM mapper and EDC exporter.

Only repository/service reads and the export clock are replaced. Mapping,
projection, source collection, canonical hashing and custody assembly run their
production implementations. No imported source carrier or USDM output is input.
"""

from contextlib import ExitStack, contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from clinical_mdr_api.services.ddf.usdm_mapper import USDMMapper
from clinical_mdr_api.services.ddf.usdm_mapping_context import native_json
from clinical_mdr_api.services.ddf.usdm_service import USDMService
from clinical_mdr_api.services.integrations.edc_export import EdcExportService
from clinical_mdr_api.tests.fixtures.usdm_native_study import (
    AS_OF, STUDY_UID, VERSION, native_study_graph,
)


TERMINOLOGY_PATH = Path(__file__).with_name("usdm_native_terminology.json")
EXPORT_TIME = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
COLLECTION_KEYS = {
    "studyArm": "arms", "studyVisit": "visits", "studyEpoch": "epochs",
    "studyElement": "elements", "studyDesignCell": "cells",
    "studyObjective": "objectives", "studyEndpoint": "endpoints",
    "studyCriteria": "criteria", "studyActivity": "activities",
    "studyCompound": "compounds", "studyCompoundDosing": "dosings",
    "studyStandardVersion": "standards", "studyCohort": "cohorts",
    "studyBranchArm": "branches", "studyActivityInstance": "instances",
    "studyActivityInstruction": "instructions", "studyActivityGroup": "groups",
    "studyActivitySubGroup": "subgroups", "studySoAGroup": "soa_groups",
    "studySoAFootnote": "footnotes", "studyDiseaseMilestone": "milestones",
    "studyActivitySchedule": "planning",
    "studyOperationalActivitySchedule": "operational",
    "studyDataSupplier": "data_suppliers",
    "studyDesignClass": "design_classes",
    "studySourceVariable": "source_variables",
}


class NativeStudySource:
    def __init__(self, graph=None):
        self.graph = native_study_graph() if graph is None else graph
        from clinical_mdr_api.tests.fixtures.usdm_native_activity import NativeActivitySource

        self.native_activity = NativeActivitySource()
        self.calls = []
        self.terminology = json.loads(TERMINOLOGY_PATH.read_text())
        self.ct_package_records = []
        self.ct_sponsor_packages = {}
        self.ct_package_extensions = {}
        self.ct_package_factory_writes = []
        for selection in self.graph["standards"]:
            package = native_json(selection.ct_package)
            readings = next(row for row in self.terminology["packages"]
                            if row["uid"] == package["uid"])
            attributes_by_code = {}
            for reading in readings["terms"]:
                term = reading["term"]
                attributes = {
                    "concept_id": term["conceptId"],
                    "preferred_term": term["preferredTerm"],
                    "definition": term["definition"],
                    "synonyms": term.get("synonyms", []),
                }
                previous = attributes_by_code.setdefault(term["conceptId"], attributes)
                assert previous == attributes
            for concept_id, attributes in attributes_by_code.items():
                self.ct_package_records.append({
                    "library": {"name": "CDISC", "is_editable": False},
                    "termUid": concept_id,
                    "termValueIdentity": f"synthetic-ct-value:{package['uid']}:{concept_id}",
                    "attributes": attributes,
                    "selectedPackage": deepcopy(package),
                    "selectedCatalogue": package["catalogue_name"],
                    "publishedPackage": deepcopy(package),
                    "publishedCatalogue": package["catalogue_name"],
                    "approvedNativeVersions": [{
                        "version": "1.0", "status": "Final",
                        "start_date": package["effective_date"] + "T00:00:00+00:00",
                        "end_date": None, "author_id": "synthetic-author",
                        "change_description": "Explicit synthetic native import of the pinned primary term.",
                    }],
                })

    def select_sponsor_package(self, catalogue_name, effective_date, library_name="Synthetic sponsor"):
        """Run the native repository/DTO factory; replace only graph persistence."""
        from clinical_mdr_api.domain_repositories.controlled_terminologies import ct_package_repository
        from clinical_mdr_api.models.controlled_terminologies.ct_package import CTPackage

        selection = next(row for row in self.graph["standards"]
                         if row.ct_package.catalogue_name == catalogue_name)
        stored_nodes = {}
        writes = []
        catalogue = SimpleNamespace(
            name=catalogue_name,
            contains_package=SimpleNamespace(connect=lambda node: writes.append({
                "type": "CONTAINS_PACKAGE", "catalogue": catalogue_name, "packageUid": node.uid,
            })),
        )

        class PackageNode:
            def __init__(self, **properties):
                fields = (
                    "uid", "name", "description", "label", "href", "registration_status",
                    "source", "effective_date", "import_date", "author_id",
                )
                self.properties = {field: properties.get(field) for field in fields}
                for field, value in self.properties.items():
                    setattr(self, field, value)
                self.contains_package = SimpleNamespace(single=lambda: catalogue)
                self.extends_package = SimpleNamespace(connect=lambda parent: writes.append({
                    "type": "EXTENDS_PACKAGE", "packageUid": self.uid, "parentUid": parent.uid,
                }))

            def save(self):
                assert self.uid not in stored_nodes
                stored_nodes[self.uid] = self
                writes.append({"type": "package", "properties": deepcopy(self.properties)})
                return self

        def find_node(**properties):
            found = [node for node in stored_nodes.values()
                     if all(getattr(node, field) == value for field, value in properties.items())]
            assert len(found) <= 1
            return found[0] if found else None

        class PackageClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return AS_OF if tz is None else AS_OF.astimezone(tz)

        PackageNode.nodes = SimpleNamespace(get_or_none=find_node)
        parent = PackageNode(**selection.ct_package.model_dump(mode="python"))
        stored_nodes[parent.uid] = parent
        with (
            patch.object(ct_package_repository, "CTPackage", PackageNode),
            patch.object(ct_package_repository, "datetime", PackageClock),
            patch.object(ct_package_repository.UserInfoService, "get_author_username_from_id",
                         return_value="Synthetic author"),
        ):
            native = ct_package_repository.CTPackageRepository().create_sponsor_package(
                extends_package=parent.uid, effective_date=effective_date,
                author_id="synthetic-author", library_name=library_name,
            )
            created = CTPackage.from_ct_package_ar(native)

        links = [row for row in writes if row["type"] == "EXTENDS_PACKAGE"]
        assert links == [{"type": "EXTENDS_PACKAGE", "packageUid": created.uid, "parentUid": parent.uid}]
        self.ct_sponsor_packages[created.uid] = {
            "properties": native_json(stored_nodes[created.uid].properties),
            "catalogue": catalogue_name,
        }
        self.ct_package_extensions[created.uid] = links[0]["parentUid"]
        self.ct_package_factory_writes.extend(native_json(writes))
        selection.ct_package = created
        return created

    def read(self, name):
        def reader(study_uid, study_value_version=None, page_size=0, **kwargs):
            self.calls.append((name, study_uid, study_value_version, page_size, kwargs))
            assert (study_uid, study_value_version) == (STUDY_UID, VERSION)
            assert page_size == 0
            return self.graph.get(name, [])
        return reader

    def collection_reader(self, collection):
        reader = self.read(COLLECTION_KEYS[collection.kind])
        if not collection.singleton:
            return reader

        def singleton_reader(**kwargs):
            rows = reader(**kwargs)
            assert len(rows) <= 1
            return rows[0] if rows else None

        return singleton_reader

    def schedules(self, study_uid, study_value_version=None, operational=False):
        name = "operational" if operational else "planning"
        self.calls.append((name, study_uid, study_value_version))
        assert (study_uid, study_value_version) == (STUDY_UID, VERSION)
        return self.graph[name]

    def definition(self, uid, version=None):
        self.calls.append(("definition", uid, version))
        assert (uid, version) == ("LibraryInstance_1", "1.0")
        return self.graph["definition"]

    def header(self, study_uid, study_value_version=None):
        self.calls.append(("header", study_uid, study_value_version))
        assert (study_uid, study_value_version) == (STUDY_UID, VERSION)
        return self.graph["header"]

    def history(self, study_uid, page_size=0):
        self.calls.append(("history", study_uid, page_size))
        assert (study_uid, page_size) == (STUDY_UID, 0)
        return self.graph["history"]

    def study(self, uid, study_value_version=None, **kwargs):
        self.calls.append(("study", uid, study_value_version, kwargs))
        assert (uid, study_value_version) == (STUDY_UID, VERSION)
        return self.graph["study"]

    def query(self, text, parameters=None):
        """Native graph row fixtures, using exact primary CDISC term readings."""
        if "MATCH (study_root:StudyRoot" in text:
            assert parameters == {"study_uid": STUDY_UID, "study_value_version": VERSION}
            assert "study_version.version = $study_value_version" in text
            assert "LINKS_TO_ACTIVITY_ITEM" in text
            self.calls.append(("native-odm-closure", STUDY_UID, VERSION))
            rows = self.odm_query_rows()
            columns = list(rows[0]) if rows else []
            return [[row[key] for key in columns] for row in rows], columns
        assert "[:LATEST]" not in text
        if "CONTAINS_DICTIONARY_TERM" in text:
            assert "{uid: $term_uid}" in text
            assert parameters == {"term_uid": "Dictionary_1", "as_of": AS_OF}
            return [[{"name": "Synthetic dictionary"},
                     {"name": "Synthetic disease", "dictionary_id": "D-1"},
                     {"version": "1.0"}]], None
        assert "package:CTPackage {uid: $package_uid}" in text
        assert "attributes.concept_id = $concept_id" in text
        assert "CTTermNameValue" not in text and "LIMIT 1" not in text
        selected_uid = parameters["package_uid"]
        membership_uid = selected_uid
        package_path = []
        # Native sponsor creation does not copy published term containment.
        # The old direct-containment query therefore correctly sees no rows.
        follows_ancestry = text.index("EXTENDS_PACKAGE") < text.index("CONTAINS_CODELIST")
        if follows_ancestry:
            seen = set()
            while membership_uid in self.ct_sponsor_packages:
                if membership_uid in seen or membership_uid not in self.ct_package_extensions:
                    return [], None
                seen.add(membership_uid)
                package_path.append(self.ct_sponsor_packages[membership_uid]["properties"])
                membership_uid = self.ct_package_extensions[membership_uid]
        rows = []
        for record in self.ct_package_records:
            if record["selectedPackage"]["uid"] != membership_uid:
                continue
            if parameters["concept_id"] not in (record["termUid"], record["attributes"]["concept_id"]):
                continue
            versions = [
                version for version in record["approvedNativeVersions"]
                if version["status"] in {"Final", "Retired"}
                and datetime.fromisoformat(version["start_date"]) <= parameters["source_datetime"]
            ]
            if versions:
                selected_package = package_path[0] if package_path else record["selectedPackage"]
                selected_catalogue = (self.ct_sponsor_packages[selected_uid]["catalogue"]
                                      if package_path else record["selectedCatalogue"])
                values = [
                    record["library"], record["termUid"], record["termValueIdentity"],
                    record["attributes"], selected_package, selected_catalogue,
                    record["publishedPackage"], record["publishedCatalogue"], versions,
                ]
                if "nodes(package_path)" in text:
                    values.append([*package_path, record["selectedPackage"]])
                rows.append(deepcopy(values))
        return rows, None

    def mapper(self):
        return USDMMapper(
            get_osb_study_design_cells=self.read("cells"),
            get_osb_study_arms=self.read("arms"), get_osb_study_epochs=self.read("epochs"),
            get_osb_study_elements=self.read("elements"),
            get_osb_study_endpoints=self.read("endpoints"), get_osb_study_visits=self.read("visits"),
            get_osb_study_activities=self.read("activities"), get_osb_activity_schedules=self.schedules,
            get_osb_study_objectives=self.read("objectives"),
            get_osb_study_standard_versions=self.read("standards"),
            get_osb_study_compounds=self.read("compounds"), get_osb_study_compound_dosings=self.read("dosings"),
            get_osb_study_criteria=self.read("criteria"),
            get_osb_study_cohorts=self.read("cohorts"), get_osb_study_branch_arms=self.read("branches"),
            get_osb_activity_instances=self.read("instances"), get_osb_activity_instructions=self.read("instructions"),
            get_osb_activity_groups=self.read("groups"), get_osb_activity_subgroups=self.read("subgroups"),
            get_osb_soa_groups=self.read("soa_groups"),
            get_osb_soa_footnotes=self.read("footnotes"), get_osb_disease_milestones=self.read("milestones"),
            get_osb_study_data_suppliers=self.read("data_suppliers"),
            get_osb_study_design_class=self.read("design_classes"),
            get_osb_study_source_variable=self.read("source_variables"),
            get_osb_activity_instance_definition=self.definition, get_osb_protocol_header=self.header,
            get_osb_snapshot_history=self.history,
        )

    def library_readers(self):
        # These are declared synthetic native library observations, not a
        # fallback query against the developer's database or a USDM echo.
        terms = {}
        for package in self.terminology["packages"]:
            for row in package["terms"]:
                terms.setdefault(row["term"]["conceptId"], []).append(row)

        def term_reading(kind, uid, version):
            if uid not in terms:
                raise LookupError("Native library fixture has no exact selected definition")
            rows = terms[uid]
            first = rows[0]["term"]
            return {
                "term_uid": uid, "version": version or "1.0",
                "name": first["preferredTerm"], "definition": first["definition"],
                "codelists": [deepcopy(row["codelist"]) for row in rows],
                "library_name": "CDISC", "fixture_reading_kind": kind,
            }
        def dictionary(uid, version):
            assert uid == "Dictionary_1"
            assert version is None
            return {
                "term_uid": uid, "name": "Synthetic disease", "dictionary_id": "D-1",
                "version": "1.0", "library_name": "Synthetic dictionary",
            }

        def unit(uid, version):
            return next(record for record in self.graph["unit_definitions"]
                        if (record.uid, record.version) == (uid, version))

        return {
            "dictionaryTerm": dictionary, "unitDefinition": unit,
            **{
            kind: (lambda uid, version, kind=kind: term_reading(kind, uid, version))
            for kind in ("ctTermAttributes", "ctTermName", "ctTermMemberships")
            },
        }

    @contextmanager
    def isolated(self):
        """All patches are restricted to native read boundaries and clock."""
        source = self

        class ExportClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return EXPORT_TIME if tz is not None else EXPORT_TIME.replace(tzinfo=None)

        with ExitStack() as stack:
            stack.enter_context(self.native_activity.isolated())
            stack.enter_context(patch(
                "clinical_mdr_api.services.studies.study_activity_instance_snapshot.StudyNativeLibrarySnapshot",
                side_effect=lambda *_args, **_kwargs: self.native_activity.snapshot(),
            ))
            stack.enter_context(patch(
                "clinical_mdr_api.services.studies.study_activity_instance_snapshot.ActivityItemClassRoot",
                SimpleNamespace(nodes=SimpleNamespace(
                    get_or_none=lambda uid: self.native_activity.item_class if uid == "ItemClass_1" else None,
                )),
            ))
            stack.enter_context(patch(
                "clinical_mdr_api.services.ddf.usdm_mapper.db.cypher_query", self.query
            ))
            stack.enter_context(patch(
                "clinical_mdr_api.services.ddf.usdm_service.StudyService",
                return_value=SimpleNamespace(get_by_uid=self.study),
            ))
            stack.enter_context(patch(
                "clinical_mdr_api.services.studies.study_arm_selection.StudyArmSelectionService",
                return_value=SimpleNamespace(get_all_selection=self.read("arms")),
            ))
            stack.enter_context(patch(
                "clinical_mdr_api.services.integrations.edc_native_study_records._reader",
                side_effect=source.collection_reader,
            ))
            stack.enter_context(patch(
                "clinical_mdr_api.services.integrations.edc_native_library_definitions.native_library_readers",
                self.library_readers,
            ))
            stack.enter_context(patch(
                "clinical_mdr_api.services.integrations.edc_export.datetime", ExportClock
            ))
            stack.enter_context(patch(
                "clinical_mdr_api.services.integrations.edc_export.config.settings.mapping_authority_mode",
                "shadow",
            ))
            yield

    def export(self, odm):
        self.odm = odm
        service = object.__new__(EdcExportService)
        service.study_service = SimpleNamespace(get_by_uid=self.study)
        service.visit_service_cls = SimpleNamespace(get_all_visits=self.read("visits"))
        service.native_visit_unit_reader = self.native_activity.visit_units
        for property_name, records in (
            ("study_event_service", odm["events"]), ("form_service", odm["forms"]),
            ("item_group_service", odm["groups"]), ("item_service", odm["items"]),
            ("method_service", []), ("condition_service", []),
        ):
            setattr(service, property_name, NativeOdmReader(records, self.calls, property_name))
        usdm = object.__new__(USDMService)
        usdm._usdm_mapper = self.mapper()
        service.usdm_service = usdm
        with self.isolated():
            return service.build_bundle(STUDY_UID, study_value_version=VERSION)

    def odm_query_rows(self):
        """Model the actual persisted links, never an expected export payload."""
        rows = []
        for link in self.odm["activityItemLinks"]:
            assert link["studyActivityInstanceUid"] == self.graph["instances"][0].study_activity_instance_uid
            assert (link["activityInstanceUid"], link["activityInstanceVersion"]) == (
                self.graph["definition"].uid, self.graph["definition"].version,
            )
            item = next(item for item in self.odm["items"]
                        if (item.uid, item.version) == (link["odmItemUid"], link["odmItemVersion"]))
            activity_item = self.graph["definition"].activity_items[link["activityItemIndex"]]
            for group in self.odm["groups"]:
                item_refs = [ref for ref in group.items if (ref.uid, ref.version) == (item.uid, item.version)]
                for item_ref in item_refs:
                    for form in self.odm["forms"]:
                        group_refs = [ref for ref in form.item_groups
                                      if (ref.uid, ref.version) == (group.uid, group.version)]
                        for group_ref in group_refs:
                            parents = [(event, ref) for event in self.odm["events"] for ref in event.forms
                                       if (ref.uid, ref.version) == (form.uid, form.version)]
                            for event, form_ref in parents or [(None, None)]:
                                rows.append({
                                    "activity_item": {
                                        "studyActivityInstanceUid": link["studyActivityInstanceUid"],
                                        "activityInstanceUid": link["activityInstanceUid"],
                                        "activityInstanceVersion": link["activityInstanceVersion"],
                                        "activityItemClassUid": activity_item.activity_item_class.uid,
                                        "sourceProperties": native_json(activity_item),
                                    },
                                    "activity_item_key": "synthetic-persisted-activity-item-0",
                                    "odm_item": {"uid": item.uid, "version": item.version, "oid": item.oid},
                                    "activity_item_link": deepcopy(link["relationship"]),
                                    "odm_item_group": {"uid": group.uid, "version": group.version, "oid": group.oid},
                                    "item_ref": native_json(item_ref),
                                    "odm_form": {"uid": form.uid, "version": form.version, "oid": form.oid},
                                    "item_group_ref": native_json(group_ref),
                                    "odm_study_event": ({"uid": event.uid, "version": event.version, "oid": event.oid}
                                                        if event is not None else None),
                                    "form_ref": native_json(form_ref),
                                })
        return rows

    def source_input(self, odm):
        return {
            "formatVersion": "synthetic-native-osb-study/1",
            "studyUid": STUDY_UID, "studyValueVersion": VERSION,
            "exportedAt": EXPORT_TIME.isoformat(),
            "graph": native_json(self.graph), "odm": native_json(odm),
            "candidateClassNativeHistories": native_json(self.native_activity.source_input()),
            "terminologyReadings": deepcopy(self.terminology),
            "nativeCtPackageRecords": deepcopy(self.ct_package_records),
            "nativeCtSponsorPackages": deepcopy(self.ct_sponsor_packages),
            "nativeCtPackageExtensions": deepcopy(self.ct_package_extensions),
            "nativeCtPackageFactoryWrites": deepcopy(self.ct_package_factory_writes),
            "scope": {
                "nativeModels": True, "repositoryReads": "isolated synthetic fixtures",
                "databaseAccess": False, "coreValidation": False,
                "inputUsdmDocument": False, "importedSourceCarrier": False,
            },
        }


class NativeOdmReader:
    def __init__(self, records, calls=None, name="odm"):
        self.records = {(record.uid, record.version): record for record in records}
        self.current_records = {record.uid: record for record in records}
        self.calls = calls if calls is not None else []
        self.name = name

    def get_all_odms(self, page_size=0, **kwargs):
        assert page_size == 0
        assert not kwargs
        return list(self.current_records.values())

    def get_by_uid(self, uid, version=None):
        self.calls.append((self.name, uid, version))
        return self.records[(uid, version)] if version is not None else self.current_records[uid]


def native_odm_graph():
    """Actual native ODM models: one form, three fields, one native event."""
    from clinical_mdr_api.models.odms.form import OdmForm
    from clinical_mdr_api.models.odms.item import OdmItem
    from clinical_mdr_api.models.odms.item_group import OdmItemGroup
    from clinical_mdr_api.models.odms.study_event import OdmStudyEvent

    base = {
        "library_name": "Synthetic native ODM", "start_date": AS_OF,
        "status": "Final", "version": "1.0", "change_description": "Synthetic native definition",
        "possible_actions": [],
    }
    common = {
        **base, "translated_texts": [], "aliases": [], "vendor_elements": [],
        "vendor_attributes": [], "vendor_element_attributes": [],
    }
    items = [
        OdmItem(
            **common, uid="OdmItem_platelets", oid="PLATELETS", name="Platelet count",
            datatype="float", prompt="Platelet count", comment="Preserve exact laboratory units.",
            unit_definitions=[{"uid": "Unit_1", "name": "10^9/L", "version": "1.0"}],
            terms=[], activity_instances=[], sds_var_name="LBORRES",
        ),
        OdmItem(
            **common, uid="OdmItem_collection_date", oid="COLLECTION_DATE", name="Collection date",
            datatype="date", prompt="Date of sample collection", unit_definitions=[],
            terms=[], activity_instances=[],
        ),
        OdmItem(
            **{**common, "aliases": [{"name": "NATIVE_COMMENT", "context": "synthetic-fixture"}]},
            uid="OdmItem_comment", oid="SAMPLE_COMMENT", name="Sample comment",
            datatype="text", length=512, prompt="Sample handling comment",
            comment="Trailing spaces retained in native metadata.  ",
            unit_definitions=[], terms=[], activity_instances=[],
        ),
    ]
    group = OdmItemGroup(
        **common, uid="OdmItemGroup_lab", oid="NATIVE_LAB_GROUP", name="Laboratory",
        sdtm_domains=[], repeating="No",
        items=[{"uid": item.uid, "version": "1.0", "order_number": index,
                "mandatory": "Yes" if index < 3 else "No", "vendor": {"attributes": []}}
               for index, item in enumerate(items, start=1)],
    )
    form = OdmForm(
        **common, uid="OdmForm_lab", oid="NATIVE_LAB", name="Native laboratory form",
        repeating="No", item_groups=[{
            "uid": group.uid, "version": "1.0", "order_number": 1,
            "mandatory": "Yes", "vendor": {"attributes": []},
        }],
    )
    event = OdmStudyEvent(
        **base, uid="OdmStudyEvent_visit2", oid="NATIVE_VISIT_2", name="Visit 2",
        repeating="No", type="Scheduled",
        forms=[{"uid": form.uid, "version": "1.0", "order_number": 1, "mandatory": "Yes"}],
    )
    return {
        "events": [event], "forms": [form], "groups": [group], "items": items,
        "activityItemLinks": [{
            "studyActivityInstanceUid": "InstanceSelection_1",
            "activityInstanceUid": "LibraryInstance_1", "activityInstanceVersion": "1.0",
            "activityItemIndex": 0, "odmItemUid": items[0].uid, "odmItemVersion": "1.0",
            "relationship": {"order": 1, "primary": True, "presetResponseValue": None,
                             "valueCondition": None, "valueDependentMap": None},
        }],
    }
