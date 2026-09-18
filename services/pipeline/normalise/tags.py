"""Ordered tag representation; vocabulary validation belongs to classification."""

import re

from services.domain.fields import FieldState
from services.domain.issues import IssueCode, TransformationCode
from services.pipeline.normalise.evidence import (
    FieldEvidence,
    FieldPath,
    NormalisedField,
)

TAG_FORMAT_DESCRIPTION = "Letter-only tags separated by |; blank or N/A means no tags"

_TAGS = re.compile(r"[A-Za-z]+(?:\|[A-Za-z]+)*")


def normalise_tags(raw: str, field: FieldPath) -> NormalisedField[tuple[str, ...]]:
    evidence = FieldEvidence(raw, field)
    text = evidence.text
    if not text or text == "N/A":
        value: tuple[str, ...] = ()
    elif _TAGS.fullmatch(text):
        value = tuple(tag.lower() for tag in text.split("|"))
    else:
        evidence.issue(
            IssueCode.INVALID_TAGS, "Expected pipe-separated letter-only tags"
        )
        return evidence.finish(FieldState.UNRESOLVED, None)
    evidence.transform(TransformationCode.PARSE_TAGS, text, value)
    return evidence.finish(FieldState.KNOWN, value)
