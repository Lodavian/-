from __future__ import annotations


class BrokerRejectedError(RuntimeError):
    """The broker definitively rejected a request before it could become live."""


class OrderUncertainError(RuntimeError):
    """The request may have reached the broker and must be reconciled, never retried."""


class GatewayUnavailableError(RuntimeError):
    """A required broker channel is unavailable or unhealthy."""
