import json
import re
from collections import defaultdict
from math import log
from pathlib import Path
from typing import Any

ARTIFACT_FIELDS = {
    "artifact_id",
    "source",
    "container_id",
    "parent_id",
    "sender_id",
    "recipients",
    "ts",
    "text",
    "meta",
    "raw",
}

ALIAS_PATTERN = re.compile(r"[A-Za-z]{2,4}-\d+|PR\s*#?\d+|\b[a-z]{1,4}\d+\b|\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
TOKEN_PATTERN = re.compile(r"\b[a-z0-9][a-z0-9'-]*\b")
STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "on", "in", "at", "to", "for", "of", "and", "or", "but", "if", "then",
    "so", "we", "i", "you", "he", "she", "it", "they", "them", "this",
    "that", "these", "those", "what", "why", "how", "when", "who", "which",
    "with", "from", "by", "as", "not", "no", "do", "does", "did", "have",
    "has", "had", "can", "could", "will", "would", "about", "our", "us",
    "my", "me", "up", "out", "any", "some", "all", "there", "here",
    "just", "now", "still", "again", "s", "t", "re",
}


def normalize_token(token: str) -> str:
    return token.lower().strip("'\".,:;()[]{}")


def extract_aliases(text: str) -> set[str]:
    aliases = {m.group(0).strip() for m in ALIAS_PATTERN.finditer(text)}
    return {alias.lower() for alias in aliases}


def text_tokens(text: str) -> list[str]:
    tokens = [normalize_token(t) for t in TOKEN_PATTERN.findall(text.lower())]
    return [t for t in tokens if t and t not in STOPWORDS]


def id_tokens(entity_id: str) -> list[str]:
    return [normalize_token(token) for token in re.findall(r"[A-Za-z0-9]+", entity_id.lower())]


class IndexError(Exception):
    pass


class Index:
    """Builds retrieval indexes from bootstrap artifacts and world entities."""

    def __init__(self, artifacts: list[dict], world: dict):
        self.artifacts = {artifact["artifact_id"]: artifact for artifact in artifacts}
        self.alias_table: dict[str, set[str]] = defaultdict(set)
        self.alias_key_index: dict[str, set[str]] = defaultdict(set)
        self.entity_index: dict[str, set[str]] = defaultdict(set)
        self.artifact_entities: dict[str, set[str]] = {}
        self.token_index: dict[str, set[str]] = defaultdict(set)
        self.thread_index: dict[str, list[str]] = defaultdict(list)
        self.parent_index: dict[str, str] = {}
        self.time_index: list[str] = []
        self.doc_freq: dict[str, int] = defaultdict(int)
        self._build(world)

    @classmethod
    def from_path(cls, data_dir: Path) -> "Index":
        artifacts = []
        for jsonl in sorted((data_dir / "corpus").glob("*.jsonl")):
            for line in jsonl.read_text().splitlines():
                if not line.strip():
                    continue
                artifact = json.loads(line)
                if not ARTIFACT_FIELDS.issubset(artifact.keys()):
                    raise IndexError(f"artifact missing required fields: {artifact.get('artifact_id')}")
                artifacts.append(artifact)
        world = json.loads((data_dir / "world.json").read_text())
        return cls(artifacts, world)

    def _build(self, world: dict) -> None:
        self._build_alias_table(world)
        for artifact_id, artifact in self.artifacts.items():
            self._index_artifact(artifact_id, artifact)
        self._finalize_time_index()

    def _build_alias_table(self, world: dict) -> None:
        for group in ("projects", "services", "clients", "employees", "external"):
            for entity in world.get(group, []):
                alias_terms = []
                if group == "employees":
                    alias_terms.extend(entity.get("aliases", []))
                    alias_terms.append(entity.get("name", ""))
                    if entity.get("email"):
                        alias_terms.append(entity["email"])
                    if entity.get("slack_handle"):
                        alias_terms.append(entity["slack_handle"])
                    if entity.get("team"):
                        alias_terms.append(entity["team"])
                else:
                    alias_terms.extend(entity.get("aliases", []))
                    alias_terms.append(entity.get("name", ""))
                entity_id = entity.get("project_id") or entity.get("service_id") or entity.get("client_id") or entity.get("employee_id")
                if not entity_id:
                    continue
                for alias in alias_terms:
                    for token in text_tokens(str(alias)):
                        self.alias_table[token].add(entity_id)
                self.alias_table[entity_id.lower()].add(entity_id)

    def _index_artifact(self, artifact_id: str, artifact: dict) -> None:
        text = artifact.get("text", "")
        self._index_tokens(artifact_id, text)
        entities = self._artifact_entities(artifact)
        self.artifact_entities[artifact_id] = entities
        for entity_id in entities:
            self.entity_index[entity_id].add(artifact_id)
        for alias_key in self._artifact_aliases(artifact):
            self.alias_key_index[alias_key].add(artifact_id)
        self._index_thread(artifact_id, artifact)
        self._index_time(artifact_id, artifact)

    def _artifact_aliases(self, artifact: dict) -> set[str]:
        aliases: set[str] = set()
        for source in [artifact.get("text", ""), artifact.get("container_id", ""), artifact.get("parent_id", ""), artifact.get("sender_id", "")]:
            aliases.update(extract_aliases(str(source)))
        for recipient in artifact.get("recipients", []):
            aliases.update(extract_aliases(str(recipient)))
        for token in text_tokens(str(artifact.get("container_id", ""))):
            aliases.add(token)
        for token in text_tokens(str(artifact.get("parent_id", ""))):
            aliases.add(token)
        return aliases

    def _index_tokens(self, artifact_id: str, text: str) -> None:
        seen: set[str] = set()
        tokens = text_tokens(text)
        for token in tokens:
            self.token_index[token].add(artifact_id)
            if token not in seen:
                self.doc_freq[token] += 1
                seen.add(token)

    def _artifact_entities(self, artifact: dict) -> set[str]:
        entities: set[str] = set()
        text = artifact.get("text", "")
        lower_text = text.lower()

        for token, entity_ids in self.alias_table.items():
            if re.search(rf"\b{re.escape(token)}\b", lower_text):
                entities.update(entity_ids)
        for alias in extract_aliases(text):
            entities.update(self.alias_table.get(alias, set()))
            if alias not in self.alias_table:
                entities.add(alias)

        container = artifact.get("container_id")
        if container:
            entities.update(self.alias_table.get(container.lower(), set()))
            for token in text_tokens(str(container)):
                entities.update(self.alias_table.get(token, set()))

        sender = artifact.get("sender_id")
        if sender:
            entities.add(sender)
        for recipient in artifact.get("recipients", []):
            entities.add(recipient)

        return entities

    def _index_thread(self, artifact_id: str, artifact: dict) -> None:
        container_id = artifact.get("container_id")
        if container_id is not None:
            self.thread_index[container_id].append(artifact_id)
        parent = artifact.get("parent_id")
        if parent:
            self.parent_index[artifact_id] = parent

    def _index_time(self, artifact_id: str, artifact: dict) -> None:
        self.time_index.append((artifact.get("ts", ""), artifact_id))

    def _finalize_time_index(self) -> None:
        self.time_index.sort()
        self.time_index = [artifact_id for _, artifact_id in self.time_index]

    def artifact(self, artifact_id: str) -> dict:
        return self.artifacts[artifact_id]

    def extract_aliases(self, text: str) -> set[str]:
        return extract_aliases(text)

    def alias_matches(self, text: str) -> set[str]:
        matches: set[str] = set()
        for token in text_tokens(text):
            matches.update(self.alias_table.get(token, set()))
        for alias in self.extract_aliases(text):
            matches.update(self.alias_table.get(alias, set()))
            matches.update(self.alias_key_index.get(alias, set()))
        return matches

    def bm25_scores(self, query: str, top_k: int = 15) -> list[tuple[str, float]]:
        query_tokens = text_tokens(query)
        if not query_tokens:
            return []
        scores: dict[str, float] = defaultdict(float)
        doc_count = len(self.artifacts)
        avgdl = sum(len(text_tokens(a["text"])) for a in self.artifacts.values()) / max(1, doc_count)
        for token in query_tokens:
            if token not in self.token_index:
                continue
            df = self.doc_freq[token]
            idf = max(0.0, log((doc_count - df + 0.5) / (df + 0.5) + 1))
            for artifact_id in self.token_index[token]:
                tf = text_tokens(self.artifacts[artifact_id]["text"]).count(token)
                denom = tf + 1.2 * (1 - 0.75 + 0.75 * len(text_tokens(self.artifacts[artifact_id]["text"])) / avgdl)
                scores[artifact_id] += idf * ((tf * (1.2 + 1)) / denom)
        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        return ranked[:top_k]

    def artifacts_in_thread(self, artifact_id: str) -> set[str]:
        artifact = self.artifacts[artifact_id]
        container = artifact.get("container_id")
        if container is None:
            return {artifact_id}
        return set(self.thread_index.get(container, []))

    def artifacts_near_time(self, artifact_id: str, window_hours: int = 6) -> set[str]:
        if artifact_id not in self.artifacts:
            return set()
        ts = self.artifacts[artifact_id].get("ts")
        if not ts:
            return set()
        source = self.artifacts[artifact_id].get("source")
        entities = self.artifact_entities.get(artifact_id, set())
        candidates = set()
        start = self._parse_ts(ts) - window_hours * 3600
        end = self._parse_ts(ts) + window_hours * 3600
        for other_id, other in self.artifacts.items():
            if other_id == artifact_id:
                continue
            if other.get("source") != source:
                continue
            other_ts = self._parse_ts(other.get("ts", ""))
            if other_ts < start or other_ts > end:
                continue
            if self.artifact_entities.get(other_id, set()) & entities:
                candidates.add(other_id)
        return candidates

    @staticmethod
    def _parse_ts(ts: str) -> int:
        from datetime import datetime
        if not ts:
            return 0
        try:
            dt = datetime.fromisoformat(ts)
            return int(dt.timestamp())
        except ValueError:
            return 0
