"""Closed, lossless JSON representation for typed immutable derived evidence.

No binary float, datetime-to-date coercion, unordered provenance, or dynamic class
imports are permitted. The tags are a storage format, not a domain dictionary API.
"""

from collections.abc import Callable
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import cast
from uuid import UUID

from services.domain.candidates import (
    CustomerCandidate,
    EntityType,
    Money,
    OrderCandidate,
    ProductCandidate,
    RejectedCandidateShell,
)
from services.domain.fields import CandidateField, FieldState, SourceRef
from services.domain.issues import ReviewReason

_CLASSES: dict[str, Callable[..., object]] = {
    cls.__name__: cls
    for cls in (
        CustomerCandidate,
        ProductCandidate,
        OrderCandidate,
        RejectedCandidateShell,
        Money,
        SourceRef,
        CandidateField,
        ReviewReason,
    )
}
_ENUMS: dict[str, type[StrEnum]] = {
    cls.__name__: cls for cls in (EntityType, FieldState)
}


def encode(value: object) -> object:
    if isinstance(value, StrEnum):
        return {"$enum": type(value).__name__, "value": str(value)}
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, Decimal):
        return {"$decimal": str(value)}
    if isinstance(value, datetime):
        return {"$datetime": value.isoformat()}
    if isinstance(value, date):
        return {"$date": value.isoformat()}
    if isinstance(value, UUID):
        return {"$uuid": str(value)}
    if isinstance(value, tuple):
        return [encode(item) for item in cast(tuple[object, ...], value)]
    if is_dataclass(value) and not isinstance(value, type):
        if type(value).__name__ not in _CLASSES:
            raise ValueError("unregistered evidence type")
        result: dict[str, object] = {"$type": type(value).__name__}
        for item in fields(value):
            field_value: object = getattr(value, item.name)
            result[item.name] = (
                (str(field_value) if field_value is not None else None)
                if item.name == "entity_type"
                else encode(field_value)
            )
        return result
    raise ValueError(f"unsupported evidence value: {type(value).__name__}")


def decode(value: object) -> object:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, list):
        return tuple(decode(item) for item in cast(list[object], value))
    if not isinstance(value, dict):
        raise ValueError("unsupported stored evidence")
    data = cast(dict[str, object], value)
    for tag, parser in (
        ("$decimal", Decimal),
        ("$datetime", datetime.fromisoformat),
        ("$date", date.fromisoformat),
        ("$uuid", UUID),
    ):
        if tag in data:
            item = data[tag]
            if not isinstance(item, str):
                raise ValueError("malformed scalar evidence")
            return parser(item)
    if "$enum" in data:
        name, member = data["$enum"], data.get("value")
        if not isinstance(name, str) or not isinstance(member, str):
            raise ValueError("malformed enum evidence")
        return _ENUMS[name](member)
    name = data.get("$type")
    if not isinstance(name, str) or name not in _CLASSES:
        raise ValueError("unregistered stored evidence type")
    result = _CLASSES[name](
        **{
            key: decode(item)
            for key, item in data.items()
            if key not in ("$type", "entity_type")
        }
    )
    if (
        isinstance(
            result,
            (
                CustomerCandidate,
                ProductCandidate,
                OrderCandidate,
                RejectedCandidateShell,
            ),
        )
        and data.get("entity_type") != result.entity_type
    ):
        raise ValueError("stored candidate discriminator mismatch")
    return result


def object_json(value: object) -> dict[str, object]:
    result = encode(value)
    if not isinstance(result, dict):
        raise ValueError("expected structured evidence object")
    return cast(dict[str, object], result)


def array_json(value: tuple[object, ...]) -> list[object]:
    return [encode(item) for item in value]


def read_as[T](value: object, expected: type[T]) -> T:
    result = decode(value)
    if not isinstance(result, expected):
        raise ValueError("stored evidence type mismatch")
    return result


def read_tuple[T](value: list[object], expected: type[T]) -> tuple[T, ...]:
    return tuple(read_as(item, expected) for item in value)
