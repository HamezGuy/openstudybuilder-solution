"""Exact terminology reads for the native arm-origin contract."""

from datetime import date, datetime, timezone

from neomodel import db

from clinical_mdr_api.domains.controlled_terminologies.ct_codelist_term import (
    CTSimpleCodelistTermAR,
)
from clinical_mdr_api.domains.study_selections.study_arm_origin import (
    STUDY_ARM_ORIGIN_CATALOGUE,
    STUDY_ARM_ORIGIN_CODELIST_UID,
    validate_study_arm_origin,
)
from common.exceptions import ValidationException


class StudyArmOriginRepository:
    @staticmethod
    def selected_uid(memberships: list[dict], description: str | None) -> str | None:
        """Validate the complete native relationship before exposing its UID."""
        if not isinstance(memberships, list) or len(memberships) > 1:
            raise ValidationException(msg="STUDY_ARM_DATA_ORIGIN_CONTEXT_AMBIGUOUS")
        uid = None
        if memberships:
            member = memberships[0]
            if (
                not isinstance(member, dict)
                or member.get("codelist_uid") != STUDY_ARM_ORIGIN_CODELIST_UID
                or not isinstance(member.get("term_uid"), str)
                or not member["term_uid"].strip()
            ):
                raise ValidationException(msg="STUDY_ARM_DATA_ORIGIN_CONTEXT_SCOPE_INVALID")
            uid = member["term_uid"]
        validate_study_arm_origin(uid, description)
        return uid

    @staticmethod
    def get_term(
        term_uid: str,
        at_specific_date_time: datetime | None = None,
    ) -> CTSimpleCodelistTermAR:
        if (
            not isinstance(term_uid, str)
            or not term_uid.strip()
        ):
            raise ValidationException(msg="STUDY_ARM_DATA_ORIGIN_TERM_SCOPE_INVALID")
        historical = at_specific_date_time is not None
        at = at_specific_date_time if historical else datetime.now(timezone.utc)
        if not isinstance(at, datetime) or at.tzinfo is None:
            raise ValidationException(msg="STUDY_ARM_DATA_ORIGIN_TERM_DATE_REQUIRED")
        result, columns = db.cypher_query(
            """
            MATCH (:CTCatalogue {name: $catalogue})-[:HAS_CODELIST]->
                  (cl:CTCodelistRoot {uid: $codelist_uid})
                  -[:HAS_ATTRIBUTES_ROOT]->(:CTCodelistAttributesRoot)
                  -[cav:HAS_VERSION]->(ca:CTCodelistAttributesValue)
            MATCH (cl)-[:HAS_NAME_ROOT]->(:CTCodelistNameRoot)
                  -[cnv:HAS_VERSION]->(cn:CTCodelistNameValue)
            MATCH (cl)-[membership:HAS_TERM]->(member:CTCodelistTerm)
                  -[:HAS_TERM_ROOT]->(term:CTTermRoot {uid: $term_uid})
            MATCH (term)-[:HAS_NAME_ROOT]->(:CTTermNameRoot)
                  -[tnv:HAS_VERSION]->(tn:CTTermNameValue)
            MATCH (term)-[:HAS_ATTRIBUTES_ROOT]->(:CTTermAttributesRoot)
                  -[tav:HAS_VERSION]->(ta:CTTermAttributesValue)
            WHERE all(version IN [cav, cnv, tnv, tav] WHERE
                    version.status IN $statuses
                    AND version.start_date <= $at
                    AND (version.end_date IS NULL OR version.end_date > $at))
              AND membership.start_date <= $at
              AND (membership.end_date IS NULL OR membership.end_date > $at)
            RETURN term.uid AS term_uid, tn.name AS term_name,
                   ta.preferred_term AS preferred_term, member.submission_value AS submission_value,
                   membership.order AS order, cn.name AS codelist_name,
                   cl.uid AS codelist_uid, ca.submission_value AS codelist_submission_value
            """,
            {
                "term_uid": term_uid,
                "catalogue": STUDY_ARM_ORIGIN_CATALOGUE,
                "codelist_uid": STUDY_ARM_ORIGIN_CODELIST_UID,
                "at": at,
                "statuses": ["Final", "Retired"] if historical else ["Final"],
            },
        )
        if len(result) != 1:
            raise ValidationException(
                msg="STUDY_ARM_DATA_ORIGIN_TERM_VERSION_REQUIRED: "
                f"expected one governed codelist membership for '{term_uid}' at {at.isoformat()}."
            )
        value = dict(zip(columns, result[0]))
        if any(
            not isinstance(value.get(key), str) or not value[key].strip()
            for key in (
                "term_uid", "term_name", "codelist_uid", "codelist_name",
                "submission_value", "codelist_submission_value",
            )
        ):
            raise ValidationException(msg="STUDY_ARM_DATA_ORIGIN_TERM_VERSION_INCOMPLETE")
        value["date_conflict"] = False
        return CTSimpleCodelistTermAR.from_result_dict(value)

    @staticmethod
    def package_code(
        term_uid: str, package_uid: str, effective_date: str
    ) -> dict[str, str]:
        """Read the publisher attributes retained in the selected native package.

        Native authoring intervals and package publication dates are different
        clocks. The package's explicit attribute edges select the released
        code and preferred term; a current sponsor name cannot replace them.
        Version history establishes eligibility without multiplying package
        membership rows when unchanged attributes have several eligible versions.
        """
        if any(
            not isinstance(value, str) or not value.strip()
            for value in (term_uid, package_uid, effective_date)
        ):
            raise ValidationException(msg="USDM_ARM_DATA_ORIGIN_CT_PIN_REQUIRED")
        try:
            valid_date = date.fromisoformat(effective_date).isoformat() == effective_date
        except ValueError:
            valid_date = False
        if not valid_date:
            raise ValidationException(msg="USDM_ARM_DATA_ORIGIN_CT_DATE_INVALID")
        result, columns = db.cypher_query(
            """
            MATCH (:CTCatalogue {name: $catalogue})-[:CONTAINS_PACKAGE]->
                  (package:CTPackage {uid: $package_uid})
                  -[:CONTAINS_CODELIST]->(pc:CTPackageCodelist)
                  -[:CONTAINS_ATTRIBUTES]->(ca:CTCodelistAttributesValue)
            MATCH (cl:CTCodelistRoot {uid: $codelist_uid})
                  -[:HAS_ATTRIBUTES_ROOT]->(car:CTCodelistAttributesRoot)
            MATCH (pc)-[:CONTAINS_TERM]->(:CTPackageTerm)
                  -[:CONTAINS_ATTRIBUTES]->(ta:CTTermAttributesValue)
            MATCH (term:CTTermRoot {uid: $term_uid})
                  -[:HAS_ATTRIBUTES_ROOT]->(tar:CTTermAttributesRoot)
            MATCH (library:Library)-[:CONTAINS_TERM]->(term)
            WHERE toString(package.effective_date) = $effective_date
              AND EXISTS {
                    MATCH (car)-[cav:HAS_VERSION]->(ca)
                    WHERE cav.status IN ['Final', 'Retired']
                  }
              AND EXISTS {
                    MATCH (tar)-[tav:HAS_VERSION]->(ta)
                    WHERE tav.status IN ['Final', 'Retired']
                  }
            RETURN ta.concept_id AS code, library.name AS code_system,
                   package.uid AS code_system_version, ta.preferred_term AS decode
            """,
            {
                "catalogue": STUDY_ARM_ORIGIN_CATALOGUE,
                "term_uid": term_uid,
                "codelist_uid": STUDY_ARM_ORIGIN_CODELIST_UID,
                "package_uid": package_uid,
                "effective_date": effective_date,
            },
        )
        if len(result) != 1:
            raise ValidationException(msg="USDM_ARM_DATA_ORIGIN_CT_PIN_REQUIRED")
        value = dict(zip(columns, result[0]))
        if any(
            not isinstance(value.get(key), str) or not value[key].strip()
            for key in ("code", "code_system", "code_system_version", "decode")
        ):
            raise ValidationException(msg="USDM_ARM_DATA_ORIGIN_CT_PIN_INCOMPLETE")
        return value
