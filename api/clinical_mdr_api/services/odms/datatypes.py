"""ODM item datatypes as CODMDT controlled-terminology terms.

OpenStudyBuilder 2.10 stores an ODM item's datatype as a ``HAS_DATA_TYPE``
relationship to a term of the DDF ``CODMDT`` codelist instead of a free-text
``datatype`` property (migration_024 converts existing items). The term's
codelist submission value is the ODM 1.3 datatype string (``integer``,
``text``, ``float``, ...), which is what the AccuraTrial integration contracts
(native capture evidence, EDC field typing, Proposal V2 reconciliation) have
always carried. This module is the one place that translates between the two.
"""

from typing import Any

from common.config import settings
from common.exceptions import BusinessLogicException


def odm_datatype_value(datatype: Any) -> str | None:
    """Return the ODM datatype string of an item's ``datatype`` value.

    Accepts the 2.10 term object (a model or its dict form, carrying
    ``submission_value``) and, for records written before 2.10, the legacy
    plain string.
    """
    if datatype is None or isinstance(datatype, str):
        return datatype
    if isinstance(datatype, dict):
        return datatype.get("submission_value")
    return getattr(datatype, "submission_value", None)


def odm_datatype_term_uids(repos) -> dict[str, str]:
    """Map each lower-cased CODMDT submission value to its CT term uid."""
    terms, _ = repos.ct_codelist_aggregated_repository.find_all_terms_aggregated_result(
        codelist_submission_value=settings.ddf_odm_data_type_cl_submval
    )
    return {
        term.ct_codelist_term_vo.submission_value.lower(): term.ct_codelist_term_vo.term_uid
        for term in terms
    }


def odm_datatype_term_uid(term_uids: dict[str, str], odm_datatype: str | None) -> str:
    """Resolve one ODM datatype string to its CODMDT term uid, failing closed."""
    BusinessLogicException.raise_if(
        not odm_datatype, msg="DataType attribute is missing or empty in ODM XML"
    )
    term_uid = term_uids.get(odm_datatype.lower())
    BusinessLogicException.raise_if(
        term_uid is None,
        msg=f"ODM DataType '{odm_datatype}' not found in the {settings.ddf_odm_data_type_cl_submval} codelist.",
    )
    return term_uid


def odm_item_datatype_cypher(value: str) -> str:
    """Cypher expression: the ODM datatype string of the OdmItemValue ``value``.

    Null when the item has no datatype term. Reads the submission value of the
    term within the codelist its CTTermContext selects, as the item repository
    does for the API's ``datatype`` field.
    """
    return f"""head(COLLECT {{
        MATCH ({value})-[:HAS_DATA_TYPE]->(datatype_context:CTTermContext)
              -[:HAS_SELECTED_TERM]->(datatype_term:CTTermRoot)
        MATCH (datatype_context)-[:HAS_SELECTED_CODELIST]->(datatype_codelist:CTCodelistRoot)
              -[:HAS_TERM]->(datatype_codelist_term:CTCodelistTerm)-[:HAS_TERM_ROOT]->(datatype_term)
        RETURN datatype_codelist_term.submission_value
    }})"""
