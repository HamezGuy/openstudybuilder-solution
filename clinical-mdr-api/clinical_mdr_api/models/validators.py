"""Reusable pydantic validator functions"""

import re
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationInfo

from clinical_mdr_api.domains._utils import get_iso_lang_data
from clinical_mdr_api.domains.enums import OdmTranslatedTextTypeEnum
from clinical_mdr_api.domains.odms.utils import EN_LANGUAGE, ENG_LANGUAGE
from common.exceptions import ValidationException

FLOAT_REGEX = "^[0-9]+\\.?[0-9]*$"


def validate_string_represents_boolean(value, info: ValidationInfo):
    """
    Validates whether a string value represents a boolean value.

    Args:
        cls: The class to which the field belongs.
        value: The value to validate.
        info: ValidationInfo

    Returns:
        str: The validated value.

    Raises:
        ValidationException: If the value does not represent a boolean value.
    """
    if not value:
        return "false"

    truthy = ("y", "yes", "t", "true", "on", "1")
    falsy = ("n", "no", "f", "false", "off", "0")

    ValidationException.raise_if(
        value.lower() not in (truthy + falsy),
        msg=f"Unsupported boolean value '{value}' for field '{info.field_name}'. Allowed values are: {truthy + falsy}.",
    )

    return value


def validate_name_only_contains_letters(value, info: ValidationInfo):
    """
    Validates whether a string value contains only letters.

    Args:
        cls: The class to which the field belongs.
        value: The value to validate.
        info: ValidationInfo

    Returns:
        str: The validated value.

    Raises:
        ValueError: If the value contains characters other than letters.
    """
    if re.search("[^a-zA-Z]", value):
        raise ValueError(
            f"Provided value '{value}' for '{info.field_name}' is invalid. Must only contain letters."
        )
    return value


def validate_regex(value, info: ValidationInfo):
    """
    Validates whether a string value is a valid regular expression.

    Args:
        cls: The class to which the field belongs.
        value: The value to validate.
        info: ValidationInfo

    Returns:
        str: The validated regular expression.

    Raises:
        ValueError: If the value is not a valid regular expression.
    """
    if value:
        try:
            re.compile(value)
            return value
        except re.error as exc:
            raise ValueError(
                f"Provided regex value '{value}' for field '{info.field_name}' is invalid."
            ) from exc
    return value


def validate_first_character_is_uppercase(value, info: ValidationInfo):
    """
    Validates whether the first character of a string value is uppercase.

    Args:
        cls: The class to which the field belongs.
        value: The value to validate.
        info: ValidationInfo

    Returns:
        str: The validated value.
    """
    if value and not value[0].isupper():
        raise ValueError(
            f"Provided value '{value}' for '{info.field_name}' is invalid. The first character must be uppercase."
        )
    return value


def validate_first_character_is_lowercase(value, info: ValidationInfo):
    """
    Validates whether the first character of a string value is lowercase.

    Args:
        cls: The class to which the field belongs.
        value: The value to validate.
        info: ValidationInfo

    Returns:
        str: The validated value.
    """
    if value and not value[0].islower():
        raise ValueError(
            f"Provided value '{value}' for '{info.field_name}' is invalid. The first character must be lowercase."
        )
    return value


def transform_to_utc(value: datetime | None, info: ValidationInfo):
    if not value:
        return None

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    try:
        return value.astimezone(timezone.utc)
    except OverflowError as exc:
        raise ValueError(
            f"Provided value '{value}' for '{info.field_name}' is invalid. {exc}"
        ) from exc


def is_language_supported(value: str):
    if not value:
        return None

    keys = ["639-3", "639-2/B", "639-2/T", "639-1"]

    for key in keys:
        try:
            # This function will throw an exception if the language isn't found
            get_iso_lang_data(query=value, return_key=key)  # type: ignore[call-overload]
            return value
        except ValidationException:
            if key == keys[-1]:
                raise

    return None


def has_english_description(translated_texts: list[Any]):
    """
    Ensures that there is at least one Translated Text with language English ('eng' or 'en') if Description(s) have been provided.

    Args:
        translated_texts (list[Any]): List of translated_texts.

    Returns:
        list[Any]: The original list if valid.

    Raises:
        ValidationException: If no English Description is found.
    """

    if not translated_texts:
        return []

    descriptions = [
        tt
        for tt in translated_texts
        if tt.text_type == OdmTranslatedTextTypeEnum.DESCRIPTION
    ]
    if not descriptions or any(
        d.language in {ENG_LANGUAGE, EN_LANGUAGE} for d in descriptions
    ):
        return translated_texts

    raise ValidationException(
        msg="A Translated Text with text_type Description and language English ('eng' or 'en') must be provided."
    )


def translated_text_uniqueness_check(translated_texts: list[Any]):
    """
    Ensures that there are no duplicate Translated Texts for the same language and text_type.

    Args:
        translated_texts (list[Any]): List of translated_texts.

    Returns:
        list[Any]: The original list if valid.

    Raises:
        ValidationException: If duplicate Translated Texts are found.
    """
    seen = set()

    for translated_text in translated_texts:
        identifier = (translated_text.text_type, translated_text.language)
        if identifier in seen:
            raise ValidationException(
                msg=f"Duplicate Translated Text found for text_type '{translated_text.text_type.value}' and language '{translated_text.language}'."
            )
        seen.add(identifier)

    return translated_texts
