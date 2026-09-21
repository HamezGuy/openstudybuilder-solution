"""Source-supported manual timing, separate from the fixed study timeline."""

import json
import math
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    TypeAdapter,
    model_validator,
)

from common.config import settings
from common.utils import VisitClass, VisitSubclass, VisitTimingMode


class _SourceTiming(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ManualDateTiming(_SourceTiming):
    kind: Literal["manual_date"]
    repeating: StrictBool


class EventRelativeTiming(_SourceTiming):
    kind: Literal["event_relative"]
    anchor_visit_uid: Annotated[
        str, Field(min_length=1, max_length=255, pattern=r"^\S+$")
    ]
    nominal_offset_days: Annotated[
        StrictInt, Field(gt=-settings.max_int_neo4j, lt=settings.max_int_neo4j)
    ]


class CalendarRepeatTiming(_SourceTiming):
    kind: Literal["calendar_repeat"]
    anchor_visit_uid: Annotated[
        str, Field(min_length=1, max_length=255, pattern=r"^\S+$")
    ]
    interval_months: Annotated[StrictInt, Field(gt=0, lt=settings.max_int_neo4j)]
    first_occurrence: Annotated[StrictInt, Field(gt=0, lt=settings.max_int_neo4j)]
    last_occurrence: Annotated[StrictInt, Field(gt=0, lt=settings.max_int_neo4j)]

    @model_validator(mode="after")
    def ordered_occurrences(self) -> Self:
        if self.last_occurrence < self.first_occurrence:
            raise ValueError("last_occurrence must be at least first_occurrence")
        if self.last_occurrence * self.interval_months >= settings.max_int_neo4j:
            raise ValueError("Calendar recurrence exceeds the supported integer range")
        return self


UntimedVisitTiming = Annotated[
    ManualDateTiming | EventRelativeTiming | CalendarRepeatTiming,
    Field(discriminator="kind"),
]
_TIMING_ADAPTER = TypeAdapter(UntimedVisitTiming)


def parse_untimed_timing(value) -> UntimedVisitTiming:
    """Validate native JSON property readback as strictly as ordinary API input."""
    if isinstance(value, str):
        value = json.loads(value)
    return _TIMING_ADAPTER.validate_python(value)


def timing_is_repeating(value) -> bool:
    timing = parse_untimed_timing(value)
    return isinstance(timing, CalendarRepeatTiming) or (
        isinstance(timing, ManualDateTiming) and timing.repeating
    )


class VisitTimingInput(BaseModel):
    timing_mode: VisitTimingMode = VisitTimingMode.STANDARD
    untimed_timing: UntimedVisitTiming | None = None

    @model_validator(mode="before")
    @classmethod
    def absent_untimed_windows(cls, data):
        if isinstance(data, dict) and data.get("timing_mode") in (
            VisitTimingMode.UNTIMED,
            VisitTimingMode.UNTIMED.value,
        ):
            data = dict(data)
            # Omitted bounds are unknown in this explicit mode. Preserve the
            # legacy defaults for every existing standard visit request.
            data.setdefault("min_visit_window_value", None)
            data.setdefault("max_visit_window_value", None)
            for field in ("visit_number", "unique_visit_number"):
                if isinstance(data.get(field), bool):
                    raise ValueError(f"{field} must be a number, not a boolean")
        return data

    @model_validator(mode="after")
    def validate_timing_mode(self) -> Self:
        if self.timing_mode != VisitTimingMode.UNTIMED:
            if self.untimed_timing is not None:
                raise ValueError("untimed_timing requires explicit timing_mode UNTIMED")
            if self.visit_contact_mode is None:
                raise ValueError("visit_contact_mode is required for STANDARD visits")
            return self
        if self.visit_class != VisitClass.MANUALLY_DEFINED_VISIT:
            raise ValueError("UNTIMED requires MANUALLY_DEFINED_VISIT")
        if self.untimed_timing is None:
            raise ValueError("untimed_timing is required for UNTIMED visits")
        for field in ("visit_name", "visit_short_name"):
            value = getattr(self, field, None)
            if value is None or not value.strip():
                raise ValueError(f"{field} is required for UNTIMED visits")
        for field in ("visit_number", "unique_visit_number"):
            value = getattr(self, field, None)
            if value is None or not math.isfinite(value):
                raise ValueError(f"{field} must be provided and finite")
        for field in (
            "time_value",
            "time_unit_uid",
            "time_reference",
            "min_visit_window_value",
            "max_visit_window_value",
            "visit_window_unit_uid",
            "visit_sublabel_reference",
            "repeating_frequency_uid",
        ):
            if getattr(self, field, None) is not None:
                raise ValueError(f"{field} must be null for UNTIMED visits")
        if self.is_global_anchor_visit:
            raise ValueError("UNTIMED visits cannot be fixed global anchors")
        if self.visit_subclass not in (None, VisitSubclass.SINGLE_VISIT):
            raise ValueError("UNTIMED repetition belongs in untimed_timing")
        return self
