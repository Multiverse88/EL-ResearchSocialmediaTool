from __future__ import annotations

import os


def _render_static_page(filename: str, fallback_title: str) -> str:
    """
    Reads a static HTML page and injects the current CHAT_ACTION_API_KEY so
    same-origin browser fetches to POST /chat authenticate correctly when
    that key is configured.
    """
    page_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "static", filename))
    if os.path.exists(page_path):
        with open(page_path, "r", encoding="utf-8") as f:
            html = f.read()
        chat_key = os.getenv("CHAT_ACTION_API_KEY", "").strip()
        return html.replace("__CHAT_ACTION_API_KEY__", chat_key)

    return f"<!DOCTYPE html><html><head><title>{fallback_title}</title></head><body><h1>{fallback_title}</h1></body></html>"


def render_analytics_dashboard_html() -> str:
    """
    Renders the social media intelligence and analytics dashboard.
    Reads static/index.html as the single source of truth.
    """
    return _render_static_page("index.html", "EasyCorp Analytics Dashboard")


def render_chat_page_html() -> str:
    """
    Renders the dedicated "Tanya AI" chat page (a standalone ChatGPT-style
    interface, separate from the analytics dashboard).
    Reads static/chat.html as the single source of truth.
    """
    return _render_static_page("chat.html", "Tanya AI - EasyCorp")


def render_logs_page_html() -> str:
    """
    Renders the dedicated scraping log page (history of every scrape run: which
    account/topic, platform, success/failure, error detail).
    Reads static/logs.html as the single source of truth.
    """
    return _render_static_page("logs.html", "Log Scraping - EasyCorp")
