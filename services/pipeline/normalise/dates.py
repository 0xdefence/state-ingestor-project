"""Exact date shapes and English month registry; no locale or fallback parser."""

import re
from datetime import date, datetime

from services.domain.fields import FieldState
from services.domain.issues import IssueCode, TransformationCode
from services.pipeline.normalise.evidence import (
    FieldEvidence,
    FieldPath,
    NormalisedField,
)

_ISO_DATE = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})")
_US_DATE = re.compile(r"([0-9]{2})/([0-9]{2})/([0-9]{4})")
_NAMED_DATE = re.compile(r"([0-9]{2})-([A-Z][a-z]{2})-([0-9]{4})")
_LOCAL_DATETIME = re.compile(
    r"([0-9]{4})/([0-9]{2})/([0-9]{2}) ([0-9]{2}):([0-9]{2}):([0-9]{2})"
)
_MONTHS = {
    month: index
    for index, month in enumerate(
        (
            "Jan",
            "Feb",
            "Mar",
            "Apr",
            "May",
            "Jun",
            "Jul",
            "Aug",
            "Sep",
            "Oct",
            "Nov",
            "Dec",
        ),
        start=1,
    )
}


def normalise_date(raw: str, field: FieldPath) -> NormalisedField[date | datetime]:
    evidence = FieldEvidence(raw, field)
    text = evidence.text
    if not text:
        return evidence.finish(FieldState.ABSENT, None)
    value: date | datetime | None = None
    try:
        if match := _ISO_DATE.fullmatch(text):
            year, month, day = map(int, match.groups())
            value = date(year, month, day)
        elif match := _US_DATE.fullmatch(text):
            month, day, year = map(int, match.groups())
            value = date(year, month, day)
        elif match := _NAMED_DATE.fullmatch(text):
            day, month_name, year = match.groups()
            if month_name in _MONTHS:
                value = date(int(year), _MONTHS[month_name], int(day))
        elif match := _LOCAL_DATETIME.fullmatch(text):
            year, month, day, hour, minute, second = map(int, match.groups())
            value = datetime(year, month, day, hour, minute, second)
    except ValueError:
        value = None
    if value is None:
        evidence.issue(IssueCode.INVALID_DATE, "Unsupported or invalid date")
        return evidence.finish(FieldState.UNRESOLVED, None)
    evidence.transform(TransformationCode.PARSE_DATE, text, value)
    return evidence.finish(FieldState.KNOWN, value)
