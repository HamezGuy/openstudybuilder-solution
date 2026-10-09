import json
from pathlib import Path as PathFromPathLib
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.templating import Jinja2Templates
from lxml import html

from clinical_mdr_api.domain_repositories.study_selections.study_soa_repository import (
    SoALayout,
)
from clinical_mdr_api.models.utils import PrettyJSONResponse
from clinical_mdr_api.routers import _generic_descriptions
from clinical_mdr_api.routers.studies.study_access import enforce_visible_study
from clinical_mdr_api.services.ddf.m11_preview import MISSING, m11_preview_context
from clinical_mdr_api.services.ddf.usdm_mapping_context import (
    USDMMappingAuthorityRequired,
)
from clinical_mdr_api.services.ddf.usdm_service import USDMService
from clinical_mdr_api.services.studies.study_design_figure import (
    StudyDesignFigureService,
)
from clinical_mdr_api.services.studies.study_flowchart import StudyFlowchartService
from common.auth import rbac
from common.auth.dependencies import security
from common.models.error import ErrorResponse
from common.telemetry import trace_block

router = APIRouter(
    prefix="/studyDefinitions",
    dependencies=[Depends(enforce_visible_study)],
)

M11_TEMPLATES_DIR_PATH = (
    PathFromPathLib(__file__).parent.parent.parent.parent / "m11-templates"
)
templates = Jinja2Templates(directory=str(M11_TEMPLATES_DIR_PATH))


@router.get(
    path="/{study_uid}",
    dependencies=[security, rbac.STUDY_READ],
    response_class=PrettyJSONResponse,
    status_code=200,
    responses={
        403: _generic_descriptions.ERROR_403,
        404: {
            "model": ErrorResponse,
            "description": "Not Found - The study with the specified 'study_uid' wasn't found.",
        },
    },
    summary="""Return an entire study in DDF USDM format""",
    description="""
State before:
- Study must exist.

State after:
- no change.

Possible errors:
- Invalid study-uid.
""",
)
def get_study(
    study_uid: Annotated[str, Path(description="The unique uid of the study.")],
    study_value_version: Annotated[
        str | None,
        Query(
            description="Optional explicit OSB study value version. All study selections are read at this same version."
        ),
    ] = None,
) -> dict[str, Any]:
    usdm_service = USDMService()
    ddf_study_wrapper = usdm_service.get_by_uid(
        study_uid, study_value_version=study_value_version
    )

    return ddf_study_wrapper


@router.get(
    path="/{study_uid}/m11",
    dependencies=[security, rbac.STUDY_READ],
    responses={
        403: _generic_descriptions.ERROR_403,
        200: {"content": {"text/html": {"schema": {"type": "string"}}}},
        404: _generic_descriptions.ERROR_404,
        422: {
            "model": ErrorResponse,
            "description": "Native mapping provenance does not match the selected study and version.",
        },
    },
    summary="Return an ICH M11 draft protocol preview with native source provenance.",
    description="""
State before:
- Study must exist.
- The selected study version, when provided, must exist and match the mapping source.

State after:
- no change.
- Missing authoring data and unresolved mapping issues remain visible in the draft.
- A selected metadata version does not establish protocol approval or immutability.

Possible errors:
- Invalid study-uid.
""",
)
def get_study_m11_protocol(
    request: Request,
    study_uid: Annotated[str, Path(description="The unique uid of the study.")],
    study_value_version: Annotated[
        str | None,
        Query(
            min_length=1,
            pattern=r"^\S+$",
            description="Explicit native study value version for every preview source. Omit for a current draft preview; this does not establish protocol approval or immutability.",
        ),
    ] = None,
    document_id: Annotated[str | None, Query(min_length=1)] = None,
    require_section_complete: bool = False,
    download: bool = False,
):
    report = USDMService().get_by_uid_with_report(
        study_uid, study_value_version=study_value_version
    )
    context = m11_preview_context(report, study_uid, study_value_version, document_id)
    authored = context.get("authored_document")
    if require_section_complete and (
        not authored or not authored["coverage"]["section_complete"]
    ):
        raise USDMMappingAuthorityRequired(
            "The selected draft has unresolved required narrative sections or sponsor information"
        )
    if not authored:
        _add_native_visuals(context, study_uid, study_value_version)
    with open(
        M11_TEMPLATES_DIR_PATH
        / "ICH_Step4_M11_Final_TechnicalSpecification_2025_1119.json",
        encoding="utf-8",
    ) as specification:
        context["specification_20251119"] = json.load(specification)
    with trace_block("template_rendering", "Rendering M11 draft preview"):
        response = templates.TemplateResponse(
            request=request, name="m11-template.html", context=context
        )
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    if download:
        response.headers["Content-Disposition"] = (
            'attachment; filename="protocol-draft.html"'
        )
    if authored:
        response.headers["X-Authored-Content-SHA256"] = authored["content_hash"]
    return response


def _add_native_visuals(context, study_uid, study_value_version):
    """Add generated visuals only to the native-data preview that consumes them."""
    flowchart = StudyFlowchartService().get_study_flowchart_html(
        study_uid=study_uid,
        study_value_version=study_value_version,
        layout=SoALayout.DETAILED,
    )
    body = (
        html.document_fromstring(flowchart).find("body") if flowchart.strip() else None
    )
    # The native table exporter escapes source text and emits footnote definitions
    # as siblings after each table. Preserve the whole generated body in order.
    if body is not None and body.find(".//table") is not None:
        context["study_flowchart_html_table"] = "".join(
            html.tostring(child, encoding="unicode") for child in body.iterchildren()
        )
    else:
        context["study_flowchart_html_table"] = MISSING
        context["preview_issues"].append(
            {
                "code": "M11_FLOWCHART_UNAVAILABLE",
                "message": "No flowchart table was returned for the selected source.",
            }
        )
    context["study_design_figure_svg"] = (
        StudyDesignFigureService(debug=False).get_svg_document(
            study_uid, study_value_version=study_value_version
        )
        or MISSING
    )
