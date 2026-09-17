"""Pipeline run state values."""

from enum import StrEnum


class RunState(StrEnum):
    """The primary state of a pipeline run."""

    CREATED = "created"
    INGESTED = "ingested"
    PARSING = "parsing"
    PARSED = "parsed"
    NORMALISING = "normalising"
    NORMALISED = "normalised"
    CLASSIFYING = "classifying"
    CLASSIFIED = "classified"
    LOADING = "loading"
    STAGED = "staged"


class RunChainInvariantError(ValueError):
    """Stored runs do not form one complete, unambiguous predecessor chain."""
