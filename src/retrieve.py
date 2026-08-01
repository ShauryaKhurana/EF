from collections import deque, defaultdict
from typing import Iterable

from .index import Index


class RetrieveError(Exception):
    pass


class Retriever:
    def __init__(self, index: Index, only_source: str | None = None):
        self.index = index
        self.only_source = only_source

    def _filter_source(self, artifact_ids: Iterable[str]) -> set[str]:
        if self.only_source is None:
            return set(artifact_ids)
        allowed = {aid for aid in artifact_ids if self.index.artifacts[aid]["source"] == self.only_source}
        return allowed

    def seed_candidates(self, question: str, top_k: int = 15) -> list[str]:
        bm25 = [aid for aid, _ in self.index.bm25_scores(question, top_k=max(top_k * 3, 30))]
        alias_ids = set()
        for entity_id in self.index.alias_matches(question):
            alias_ids.update(self.index.entity_index.get(entity_id, set()))
        for alias in self.index.extract_aliases(question):
            alias_ids.update(self.index.alias_key_index.get(alias, set()))
        candidates = self._filter_source(bm25 + list(alias_ids))
        return list(candidates)[:max(top_k * 2, top_k)]

    def expand_hop(self, frontier: list[str], cap: int = 15) -> dict[str, list[str]]:
        scored: dict[str, tuple[float, list[str]]] = {}

        for artifact_id in frontier:
            current_hops = [artifact_id]
            for neighbor, score in self._neighbor_candidates(artifact_id).items():
                if neighbor not in self._filter_source([neighbor]):
                    continue
                if neighbor == artifact_id:
                    continue
                if neighbor in scored and scored[neighbor][0] >= score:
                    continue
                scored[neighbor] = (score, current_hops + [neighbor])

        best = sorted(scored.items(), key=lambda item: (-item[1][0], item[0]))
        kept = best[: max(cap * 3, cap)]
        return {artifact_id: path for artifact_id, (_, path) in kept}

    def _shared_entity_neighbors(self, artifact_id: str) -> set[str]:
        entities = self.index.artifact_entities.get(artifact_id, set())
        neighbors = set()
        for entity_id in entities:
            neighbors.update(self.index.entity_index.get(entity_id, set()))
        neighbors.discard(artifact_id)
        return neighbors

    def _neighbor_candidates(self, artifact_id: str) -> dict[str, float]:
        scores: dict[str, float] = {}
        for neighbor in self._shared_entity_neighbors(artifact_id):
            scores[neighbor] = max(scores.get(neighbor, 0.0), 3.0)
        for neighbor in self._thread_neighbors(artifact_id):
            scores[neighbor] = max(scores.get(neighbor, 0.0), 2.0)
        for neighbor in self._time_neighbors(artifact_id):
            scores[neighbor] = max(scores.get(neighbor, 0.0), 1.0)
        return scores

    def _thread_neighbors(self, artifact_id: str) -> set[str]:
        return self.index.artifacts_in_thread(artifact_id) - {artifact_id}

    def _time_neighbors(self, artifact_id: str) -> set[str]:
        return self.index.artifacts_near_time(artifact_id, window_hours=6)

    def retrieve(self, question: str, hops: int = 3, top_k: int = 50) -> list[dict]:
        seed = self.seed_candidates(question, top_k=20)
        seen: set[str] = set(seed)
        candidate_paths: dict[str, list[str]] = {aid: [aid] for aid in seed}
        frontier = seed[:]

        for hop in range(1, hops + 1):
            next_paths = self.expand_hop(frontier, cap=200)
            new_neighbors = [aid for aid in next_paths if aid not in seen]
            for aid in new_neighbors:
                candidate_paths[aid] = next_paths[aid]
            seen.update(new_neighbors)
            frontier = new_neighbors
            if len(seen) >= top_k:
                break

        alias_hits: set[str] = set()
        for alias in self.index.extract_aliases(question):
            for artifact_id in self.index.alias_key_index.get(alias, set()):
                if artifact_id not in candidate_paths:
                    candidate_paths[artifact_id] = [artifact_id]
                    seen.add(artifact_id)
                alias_hits.add(artifact_id)

        for artifact_id in list(candidate_paths):
            artifact = self.index.artifact(artifact_id)
            for alias in self.index.extract_aliases(artifact.get("text", "")):
                for hit in self.index.alias_key_index.get(alias, set()):
                    if hit not in candidate_paths:
                        candidate_paths[hit] = [artifact_id, hit]
                        seen.add(hit)
                    alias_hits.add(hit)

        ordered_ids = [artifact_id for artifact_id in candidate_paths if artifact_id in alias_hits]
        ordered_ids.extend(artifact_id for artifact_id in candidate_paths if artifact_id not in alias_hits)

        results = []
        for artifact_id in ordered_ids[:top_k]:
            path = candidate_paths[artifact_id]
            results.append({
                "artifact_id": artifact_id,
                "path": path,
                "artifact": self.index.artifact(artifact_id),
            })
        return results
