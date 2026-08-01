from dataclasses import dataclass, asdict
from typing import List, Dict, Any


@dataclass
class StatusItem:
    sender: str
    source: str
    timestamp: str
    category: str
    summary: str
    detail: str
    action_items: List[str]


STATUS_ITEM_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "sender": {"type": "string"},
        "source": {"type": "string"},
        "timestamp": {"type": "string"},
        "category": {"type": "string"},
        "summary": {"type": "string"},
        "detail": {"type": "string"},
        "action_items": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["sender", "source", "timestamp", "category", "summary", "detail", "action_items"],
}


def to_dict(item: StatusItem) -> Dict[str, Any]:
    return asdict(item)
