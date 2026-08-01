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

    def expand_hop(self, frontier: list[str], cap: int = 15) -> dict[str, tuple[float, list[str]]]:
        """Returns artifact_id -> (score, path). Score is carried by the caller so the
        final candidate list can be ranked globally instead of by hop-insertion order —
        without this, an artifact reached late by a strong link (e.g. shared entity) can
        rank behind weaker matches found earlier, which is what silently dropped a gold
        artifact off the k=50 cut on Q_005.
        """
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
        return {artifact_id: (score, path) for artifact_id, (score, path) in kept}

    def _shared_entity_neighbors(self, artifact_id: str) -> dict[str, float]:
        """neighbor -> sum of IDF over entities shared with artifact_id.

        IDF-weighted, not a flat count: at real corpus scale a project like PROJ_AEGIS
        can touch a third of every artifact, so "shares an entity" alone is nearly
        uninformative for it. A neighbor sharing one rare entity (a specific ticket, a
        specific person pair) should outrank one sharing several corpus-wide hubs —
        summing raw counts got that backwards.
        """
        entities = self.index.artifact_entities.get(artifact_id, set())
        scores: dict[str, float] = defaultdict(float)
        for entity_id in entities:
            weight = self.index.entity_idf.get(entity_id, 0.05)
            for neighbor in self.index.entity_index.get(entity_id, set()):
                if neighbor != artifact_id:
                    scores[neighbor] += weight
        return scores

    def _neighbor_candidates(self, artifact_id: str) -> dict[str, float]:
        scores: dict[str, float] = {}
        for neighbor, shared_weight in self._shared_entity_neighbors(artifact_id).items():
            scores[neighbor] = max(scores.get(neighbor, 0.0), 3.0 * shared_weight)
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
        # A seed-hop artifact gets a high floor score so it always outranks anything
        # found only by weaker later-hop links (thread/time), but a strong shared-entity
        # link discovered on a later hop can still beat a weak seed match.
        SEED_SCORE = 10.0
        seed = self.seed_candidates(question, top_k=20)
        seen: set[str] = set(seed)
        candidate_paths: dict[str, list[str]] = {aid: [aid] for aid in seed}
        candidate_scores: dict[str, float] = {aid: SEED_SCORE for aid in seed}
        frontier = seed[:]

        # Frontier cap for the *next* hop's starting points, not a stop condition — at
        # real corpus scale hop 1 alone can turn up thousands of weakly-linked
        # neighbors (a common project touches a third of the corpus). Stopping once
        # `seen >= top_k` used to cut hops 2/3 off entirely as soon as that happened,
        # which silently disabled multi-hop reasoning exactly when the corpus was big
        # enough to need it. Instead: always run all `hops` iterations, but only carry
        # the best-scoring newcomers forward so later hops stay tractable.
        FRONTIER_CAP = 100
        for hop in range(1, hops + 1):
            next_hits = self.expand_hop(frontier, cap=200)
            new_neighbors = [aid for aid in next_hits if aid not in seen]
            for aid in new_neighbors:
                score, path = next_hits[aid]
                candidate_paths[aid] = path
                candidate_scores[aid] = score
            seen.update(new_neighbors)
            new_neighbors.sort(key=lambda aid: -candidate_scores[aid])
            frontier = new_neighbors[:FRONTIER_CAP]
            if not frontier:
                break

        # At ~3k artifacts the loose ALIAS_PATTERN (any 1-4 lowercase letters + digits)
        # matches common tokens like "v1"/"v2"/"aeg-2"/"p99" hundreds of times, plus
        # channel names leak into alias_key_index the same way container_id's own
        # tokens do. Treating those as a "shared identifier" link is noise, not
        # signal — a rare key like "eng-4402" (5 artifacts) is the real thing this
        # is for. Skip any alias whose group is too big to mean anything specific.
        MAX_ALIAS_GROUP = 20

        def _alias_hits(alias: str) -> set[str]:
            group = self.index.alias_key_index.get(alias, set())
            if len(group) > MAX_ALIAS_GROUP:
                return set()
            return self._filter_source(group)

        alias_hits: set[str] = set()
        for alias in self.index.extract_aliases(question):
            for artifact_id in _alias_hits(alias):
                if artifact_id not in candidate_paths:
                    candidate_paths[artifact_id] = [artifact_id]
                    candidate_scores[artifact_id] = SEED_SCORE
                    seen.add(artifact_id)
                alias_hits.add(artifact_id)

        # An alias-linked hit (e.g. sharing a ticket key) is a real signal but weaker
        # than the artifact that led to it — inheriting the parent's full score verbatim
        # produced a pile of unrelated ties at the seed score, which made the final sort
        # fall back to arbitrary insertion order among all of them.
        ALIAS_HOP_SCORE = 8.0
        for artifact_id in list(candidate_paths):
            artifact = self.index.artifact(artifact_id)
            for alias in self.index.extract_aliases(artifact.get("text", "")):
                for hit in _alias_hits(alias):
                    if hit not in candidate_paths:
                        candidate_paths[hit] = [artifact_id, hit]
                        candidate_scores[hit] = ALIAS_HOP_SCORE
                        seen.add(hit)
                    alias_hits.add(hit)

        # Rank globally: alias hits first, then by best link score, then by relevance to
        # the question (BM25) as a tiebreaker instead of arbitrary hop-insertion order —
        # ties are common (many artifacts share the same structural score), and among
        # ties the one that actually reads as on-topic should win the top_k cut.
        bm25_scores = dict(self.index.bm25_scores(question, top_k=len(self.index.artifacts)))
        ordered_ids = sorted(
            candidate_paths,
            key=lambda aid: (
                0 if aid in alias_hits else 1,
                -candidate_scores.get(aid, 0.0),
                -bm25_scores.get(aid, 0.0),
                len(candidate_paths[aid]),
            ),
        )

        results = []
        for artifact_id in ordered_ids[:top_k]:
            path = candidate_paths[artifact_id]
            results.append({
                "artifact_id": artifact_id,
                "path": path,
                "artifact": self.index.artifact(artifact_id),
            })
        return results
