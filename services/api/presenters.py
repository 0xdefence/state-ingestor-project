"""JSON boundary: exact decimals, UTC instants and explicit local display."""

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from typing import cast
from uuid import UUID
from zoneinfo import ZoneInfo

from services.application.queries import (
    AllFilesScope,
    CurrentFileScope,
    SelectedFilesScope,
)
from services.application.views import ObjectView, code_label

present_code = code_label


def present_instant(instant: datetime, zone: ZoneInfo) -> dict[str, str]:
    if instant.tzinfo is None:
        raise ValueError("instant requires a timezone")
    local = instant.astimezone(zone)
    return {
        "instant": instant.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "display": f"{local.day} {local:%B %Y, %H:%M %Z}",
        "timezone": zone.key,
    }


def present(value: object, zone: ZoneInfo = ZoneInfo("Europe/London")) -> object:
    if isinstance(value, ObjectView):
        return {key: present(item, zone) for key, item in value.fields}
    if isinstance(value, CurrentFileScope):
        return {"kind": "current", "run_ids": [str(value.run_id)]}
    if isinstance(value, SelectedFilesScope):
        return {"kind": "selected", "run_ids": [str(i) for i in value.run_ids]}
    if isinstance(value, AllFilesScope):
        return {"kind": "all", "run_ids": []}
    if isinstance(value, datetime):
        return (
            value.isoformat() if value.tzinfo is None else present_instant(value, zone)
        )
    if isinstance(value, (UUID, Decimal, date)):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: present(getattr(value, f.name), zone) for f in fields(value)}
    if isinstance(value, Mapping):
        return {
            str(k): present(v, zone)
            for k, v in cast(Mapping[object, object], value).items()
        }
    if isinstance(value, (tuple, list)):
        return [present(v, zone) for v in cast(tuple[object, ...], value)]
    return value
