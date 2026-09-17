import pytest

from services.domain.fields import CandidateField, FieldState


def test_known_field_requires_value() -> None:
    with pytest.raises(ValueError, match="known field requires a value"):
        CandidateField.known(None, source_refs=())


def test_non_known_field_cannot_carry_value() -> None:
    with pytest.raises(ValueError, match="cannot carry a value"):
        CandidateField(state=FieldState.ABSENT, value="x", source_refs=())
