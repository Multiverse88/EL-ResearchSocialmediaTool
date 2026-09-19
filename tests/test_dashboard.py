import os
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient

from src.dashboard import get_analytics_dashboard_url, render_analytics_dashboard_html
from src.server import app


class TestAnalyticsDashboard(unittest.TestCase):
    def test_get_analytics_dashboard_url_default(self):
        with patch.dict(os.environ, {}, clear=True):
            url = get_analytics_dashboard_url()
            self.assertEqual(url, "/analytics")

    def test_get_analytics_dashboard_url_custom_domain(self):
        custom = "https://social-analytics.easylegal.my.id/analytics"
        with patch.dict(os.environ, {"ANALYTICS_DASHBOARD_URL": custom}):
            url = get_analytics_dashboard_url()
            self.assertEqual(url, custom)

    def test_render_analytics_dashboard_html(self):
        html = render_analytics_dashboard_html()
        self.assertIsInstance(html, str)
        self.assertTrue(len(html) > 1000)
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("EasyCorp Social Media Analytics", html)
        self.assertIn("chart.js", html.lower())
        self.assertIn("timeseriesChart", html)
        self.assertIn("competitorChart", html)
        self.assertIn("leaderboard-body", html)
        self.assertIn("sync-now-btn", html)

    def test_get_analytics_endpoint(self):
        client = TestClient(app)
        resp = client.get("/analytics")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers.get("content-type", ""))
        self.assertIn("EasyCorp Social Media Analytics", resp.text)
        self.assertIn("timeseriesChart", resp.text)


if __name__ == "__main__":
    unittest.main()
