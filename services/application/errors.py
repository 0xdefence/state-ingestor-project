"""Explicit, transport-independent failures safe for operator-facing adapters."""


class ApplicationValidationError(ValueError):
    """A known invalid request with an intentionally safe explanation."""


class ResourceNotFoundError(LookupError):
    """A requested resource was absent at its repository boundary."""
