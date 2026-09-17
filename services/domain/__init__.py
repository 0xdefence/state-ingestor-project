"""Framework-free domain primitives."""

from .fields import CandidateField, FieldState, SourceRef
from .ids import deterministic_id, new_id
from .raw import RAW_NAMESPACE, RawRecord, RawRecordKind
from .runs import RunState

__all__ = [
    "RAW_NAMESPACE",
    "CandidateField",
    "FieldState",
    "RawRecord",
    "RawRecordKind",
    "RunState",
    "SourceRef",
    "deterministic_id",
    "new_id",
]
