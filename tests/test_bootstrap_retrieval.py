import json
import unittest
from pathlib import Path

from src.index import Index
from src.retrieve import Retriever

ROOT = Path(__file__).resolve().parent.parent / "data"


class BootstrapRetrievalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.index = Index.from_path(ROOT)
        cls.key = json.loads((ROOT / "answer_key.json").read_text())

    def test_q004_retrieves_ticket_artifact(self) -> None:
        question = next(q for q in self.key["questions"] if q["question_id"] == "Q_004")
        results = Retriever(self.index).retrieve(question["question"], hops=3, top_k=50)
        artifact_ids = [item["artifact_id"] for item in results]
        self.assertIn("tkt_4402_c5", artifact_ids)


if __name__ == "__main__":
    unittest.main()
