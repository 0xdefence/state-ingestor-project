"""Presentation of registered normalisation domains; no browser policy copy."""

from services.pipeline.normalise.status import supported_statuses
from services.pipeline.normalise.tags import TAG_FORMAT_DESCRIPTION


def expected_domain(code: str, field_path: str) -> str | None:
    if code == "INVALID_STATUS":
        values = supported_statuses(field_path)
        return "One of: " + ", ".join(values) if values else None
    if code == "INVALID_TAGS":
        return TAG_FORMAT_DESCRIPTION
    return {
        "INVALID_INTEGER": "A whole number",
        "INVALID_MONEY": "A money amount",
        "AMBIGUOUS_MONEY": "An unambiguous money amount",
        "INVALID_DATE": "A valid date",
        "INVALID_EMAIL": "An email address",
    }.get(code)
