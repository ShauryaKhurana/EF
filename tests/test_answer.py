import unittest

from src.answer import generate_answer


class AnswerGenerationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.world = {
            "projects": [],
            "services": [],
            "clients": [],
            "employees": [],
        }

    def test_relevant_items_are_prioritized_for_acme_question(self) -> None:
        items = [
            {
                "topic": "Acme Checkout Incident",
                "status": "blocked",
                "confidence": "high",
                "owner": "EMP_082",
                "blocker": "checkout outage",
            },
            {
                "topic": "Bill-v2",
                "status": "at_risk",
                "confidence": "medium",
                "owner": "EMP_003",
                "blocker": "memory leak",
            },
        ]

        answer = generate_answer(
            question="Why is CS flagging churn risk on Acme?",
            items=items,
            conflicts=[],
            hop_paths={},
            world=self.world,
        )

        self.assertFalse(answer.abstained)
        self.assertIn("Acme Checkout Incident", answer.text)
        self.assertNotIn("Bill-v2", answer.text)

    def test_abstains_when_question_has_no_matching_entity(self) -> None:
        items = [
            {
                "topic": "Acme Checkout Incident",
                "status": "blocked",
                "confidence": "high",
                "owner": "EMP_082",
                "blocker": "checkout outage",
            }
        ]

        answer = generate_answer(
            question="Why is CS flagging churn risk on the moon?",
            items=items,
            conflicts=[],
            hop_paths={},
            world=self.world,
        )

        self.assertTrue(answer.abstained)
        self.assertIn("enough confirmed information", answer.text.lower())

    def test_ignores_generic_risk_words_when_no_entity_matches(self) -> None:
        items = [
            {
                "topic": "Aegis is at risk",
                "status": "at_risk",
                "confidence": "medium",
                "owner": "EMP_082",
                "blocker": "pool fix needs a soak week",
            }
        ]

        answer = generate_answer(
            question="Why is CS flagging churn risk on the moon?",
            items=items,
            conflicts=[],
            hop_paths={},
            world=self.world,
        )

        self.assertTrue(answer.abstained)


if __name__ == "__main__":
    unittest.main()
