"""OpenStudyBuilder 2.10 ODM item datatypes at the importer transport."""

# exact == [] / == {} comparisons are the assertion
# pylint: disable=use-implicit-booleaness-not-comparison

import pytest

from importers.utils.api_bindings import ApiBinding


class _Binding(ApiBinding):
    """ApiBinding without a live API: the CODMDT codelist is served locally."""

    def __init__(self, terms=None):  # pylint: disable=super-init-not-called
        self.calls = []
        self._terms = (
            terms
            if terms is not None
            else [
                {"submission_value": "integer", "term_uid": "CODMDT_INTEGER"},
                {"submission_value": "text", "term_uid": "CODMDT_TEXT"},
                {"submission_value": "float", "term_uid": "CODMDT_FLOAT"},
            ]
        )

    def get_codelist_uid(self, codelist_submval):
        self.calls.append(("codelist", codelist_submval))
        return "CTCodelist_CODMDT"

    def get_all_from_api(self, path, params=None, items_only=True):
        self.calls.append(("terms", path))
        return self._terms


@pytest.mark.parametrize(
    "path, expected",
    [
        ("/odms/items", True),
        ("odms/items", True),
        ("/odms/items/OdmItem_000001", True),
        ("/odms/items?page_size=0", True),
        ("/odms/item-groups", False),
        ("/odms/items-other", False),
        ("/odms/forms/OdmForm_000001", False),
    ],
)
def test_only_odm_item_paths_are_translated(path, expected):
    assert ApiBinding.is_odm_items_path(path) is expected


def test_write_sends_the_codmdt_term_uid_instead_of_the_string():
    api = _Binding()
    body = {"name": "Age", "oid": "I.AGE", "datatype": "Integer", "length": None}
    sent = api.odm_item_request_body("/odms/items", body)
    assert sent == {
        "name": "Age",
        "oid": "I.AGE",
        "datatype_uid": "CODMDT_INTEGER",
        "length": None,
    }
    # The reviewed body itself is not mutated.
    assert body["datatype"] == "Integer" and "datatype_uid" not in body
    # The codelist is read once per binding.
    api.odm_item_request_body("/odms/items/OdmItem_000001", {"datatype": "text"})
    assert [call[0] for call in api.calls] == ["codelist", "terms"]


def test_write_is_untouched_off_item_paths_or_when_already_translated():
    api = _Binding()
    group = {"name": "G", "datatype": "text"}
    assert api.odm_item_request_body("/odms/item-groups", group) is group
    native = {"name": "I", "datatype_uid": "CODMDT_TEXT"}
    assert api.odm_item_request_body("/odms/items", native) is native
    assert api.calls == []


def test_unknown_datatype_or_missing_codelist_fails_closed():
    with pytest.raises(ValueError, match="OSB_ODM_DATATYPE_TERM_UNKNOWN:boolean"):
        _Binding().odm_item_request_body("/odms/items", {"datatype": "boolean"})
    with pytest.raises(ValueError, match="OSB_ODM_DATATYPE_CODELIST_UNAVAILABLE"):
        _Binding(terms=[]).odm_item_request_body("/odms/items", {"datatype": "text"})


def test_read_back_carries_the_odm_datatype_string():
    term = {
        "uid": "C170000",
        "name": "Integer",
        "codelist_uid": "C1",
        "submission_value": "integer",
    }
    record = {"uid": "OdmItem_1", "oid": "I.AGE", "datatype": term}
    assert ApiBinding.odm_item_response(record)["datatype"] == "integer"
    assert ApiBinding.odm_item_response([record])[0]["datatype"] == "integer"
    page = {"items": [record], "total": 1}
    assert ApiBinding.odm_item_response(page) == {
        "items": [{**record, "datatype": "integer"}],
        "total": 1,
    }
    legacy = {"uid": "OdmItem_2", "oid": "I.X", "datatype": "text"}
    assert ApiBinding.odm_item_response(legacy) == legacy
    assert (
        ApiBinding.odm_item_response({"uid": "OdmItem_3", "datatype": None})["datatype"]
        is None
    )
    # The original record is not mutated.
    assert record["datatype"] is term
