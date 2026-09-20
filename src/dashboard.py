from __future__ import annotations

import os


def render_analytics_dashboard_html() -> str:
    """
    Renders the social media intelligence and analytics dashboard, with an embedded
    "Tanya AI" chat panel (replaces the standalone Open WebUI container).
    Reads static/index.html as the single source of truth, injecting the current
    CHAT_ACTION_API_KEY so same-origin browser fetches to POST /chat authenticate
    correctly when that key is configured.
    """
    index_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "static", "index.html"))
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            html = f.read()
        chat_key = os.getenv("CHAT_ACTION_API_KEY", "").strip()
        return html.replace("__CHAT_ACTION_API_KEY__", chat_key)

    return "<!DOCTYPE html><html><head><title>EasyCorp Analytics</title></head><body><h1>EasyCorp Analytics Dashboard</h1></body></html>"
