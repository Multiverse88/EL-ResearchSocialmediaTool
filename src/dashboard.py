from __future__ import annotations

import os

_DEFAULT_DASHBOARD_URL = "https://sosmed.easycorp.id/analytics"


def get_analytics_dashboard_url() -> str:
    """Returns the external dashboard URL from environment, or default https://sosmed.easycorp.id/analytics."""
    return os.getenv("ANALYTICS_DASHBOARD_URL", _DEFAULT_DASHBOARD_URL).strip() or _DEFAULT_DASHBOARD_URL


def render_analytics_dashboard_html() -> str:
    """
    Renders the Awwwards-grade social media intelligence and topic analytics dashboard.
    Reads static/index.html as the single source of truth.
    """
    index_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "static", "index.html"))
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return f.read()

    return "<!DOCTYPE html><html><head><title>EasyCorp Analytics</title></head><body><h1>EasyCorp Analytics Dashboard</h1></body></html>"
