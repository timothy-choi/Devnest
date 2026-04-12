"""Errors when binding workspace execution to a concrete node (Docker + host commands)."""


class NodeExecutionError(Exception):
    """Base class for node execution binding failures."""


class NodeExecutionBindingError(NodeExecutionError):
    """Cannot build Docker client or host command runner for the selected execution node."""
