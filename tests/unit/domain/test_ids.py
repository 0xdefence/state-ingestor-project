from services.domain.ids import deterministic_id
from services.domain.raw import RAW_NAMESPACE


def test_deterministic_id_is_stable_and_order_sensitive() -> None:
    assert deterministic_id(RAW_NAMESPACE, "run-1", 2, 3) == deterministic_id(
        RAW_NAMESPACE, "run-1", 2, 3
    )
    assert deterministic_id(RAW_NAMESPACE, "run-1", 2, 3) != deterministic_id(
        RAW_NAMESPACE, "run-1", 3, 2
    )
