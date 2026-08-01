import unittest
from unittest.mock import patch

from src.agent4 import run_agent4
from src.crossref import dedupe_status_items, detect_conflicts


class Agent4Tests(unittest.TestCase):
    @patch("src.agent4.extract_candidates")
    @patch("src.agent4.Retriever.retrieve")
    def test_agent4_orchestration_smoke(self, mock_retrieve, mock_extract):
        mock_retrieve.return_value = [
            {
                "artifact_id": "tkt_001",
                "path": ["tkt_001"],
                "artifact": {
                    "artifact_id": "tkt_001",
                    "source": "ticket",
                    "text": "Legal sign-off pending",
                },
            }
        ]

        mock_extract.return_value = [
            {
                "source": "ticket",
                "topic": "Billing migration",
                "status": "blocked",
                "owner": "Marcus",
                "blocker": "Legal sign-off pending",
                "confidence": "high",
                "evidence": [
                    {"record_id": "tkt_001", "span": "Legal sign-off pending"}
                ],
            }
        ]

        result = run_agent4(
            question="What is currently blocked?",
            corpus_dir="data",
            hops=1,
            top_k=5,
            batch_size=1,
            max_workers=1,
        )

        self.assertIsInstance(result, dict)
        self.assertEqual(result["question"], "What is currently blocked?")
        self.assertIsInstance(result["candidates"], list)
        self.assertIsInstance(result["items"], list)
        self.assertIsInstance(result["conflicts"], list)

    def test_crossref_detects_conflict(self):
        items = [
            {
                "source": "slack",
                "topic": "Billing migration",
                "status": "blocked",
                "owner": "Marcus",
                "blocker": "Legal sign-off pending on the data retention change",
                "confidence": "high",
                "evidence": [
                    {
                        "record_id": "msg_1",
                        "span": "Legal sign-off pending on the data retention change",
                    }
                ],
            },
            {
                "source": "email",
                "topic": "Billing migration",
                "status": "on_track",
                "owner": "Marcus",
                "blocker": "Legal sign-off obtained and we are moving forward",
                "confidence": "high",
                "evidence": [
                    {
                        "record_id": "msg_2",
                        "span": "Legal sign-off obtained and we are moving forward",
                    }
                ],
            },
        ]
        merged = dedupe_status_items(items)
        conflicts = detect_conflicts(items)
        self.assertTrue(merged)
        self.assertGreaterEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["subject"], "Billing migration")
