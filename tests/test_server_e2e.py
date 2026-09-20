import unittest
from fastapi.testclient import TestClient
from src.server import app, get_db
from src.models import Account, Post

db = get_db()


class TestServerE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_01_health(self):
        res = self.client.get("/api/health")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "healthy")

    def test_02_accounts_crud(self):
        # List accounts (should have seeded accounts)
        res = self.client.get("/accounts")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "success")
        self.assertGreaterEqual(data["count"], 1)

        # Create new account
        new_acc_payload = {
            "platform": "instagram",
            "username": "easytax_official_test",
            "is_own_brand": True,
        }
        res_create = self.client.post("/accounts", json=new_acc_payload)
        self.assertEqual(res_create.status_code, 200)
        create_data = res_create.json()
        self.assertEqual(create_data["status"], "success")
        self.assertEqual(create_data["data"]["username"], "easytax_official_test")

    def test_03_posts_and_summary(self):
        # Insert a sample post directly into db for testing
        acc = db.get_account_by_username("instagram", "easylegal_id")
        self.assertIsNotNone(acc)
        
        test_post = Post.create(
            account_id=acc.id,
            platform_post_id="TEST_IG_999",
            caption="Panduan pengurusan izin OSS dan pendirian PT 2026 #EasyLegal",
            media_url="https://img.com/p999.jpg",
            likes=450,
            comments=35,
            views=1200,
            posted_at="2026-02-01T10:00:00+00:00",
            platform="instagram",
        )
        db.upsert_posts([test_post])

        # Query posts
        res = self.client.get("/posts?keyword=OSS")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertGreaterEqual(data["count"], 1)
        self.assertIn("OSS", data["data"][0]["caption"])

        # Query posts summary
        res_sum = self.client.get(f"/posts/summary?account_id={acc.id}")
        self.assertEqual(res_sum.status_code, 200)
        sum_data = res_sum.json()
        self.assertEqual(sum_data["status"], "success")
        self.assertGreater(sum_data["summary"]["total_posts"], 0)

    def test_04_chat_endpoint(self):
        # Test marketing engagement query
        res = self.client.post("/chat", json={"message": "Berapa rata-rata likes akun easylegal_id?"})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "success")
        self.assertIn("reply", data)
        self.assertTrue(len(data["reply"]) > 0)

        # Test compare accounts query
        res_comp = self.client.post("/chat", json={"message": "Bandingkan performa easylegal_id vs legalku_official"})
        self.assertEqual(res_comp.status_code, 200)
        comp_data = res_comp.json()
        self.assertEqual(comp_data["status"], "success")

    def test_05_openai_compatible_api(self):
        # 1. OpenAI-compatible model discovery endpoint (used by any OpenAI-compatible client)
        res_models = self.client.get("/v1/models")
        self.assertEqual(res_models.status_code, 200)
        models_data = res_models.json()
        self.assertEqual(models_data["object"], "list")
        model_ids = [m["id"] for m in models_data["data"]]
        self.assertIn("social-media-claude-agent", model_ids)

        # 2. OpenAI-compatible chat completion endpoint - default streaming (SSE) for typing animation
        payload = {
            "model": "social-media-claude-agent",
            "messages": [
                {"role": "user", "content": "Tampilkan performa engagement easylegal_id"}
            ]
        }
        res_stream = self.client.post("/v1/chat/completions", json=payload)
        self.assertEqual(res_stream.status_code, 200)
        self.assertIn("text/event-stream", res_stream.headers.get("content-type", ""))
        body = res_stream.text
        self.assertIn("data: ", body)
        self.assertIn("[DONE]", body)
        self.assertIn('"chat.completion.chunk"', body)

        # 3. Explicit stream=false returns the classic buffered JSON contract
        payload_no_stream = {**payload, "stream": False}
        res_chat = self.client.post("/v1/chat/completions", json=payload_no_stream)
        self.assertEqual(res_chat.status_code, 200)
        chat_data = res_chat.json()
        self.assertEqual(chat_data["object"], "chat.completion")
        self.assertEqual(len(chat_data["choices"]), 1)
        self.assertEqual(chat_data["choices"][0]["message"]["role"], "assistant")
        self.assertTrue(len(chat_data["choices"][0]["message"]["content"]) > 0)

    def test_06_dashboard_served(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("text/html", res.headers.get("content-type", ""))
        self.assertIn("EasyCorp Social Media Intel", res.text)


if __name__ == "__main__":
    unittest.main()
