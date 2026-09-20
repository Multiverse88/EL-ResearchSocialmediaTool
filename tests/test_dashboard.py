import os
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient

from src.dashboard import render_analytics_dashboard_html
from src.server import app


class TestAnalyticsDashboard(unittest.TestCase):
    def test_render_analytics_dashboard_html(self):
        html = render_analytics_dashboard_html()
        self.assertIsInstance(html, str)
        self.assertTrue(len(html) > 1000)
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("EasyCorp Social Media Intelligence", html)
        self.assertIn("chart.js", html.lower())
        self.assertIn("engagementWaveCanvas", html)
        self.assertIn("topicBarChartCanvas", html)
        self.assertIn("posts-table-body", html)
        self.assertIn("trigger-sync-btn", html)
        self.assertIn("ai-chat-form", html)
        self.assertIn("toggle-ai-panel-btn", html)
        self.assertNotIn("open-webui", html.lower())

    def test_render_injects_chat_action_api_key(self):
        with patch.dict(os.environ, {"CHAT_ACTION_API_KEY": "secret-test-key"}):
            html = render_analytics_dashboard_html()
        self.assertIn('"secret-test-key"', html)
        self.assertNotIn("__CHAT_ACTION_API_KEY__", html)

    def test_render_leaves_placeholder_empty_when_key_unset(self):
        with patch.dict(os.environ, {}, clear=True):
            html = render_analytics_dashboard_html()
        self.assertIn('const CHAT_ACTION_API_KEY = "";', html)

    def test_get_analytics_endpoint(self):
        client = TestClient(app)
        resp = client.get("/analytics")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers.get("content-type", ""))
        self.assertIn("EasyCorp Social Media Intelligence", resp.text)
        self.assertIn("engagementWaveCanvas", resp.text)


if __name__ == "__main__":
    unittest.main()
