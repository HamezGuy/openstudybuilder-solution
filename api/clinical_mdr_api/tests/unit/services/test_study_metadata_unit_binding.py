"""Exercise real native unit aggregates/converters with read-only repository doubles."""

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from pydantic import TypeAdapter

from clinical_mdr_api.domains.concepts.unit_definitions.unit_definition import (
    CTTerm,
    UnitDefinitionAR,
    UnitDefinitionValueVO,
)
from clinical_mdr_api.domains.versioned_object_aggregate import (
    LibraryItemMetadataVO,
    LibraryItemStatus,
    LibraryVO,
)
from clinical_mdr_api.models.study_selections.study import StudyPatchRequestJsonModel
from clinical_mdr_api.services.integrations import (
    study_metadata_unit_binding as binding,
)
from clinical_mdr_api.services.integrations.candidate_set import OsbCandidateSetError
from common.config import settings


def unit(uid, name, *, status=LibraryItemStatus.FINAL, version=1, **changes):
    value = UnitDefinitionValueVO.from_repository_values(
        name=name,
        definition="Synthetic time unit",
        convertible_unit=True,
        display_unit=True,
        master_unit=False,
        si_unit=False,
        us_conventional_unit=False,
        use_complex_unit_conversion=False,
        ct_units=[],
        unit_subsets=[CTTerm("Subset_Time", settings.study_time_unit_subset)],
        ucum_uid="UCUM_year",
        ucum_name="a",
        unit_dimension_uid="Dimension_Time",
        unit_dimension_name="Time",
        legacy_code=None,
        use_molecular_weight=False,
        conversion_factor_to_master=31557600.0,
        order=None,
        comment=None,
        is_template_parameter=False,
    )
    return UnitDefinitionAR.from_repository_values(
        uid=uid,
        unit_definition_value=replace(value, **changes),
        library=LibraryVO.from_repository_values("Synthetic units", True),
        item_metadata=LibraryItemMetadataVO.from_repository_values(
            change_description="Synthetic final version",
            status=status,
            author_id="unit-test",
            author_username="unit-test",
            start_date=datetime(2026, 1, 1, tzinfo=UTC),
            end_date=None,
            major_version=version,
            minor_version=0,
        ),
    )


class ReadOnlyRepository:
    def __init__(self, units):
        self.units = units
        self.revalidation_units = None
        self.not_current = set()
        self.calls = []
        self.closed = False

    def find_all(self, **kwargs):
        self.calls.append(deepcopy(kwargs))
        assert kwargs["subset"] == settings.study_time_unit_subset
        assert set(kwargs) <= {"subset", "uids"}
        values = self.units
        if "uids" in kwargs:
            values = (
                self.revalidation_units
                if self.revalidation_units is not None
                else values
            )
            values = [item for item in values if item.uid in kwargs["uids"]]
        return deepcopy(values), len(values)

    def latest_concept_is_final(self, uid):
        return uid not in self.not_current

    def close(self):
        self.closed = True


@pytest.fixture(name="repository")
def repository_fixture(monkeypatch):
    result = ReadOnlyRepository([unit("Year", "Year"), unit("Years", "Years")])
    monkeypatch.setattr(binding, "UnitDefinitionRepository", lambda: result)
    return result


def resolve(name="Year", amount=18):
    reference = {"unitName": name, "basis": "stated:unit"}
    plan = {
        "metadataValue": {
            "duration_value": amount,
            "duration_unit_code": {"unitRef": "unit"},
        },
        "unitRefs": {"unit": deepcopy(reference)},
    }
    before = deepcopy((reference, plan))
    result = binding.resolve_study_metadata_unit(reference, plan)
    assert (reference, plan) == before
    return result


@pytest.mark.parametrize(
    "source,amount,expected",
    [
        ("Year", 18, "Years"),
        ("Years", 18, "Years"),
        ("Year", 1, "Year"),
        ("Years", 1, "Year"),
        ("Year", 0, "Year"),
        ("Years", 0, "Year"),
    ],
)
def test_actual_native_roundtrip_uses_proven_canonical_unit(
    repository, source, amount, expected
):
    result = resolve(source, amount)
    assert result["value"] == {"uid": expected}
    identity = result["identity"]
    assert identity["uid"] == expected
    assert identity["sourceUnitIdentity"]["uid"] == source
    assert identity["sourceUnitIdentity"]["version"] == "1.0"
    assert (
        identity["sourceUnitIdentity"]["valueHash"]["schemaVersion"]
        == binding.DEFINITION_CONTRACT
    )
    assert identity["correspondence"]["persistedDuration"] == f"P{amount}Y"
    assert identity["correspondence"]["nativeReadBackValue"] == {
        "duration_value": amount,
        "duration_unit_code": {"uid": expected},
    }
    assert repository.calls[0] == {"subset": settings.study_time_unit_subset}
    assert repository.closed
    TypeAdapter(StudyPatchRequestJsonModel).validate_python(
        {
            "current_metadata": {
                "study_population": {
                    "planned_minimum_age_of_subjects": {
                        "duration_value": amount,
                        "duration_unit_code": result["value"],
                    },
                }
            },
        },
        strict=True,
    )


def test_preserves_native_repository_order_even_when_plural_is_first(repository):
    repository.units.reverse()
    result = resolve("Year", 1)
    # OSB chooses the first matching unit for 0/1; service sorting would differ.
    assert result["value"] == {"uid": "Years"}
    assert result["identity"]["sourceUnitIdentity"]["uid"] == "Year"


def test_calls_the_real_native_converters(repository, monkeypatch):
    write = binding.create_duration_object_from_api_input
    read = binding.from_duration_object_to_value_and_unit
    calls = []

    def observe_write(*args, **kwargs):
        calls.append("write")
        return write(*args, **kwargs)

    def observe_read(*args, **kwargs):
        calls.append("read")
        return read(*args, **kwargs)

    monkeypatch.setattr(binding, "create_duration_object_from_api_input", observe_write)
    monkeypatch.setattr(binding, "from_duration_object_to_value_and_unit", observe_read)
    assert resolve()["value"] == {"uid": "Years"}
    assert calls == ["write", "read", "write", "read"]
    assert repository.closed


@pytest.mark.parametrize(
    "changes",
    [
        {"ucum_uid": "UCUM_different"},
        {"ucum_name": "different-code"},
        {"unit_dimension_uid": "Dimension_different"},
        {"unit_dimension_name": "Different dimension"},
        {"conversion_factor_to_master": 31557601.0},
    ],
)
def test_rejects_different_semantics_despite_matching_names(repository, changes):
    repository.units[1] = unit("Years", "Years", **changes)
    with pytest.raises(OsbCandidateSetError) as error:
        resolve()
    assert error.value.code == "OSB_STUDY_METADATA_UNIT_CORRESPONDENCE_MISMATCH"


@pytest.mark.parametrize(
    "changes",
    [
        {"ucum_uid": None},
        {"ucum_name": None},
        {"unit_dimension_uid": None},
        {"unit_dimension_name": None},
        {"conversion_factor_to_master": None},
        {"conversion_factor_to_master": 0},
        {"conversion_factor_to_master": float("inf")},
        {"conversion_factor_to_master": float("nan")},
        {"conversion_factor_to_master": True},
        {"convertible_unit": False},
        {"use_complex_unit_conversion": True},
        {"use_molecular_weight": True},
        {"use_molecular_weight": None},
    ],
)
def test_missing_or_context_dependent_semantics_never_prove_equivalence(
    repository, changes
):
    repository.units = [unit("Year", "Year", **changes)]
    with pytest.raises(OsbCandidateSetError) as error:
        resolve("Year", 1)
    assert error.value.code == "OSB_STUDY_METADATA_UNIT_SEMANTICS_UNPROVEN"


def test_native_initial_letter_collision_is_not_unit_equivalence(repository):
    repository.units = [
        unit(
            "Months",
            "Months",
            ucum_uid="UCUM_month",
            ucum_name="mo",
            conversion_factor_to_master=2629800.0,
        ),
        unit(
            "Minutes",
            "Minutes",
            ucum_uid="UCUM_minute",
            ucum_name="min",
            conversion_factor_to_master=60.0,
        ),
    ]
    with pytest.raises(OsbCandidateSetError) as error:
        resolve("Minutes", 18)
    assert error.value.code == "OSB_STUDY_METADATA_UNIT_CORRESPONDENCE_MISMATCH"


@pytest.mark.parametrize("status", [LibraryItemStatus.DRAFT, LibraryItemStatus.RETIRED])
def test_does_not_filter_invalid_canonical_units_out_of_native_reader_order(
    repository, status
):
    repository.units[1] = unit("Years", "Years", status=status)
    with pytest.raises(OsbCandidateSetError) as error:
        resolve()
    assert error.value.code == "OSB_STUDY_METADATA_UNIT_NOT_CURRENT"


@pytest.mark.parametrize("status", [LibraryItemStatus.DRAFT, LibraryItemStatus.RETIRED])
def test_source_must_have_a_final_definition(repository, status):
    repository.units[0] = unit("Year", "Year", status=status)
    with pytest.raises(OsbCandidateSetError) as error:
        resolve()
    assert error.value.code == "OSB_STUDY_METADATA_UNIT_UNRESOLVED"


@pytest.mark.parametrize("uid", ["Year", "Years"])
def test_requires_an_open_final_native_version_relationship(repository, uid):
    repository.not_current.add(uid)
    with pytest.raises(OsbCandidateSetError) as error:
        resolve()
    assert error.value.code == "OSB_STUDY_METADATA_UNIT_NOT_CURRENT"


@pytest.mark.parametrize(
    "field,value",
    [
        ("_end_date", datetime(2026, 1, 2, tzinfo=UTC)),
        ("_start_date", datetime(2999, 1, 1, tzinfo=UTC)),
        ("_start_date", datetime(2026, 1, 1)),
    ],
)
def test_rejects_expired_future_or_unqualified_validity(repository, field, value):
    source = repository.units[0]
    source._item_metadata = replace(source.item_metadata, **{field: value})
    with pytest.raises(OsbCandidateSetError) as error:
        resolve()
    assert error.value.code == "OSB_STUDY_METADATA_UNIT_NOT_CURRENT"


def test_a_unit_outside_study_time_is_not_accepted(repository):
    repository.units[0] = unit("Year", "Year", unit_subsets=[])
    with pytest.raises(OsbCandidateSetError) as error:
        resolve()
    assert error.value.code == "OSB_STUDY_METADATA_UNIT_NOT_CURRENT"


def test_ambiguous_source_names_are_blocked(repository):
    repository.units.append(unit("Other_Year", "year"))
    with pytest.raises(OsbCandidateSetError) as error:
        resolve()
    assert error.value.code == "OSB_STUDY_METADATA_UNIT_UNRESOLVED"


def test_missing_canonical_plural_is_blocked(repository):
    repository.units = [repository.units[0]]
    with pytest.raises(OsbCandidateSetError) as error:
        resolve()
    assert error.value.code == "OSB_STUDY_METADATA_UNIT_NATIVE_READBACK_UNAVAILABLE"


@pytest.mark.parametrize("amount", [None, False, True, -1, 1.5, 18.0, "18"])
def test_duration_value_must_match_native_integer_type_before_any_reads(
    repository, amount
):
    with pytest.raises(OsbCandidateSetError) as error:
        resolve(amount=amount)
    assert error.value.code == "OSB_STUDY_METADATA_UNIT_DURATION_INVALID"
    assert repository.calls == []


@pytest.mark.parametrize("name", [None, "", " ", 18])
def test_source_name_is_required_before_any_reads(repository, name):
    with pytest.raises(OsbCandidateSetError) as error:
        resolve(name=name)
    assert error.value.code == "OSB_STUDY_METADATA_UNIT_REFERENCE_INVALID"
    assert repository.calls == []


@pytest.mark.parametrize("changed", ["Year", "Years"])
def test_rechecks_both_exact_definitions_before_returning_binding(repository, changed):
    repository.revalidation_units = [
        unit(item.uid, item.name, version=2 if item.uid == changed else 1)
        for item in repository.units
    ]
    with pytest.raises(OsbCandidateSetError) as error:
        resolve()
    assert error.value.code == "OSB_STUDY_METADATA_UNIT_CHANGED"
    assert repository.closed


def test_deletion_during_correspondence_resolution_is_blocked(repository):
    repository.revalidation_units = [repository.units[0]]
    with pytest.raises(OsbCandidateSetError) as error:
        resolve()
    assert error.value.code == "OSB_STUDY_METADATA_UNIT_CHANGED"


def test_incomplete_semantics_during_revalidation_keep_a_named_blocker(repository):
    repository.revalidation_units = [
        repository.units[0],
        unit("Years", "Years", conversion_factor_to_master=float("nan")),
    ]
    with pytest.raises(OsbCandidateSetError) as error:
        resolve()
    assert error.value.code == "OSB_STUDY_METADATA_UNIT_SEMANTICS_UNPROVEN"


def test_same_uid_cannot_hide_different_source_and_canonical_definitions(repository):
    source, canonical = unit("Shared", "Year"), unit("Shared", "Years")
    repository.units = [source, canonical]
    repository.revalidation_units = [canonical]
    with pytest.raises(OsbCandidateSetError) as error:
        resolve()
    assert error.value.code == "OSB_STUDY_METADATA_UNIT_CHANGED"


def test_replay_recomputes_exact_hashes_of_source_and_canonical_definitions(repository):
    first = resolve()
    assert resolve() == first
    repository.units[0] = unit(
        "Year", "Year", comment="Later source-only definition change"
    )
    second = resolve()
    assert second["value"] == first["value"]
    assert second["identity"]["valueHash"] == first["identity"]["valueHash"]
    assert (
        second["identity"]["sourceUnitIdentity"]["valueHash"]
        != first["identity"]["sourceUnitIdentity"]["valueHash"]
    )
    repository.units[1] = unit("Years", "Years", version=2)
    third = resolve()
    assert third["identity"]["version"] == "2.0"
    assert third["identity"]["valueHash"] != second["identity"]["valueHash"]


def test_native_order_change_changes_binding_instead_of_hiding_stale_offer(repository):
    first = resolve("Year", 1)
    repository.units.reverse()
    second = resolve("Year", 1)
    assert first["identity"] != second["identity"]
    assert first["value"] == {"uid": "Year"}
    assert second["value"] == {"uid": "Years"}
