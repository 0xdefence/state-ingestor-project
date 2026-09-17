"""UUID creation helpers for domain-owned identities."""

from uuid import UUID, uuid4, uuid5


def new_id() -> UUID:
    """Create a non-deterministic UUID for a new identity."""
    return uuid4()


def deterministic_id(namespace: UUID, *parts: object) -> UUID:
    """Create a UUIDv5 from an unambiguous, ordered sequence of parts."""
    serialized = b"".join(
        len(encoded).to_bytes(8, byteorder="big") + encoded
        for encoded in (str(part).encode("utf-8") for part in parts)
    )
    return uuid5(namespace, serialized.hex())
