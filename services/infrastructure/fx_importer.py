"""Verify exact pinned ECB bytes before opening the offline import transaction."""

import csv
import json
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
from io import StringIO
from pathlib import Path
from typing import cast
from urllib.parse import urlparse
from uuid import NAMESPACE_URL

from services.application.process import UnitOfWorkFactory
from services.domain.fx import FxRate, FxSnapshot
from services.domain.ids import deterministic_id


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("FX manifest must contain objects")
    return cast(dict[str, object], value)


def _text(data: dict[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"FX manifest requires {key}")
    return value


def read_fx_snapshot(fixture: Path, manifest: Path) -> FxSnapshot:
    manifest_bytes = manifest.read_bytes()
    data = _object(json.loads(manifest_bytes))
    content = fixture.read_bytes()
    if sha256(content).hexdigest() != _text(data, "sha256"):
        raise ValueError("FX fixture hash mismatch")
    if type(data.get("byte_size")) is not int or data["byte_size"] != len(content):
        raise ValueError("FX fixture byte size mismatch")
    if (
        type(data.get("version")) is not int
        or data.get("version") != 1
        or data.get("source") != "ECB"
        or data.get("format") != "ECB-SDMX-CSV"
        or data.get("encoding") != "utf-8"
        or data.get("base_currency") != "EUR"
    ):
        raise ValueError("unsupported FX manifest format")
    source_url = _text(data, "source_url")
    url = urlparse(source_url)
    if url.scheme != "https" or url.hostname != "data-api.ecb.europa.eu":
        raise ValueError("FX manifest requires official ECB source provenance")
    date.fromisoformat(_text(data, "retrieved_on"))
    _text(data, "provenance")
    effective_at = datetime.fromisoformat(_text(data, "effective_at"))
    rates: list[FxRate] = []
    for row in csv.DictReader(StringIO(content.decode("utf-8"), newline="")):
        currency = row["CURRENCY"]
        if (
            row["KEY"] != f"EXR.D.{currency}.EUR.SP00.A"
            or row["FREQ"] != "D"
            or row["CURRENCY_DENOM"] != "EUR"
            or row["EXR_TYPE"] != "SP00"
            or row["EXR_SUFFIX"] != "A"
            or row["OBS_STATUS"] != "A"
            or row["UNIT"] != currency
            or row["UNIT_MULT"] != "0"
        ):
            raise ValueError("unsupported ECB observation")
        rates.append(
            FxRate(
                currency,
                date.fromisoformat(row["TIME_PERIOD"]),
                Decimal(row["OBS_VALUE"]),
                source_url,
            )
        )
    if not rates:
        raise ValueError("FX coverage must not be empty")
    coverage = _object(data.get("coverage"))
    if (
        coverage.get("start") != min(r.publication_date for r in rates).isoformat()
        or coverage.get("end") != max(r.publication_date for r in rates).isoformat()
        or coverage.get("currencies") != sorted({r.currency for r in rates})
        or type(coverage.get("rate_count")) is not int
        or coverage.get("rate_count") != len(rates)
        or max(r.publication_date for r in rates) > effective_at.date()
    ):
        raise ValueError("FX manifest coverage mismatch")
    manifest_hash = sha256(manifest_bytes).hexdigest()
    return FxSnapshot(
        deterministic_id(NAMESPACE_URL, "ecb-snapshot-v1", manifest_hash),
        manifest_hash,
        effective_at,
        "ECB",
        source_url,
        tuple(sorted(rates, key=lambda r: (r.publication_date, r.currency))),
    )


def import_fx_snapshot(
    fixture: Path,
    manifest: Path,
    uow_factory: UnitOfWorkFactory,
) -> FxSnapshot:
    snapshot = read_fx_snapshot(fixture, manifest)
    with uow_factory() as work:
        work.fx.add(snapshot)
        work.commit()
    return snapshot
