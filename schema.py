"""Shared data model for status items.

The canonical definition lives in `extraction.py`, which is deliberately
self-contained and runnable on its own. This module re-exports it so
`from schema import StatusItem` keeps working.

NOTE: the original scaffold defined a different StatusItem here
(sender/category/summary/detail/action_items). That shape is gone — the pipeline
now uses source/topic/status/owner/blocker/confidence.
"""

from dataclasses import asdict

from extraction import (
    CONFIDENCES,
    FIELDS,
    RESPONSE_SCHEMA,
    SOURCES,
    STATUSES,
    StatusItem,
)

__all__ = [
    "StatusItem",
    "SOURCES",
    "STATUSES",
    "CONFIDENCES",
    "FIELDS",
    "RESPONSE_SCHEMA",
    "to_dict",
]


def to_dict(item: StatusItem) -> dict:
    return asdict(item)
