"""Ownership and membership lookup over data/world.json. Pure Python, no LLM.

The only thing answer.py's abstention and alerts.py's silence detection both need:
"who owns this" and "who is in this channel" are real lookups against declared ground
truth, never an LLM guess. Reuses src/index.py's alias table for entity resolution
instead of building a second one.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.index import text_tokens


class WorldError(Exception):
    pass


class World:
    """Loads data/world.json and answers ownership/membership/identity questions."""

    def __init__(self, world: Dict[str, Any]):
        self.raw = world
        self.employees: Dict[str, dict] = {e["employee_id"]: e for e in world.get("employees", [])}
        self.projects: Dict[str, dict] = {p["project_id"]: p for p in world.get("projects", [])}
        self.services: Dict[str, dict] = {s["service_id"]: s for s in world.get("services", [])}
        self.clients: Dict[str, dict] = {c["client_id"]: c for c in world.get("clients", [])}
        self.channels: Dict[str, dict] = {c["channel_id"]: c for c in world.get("channels", [])}
        self.external: Dict[str, dict] = {x["entity_id"]: x for x in world.get("external", [])}

        # alias/token -> entity_id, built the same way Index._build_alias_table does,
        # so "aegis", "svc-bill", "acme", "@priya", "priya.raman@company.com" all resolve.
        self._alias_index: Dict[str, str] = {}
        for group in (self.projects, self.services, self.clients, self.employees):
            for entity_id, entity in group.items():
                terms = list(entity.get("aliases", []))
                if entity.get("name"):
                    terms.append(entity["name"])
                if entity.get("email"):
                    terms.append(entity["email"])
                if entity.get("slack_handle"):
                    terms.append(entity["slack_handle"])
                terms.append(entity_id)
                for term in terms:
                    for token in text_tokens(str(term)) or [str(term).lower().strip()]:
                        self._alias_index.setdefault(token, entity_id)
                    self._alias_index.setdefault(str(term).lower().strip(), entity_id)

    @classmethod
    def from_path(cls, path: Path | str = "data/world.json") -> "World":
        path = Path(path)
        if not path.exists():
            raise WorldError(f"world file {path!s} does not exist")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise WorldError(f"world file {path!s} is not valid JSON: {exc}") from exc
        return cls(data)

    # ------------------------------------------------------------------ identity

    def person(self, id_or_alias: Optional[str]) -> Optional[dict]:
        """Resolve a canonical id, name, alias, email, or handle to an employee record."""
        if not id_or_alias:
            return None
        if id_or_alias in self.employees:
            return self.employees[id_or_alias]
        key = id_or_alias.strip().lower().lstrip("@")
        entity_id = self._alias_index.get(key)
        if entity_id in self.employees:
            return self.employees[entity_id]
        return None

    def display(self, employee_id: Optional[str]) -> str:
        """Human-readable name for an employee id, or the id itself if unknown."""
        if not employee_id:
            return "someone unnamed"
        emp = self.employees.get(employee_id)
        if emp:
            return f"{emp['name']} ({emp.get('role', 'role unknown')})"
        return employee_id

    # ------------------------------------------------------------------ ownership

    def owner_of(self, entity_id: Optional[str]) -> Optional[str]:
        """Return the employee_id who owns a project, service, or client. None if unowned/unknown."""
        if not entity_id:
            return None
        if entity_id in self.projects:
            return self.projects[entity_id].get("owner_id")
        if entity_id in self.services:
            return self.services[entity_id].get("owner_id")
        if entity_id in self.clients:
            return self.clients[entity_id].get("csm_id")
        return None

    def resolve_entity(self, text: str) -> Optional[str]:
        """Best-effort: map free text (a subject, a project name) to a known entity id."""
        key = text.strip().lower()
        if key in self._alias_index:
            return self._alias_index[key]
        for token in text_tokens(text):
            if token in self._alias_index:
                return self._alias_index[token]
        return None

    def who_would_know(self, entity_ids_or_text: List[str]) -> List[dict]:
        """Given entity ids (or free text naming a project/service/client), return the
        employee record(s) who own or are accountable for them. Used by abstention and
        alerts — never fabricated, always traced back to world.json ownership.
        """
        seen: List[str] = []
        result: List[dict] = []
        for candidate in entity_ids_or_text:
            entity_id = candidate if (
                candidate in self.projects or candidate in self.services or candidate in self.clients
            ) else self.resolve_entity(candidate)
            owner_id = self.owner_of(entity_id) if entity_id else None
            if owner_id and owner_id not in seen:
                seen.append(owner_id)
                result.append(self.employees.get(owner_id, {"employee_id": owner_id, "name": owner_id}))
        return result

    # ---------------------------------------------------------------- membership

    def channel_members(self, channel_id: Optional[str]) -> List[str]:
        if not channel_id:
            return []
        channel = self.channels.get(channel_id)
        return list(channel.get("members", [])) if channel else []

    def is_member(self, employee_id: str, channel_id: str) -> bool:
        return employee_id in self.channel_members(channel_id)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Query data/world.json ownership and membership.")
    parser.add_argument("--world", default="data/world.json")
    parser.add_argument("--who-would-know", metavar="ENTITY", help="entity id or free text (project/service/client)")
    parser.add_argument("--owner-of", metavar="ENTITY_ID")
    parser.add_argument("--person", metavar="ID_OR_ALIAS")
    parser.add_argument("--channel-members", metavar="CHANNEL_ID")
    args = parser.parse_args(argv)

    try:
        world = World.from_path(args.world)
    except WorldError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1

    if args.who_would_know:
        hits = world.who_would_know([args.who_would_know])
        if not hits:
            print(f"no owner found for {args.who_would_know!r}")
            return 0
        for emp in hits:
            print(f"{emp['employee_id']}: {world.display(emp['employee_id'])}")
    elif args.owner_of:
        owner = world.owner_of(args.owner_of)
        print(owner or f"no owner recorded for {args.owner_of!r}")
    elif args.person:
        emp = world.person(args.person)
        print(json.dumps(emp, indent=2) if emp else f"no match for {args.person!r}")
    elif args.channel_members:
        print(", ".join(world.channel_members(args.channel_members)) or "(no members / unknown channel)")
    else:
        parser.error("pass one of --who-would-know / --owner-of / --person / --channel-members")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
