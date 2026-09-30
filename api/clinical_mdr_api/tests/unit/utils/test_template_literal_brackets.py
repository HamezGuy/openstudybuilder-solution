"""Explicit numeric bracket literals survive native template and instance paths."""

import html
import importlib

import pytest

from clinical_mdr_api.domains.libraries.object import ParametrizedTemplateVO
from clinical_mdr_api.domains.libraries.parameter_term import (
    ParameterTermEntryVO,
    SimpleParameterTermVO,
)
from clinical_mdr_api.domains.syntax_templates.template import TemplateVO
from clinical_mdr_api.models.syntax_templates.criteria_template import (
    CriteriaTemplateCreateInput,
    CriteriaTemplateEditInput,
)
from clinical_mdr_api.models.utils import sanitize_html, sanitize_template_html
from clinical_mdr_api.utils import convert_to_plain, extract_parameters, strip_html
from common.exceptions import ValidationException


def literal_html(source):
    return "<p>" + html.escape(source).replace("[", "&#91;").replace(
        "]", "&#93;"
    ) + "</p>"


def instantiate(name, parameters=None):
    return ParametrizedTemplateVO.from_name_and_parameter_terms(
        name=name,
        template_uid="test-template",
        parameter_terms=[] if parameters is None else parameters,
        library_name="test",
    )


@pytest.mark.parametrize("source", [
    "Prior surgery [keratoplasty]",
    "[] [NA] [a[b]] [x] [",
    "[lowercase] literal at start",
    "PCR < 72 hours OR results > 24 hours & dose <= 5",
    "Literal &#91; &lbrack; &amp; &lt; plus [literal]",
    "__OSB_LITERAL_BRACKET_91__ __OSB_LITERAL_BRACKET__93__ [literal]",
])
def test_literal_source_roundtrip_and_repeated_sanitization(source):
    encoded = literal_html(source)
    request = CriteriaTemplateCreateInput(name=encoded, type_uid="test-type")
    for _ in range(3):
        request = CriteriaTemplateCreateInput(name=request.name, type_uid="test-type")
    edited = CriteriaTemplateEditInput(name=request.name, change_description="roundtrip")
    assert edited.name == request.name
    template = TemplateVO.from_input_values_2(
        request.name, parameter_name_exists_callback=lambda _: False
    )
    assert template.name_plain == source
    assert template.parameter_names == []
    instance = instantiate(template.name)
    assert strip_html(instance.expanded_template_value) == source
    assert instance.expanded_plain_template_value == source


@pytest.mark.parametrize("encoded", [
    "&#91;literal&#93;", "&#091;literal&#093;", "&#x5b;literal&#x5d;",
    "&#X05B;literal&#X05D;",
])
def test_decimal_and_hex_literals_are_canonicalized(encoded):
    sanitized = sanitize_template_html(encoded)
    assert sanitized == "&#91;literal&#93;"
    assert sanitize_template_html(sanitized) == sanitized
    assert extract_parameters(sanitized) == []
    assert convert_to_plain(sanitized) == "[literal]"


@pytest.mark.parametrize("prefix", ["", "&#91;literal&#93; then "])
def test_raw_parameter_dsl_is_still_validated_and_instantiated(prefix):
    name = sanitize_template_html("<p>" + prefix + "[TextValue] and &#91;NA&#93;</p>")
    seen = []
    template = TemplateVO.from_input_values_2(
        name,
        parameter_name_exists_callback=lambda parameter: seen.append(parameter)
        or parameter == "TextValue",
    )
    assert seen == ["TextValue"]
    values = [ParameterTermEntryVO(
        parameters=[SimpleParameterTermVO(uid="value1", value="sample", labels=[])],
        conjunction="and", parameter_name="TextValue", labels=[],
    )]
    expected = "Sample and [NA]" if not prefix else "[literal] then sample and [NA]"
    assert instantiate(template.name, values).expanded_plain_template_value == expected
    with pytest.raises(ValidationException, match="Unknown parameter name"):
        TemplateVO.from_input_values_2(name, parameter_name_exists_callback=lambda _: False)
    with pytest.raises(ValidationException, match="syntax incorrect"):
        TemplateVO.from_input_values_2("[nested[raw]]", lambda _: True)


@pytest.mark.parametrize("stem,class_prefix", [
    ("activity_instruction", "ActivityInstruction"), ("criteria", "Criteria"),
    ("endpoint", "Endpoint"), ("footnote", "Footnote"),
    ("objective", "Objective"), ("timeframe", "Timeframe"),
])
def test_only_declared_template_name_fields_opt_in(stem, class_prefix):
    module = importlib.import_module(
        f"clinical_mdr_api.models.syntax_templates.{stem}_template"
    )
    for suffix in ["PreValidateInput", "CreateInput", "EditInput"]:
        model = getattr(module, class_prefix + "Template" + suffix)
        for name, field in model.model_fields.items():
            opted = (field.json_schema_extra or {}).get("preserve_literal_brackets", False)
            assert opted == (name == "name")
    prevalidate = getattr(module, class_prefix + "TemplatePreValidateInput")
    value = prevalidate(name="&#91;literal&#93;")
    assert value.name == "&#91;literal&#93;"
    assert "preserve_literal_brackets" not in str(prevalidate.model_json_schema())


def test_sanitizer_still_removes_unsafe_markup_and_avoids_decoded_marker_collision():
    source = "<p>__OSB_LITERAL_<b></b>BRACKET_91__ &#91;literal&#93;</p>"
    sanitized = sanitize_template_html(source)
    assert strip_html(sanitized) == "__OSB_LITERAL_BRACKET_91__ [literal]"
    malicious = '<script>alert(1)</script><p onclick="bad()">&#91;x&#93;</p>'
    safe = sanitize_template_html(malicious)
    assert safe == "<p>&#91;x&#93;</p>"
    assert sanitize_html("<p>&#91;raw&#93;</p>") == "<p>[raw]</p>"
    assert convert_to_plain("<p>[parameter] &amp;#91;literal entity text&amp;#93;</p>") == (
        "parameter &#91;literal entity text&#93;"
    )
