"""Native TA and TE must project one authored code under the same SDTM limit."""

import pytest

from clinical_mdr_api.models.listings.listings_sdtm import (
    StudyArmListing,
    StudyElementListing,
)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("A-SCREEN", "A-SCREEN"),
        ("A-EVT", "A-EVT"),
        ("007", "007"),
        (None, None),
        ("", None),
        ("   ", None),
        ("A-FOLLOW-UP", None),
        ("é" * 4, "é" * 4),
        ("é" * 5, None),
    ],
)
def test_ta_and_te_keep_the_same_authored_code(source, expected):
    shared = {"STUDYID": "STUDY-1", "ELEMENT": "Screening", "ETCD": source}
    arm = StudyArmListing.from_query(
        dict(
            shared, DOMAIN="TA", ARM="Screening arm", ARMCD="SCR",
            TAETORD=14, TABRANCH=None, TATRANS=None, EPOCH="Screening",
        )
    )
    element = StudyElementListing.from_query(
        dict(shared, DOMAIN="TE", TESTRL=None, TEENRL=None, TEDUR=None)
    )
    assert arm.TAETORD == "14"
    assert arm.SOURCE_ELEMENT_CODE == element.SOURCE_ELEMENT_CODE == source
    assert arm.ETCD == element.ETCD == expected
