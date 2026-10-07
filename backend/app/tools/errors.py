"""Errors shared by all investigation tools."""


class NamespaceNotAllowedError(PermissionError):
    """The requested namespace is outside the configured allow-list."""


class InvalidTargetError(ValueError):
    """A namespace/service name is not a valid Kubernetes name (and could
    otherwise be used to inject into a query)."""
