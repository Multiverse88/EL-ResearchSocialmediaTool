import unittest
from fastapi.testclient import TestClient
from src.server import app, get_db
from src.sample_data import seed_marketing_sample_data


class TestTopicResearch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        db = get_db()
        seed_marketing_sample_data(db, posts_per_account=15)

    def test_list_topics(self):
        res = self.client.get("/topics")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "success")
        self.assertGreater(data["count"], 0)

    def test_topic_summary(self):
        res = self.client.get("/topics/summary?keyword=pendirian PT")
        self.assertEqual(res.status_code, 200)
        json_data = res.json()
        self.assertEqual(json_data["status"], "success")
        summary = json_data["data"]
        self.assertEqual(summary["keyword"], "pendirian pt")
        self.assertGreater(summary["total_posts"], 0)
        self.assertGreater(summary["avg_likes"], 0)
        self.assertIn("viral_references", summary)
        self.assertTrue(len(summary["viral_references"]) > 0)

    def test_compare_topics(self):
        res = self.client.get("/topics/compare?keywords=pendirian PT,virtual office,konsultasi pajak")
        self.assertEqual(res.status_code, 200)
        json_data = res.json()
        self.assertEqual(json_data["status"], "success")
        comp = json_data["data"]
        self.assertEqual(comp["compared_topics"], 3)
        self.assertEqual(len(comp["leaderboard"]), 3)
        self.assertEqual(comp["leaderboard"][0]["rank"], 1)

    def test_chat_topic_research(self):
        res = self.client.post("/chat", json={"message": "Riset topik pendirian PT seperti apa performanya di sosial media?"})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["tool_used"], "research_topic")
        self.assertIn("pendirian pt", data["reply"].lower())

    def test_chat_compare_topics(self):
        res = self.client.post("/chat", json={"message": "Bandingkan topik pendirian PT vs virtual office vs konsultasi pajak"})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["tool_used"], "compare_topics")


if __name__ == "__main__":
    unittest.main()
