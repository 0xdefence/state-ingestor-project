"""Display text, contextual integers, and separately derived matching keys."""

import re
import unicodedata

from services.domain.fields import FieldState
from services.domain.issues import IssueCode, TransformationCode
from services.pipeline.normalise.evidence import (
    FieldEvidence,
    FieldPath,
    NormalisedField,
)

_INTEGER = re.compile(r"-?[0-9]+")


def normalise_text(raw: str, field: FieldPath) -> NormalisedField[str]:
    evidence = FieldEvidence(raw, field)
    if not evidence.text or (field.path == "customer.email" and evidence.text == "-"):
        return evidence.finish(FieldState.ABSENT, None)
    return evidence.finish(FieldState.KNOWN, evidence.text)


def normalise_integer(raw: str, field: FieldPath) -> NormalisedField[int]:
    evidence = FieldEvidence(raw, field)
    text = evidence.text
    if not text or (field.path == "order.quantity" and text == "None"):
        return evidence.finish(FieldState.ABSENT, None)
    if _INTEGER.fullmatch(text):
        try:
            value = int(text)
        except ValueError:
            pass
        else:
            evidence.transform(TransformationCode.PARSE_INTEGER, text, value)
            return evidence.finish(FieldState.KNOWN, value)
    evidence.issue(IssueCode.INVALID_INTEGER, "Expected an integer")
    return evidence.finish(FieldState.UNRESOLVED, None)


def match_key(raw: str) -> str:
    normalized = unicodedata.normalize("NFKC", raw)
    without_symbols = "".join(
        character for character in normalized if unicodedata.category(character) != "So"
    )
    return " ".join(without_symbols.split()).casefold()
