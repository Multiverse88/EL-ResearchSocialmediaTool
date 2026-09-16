from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("scrapers.apify")

APIFY_BASE_URL = "https://api.apify.com/v2"

# Well-maintained public actors for each platform.
INSTAGRAM_ACTOR_ID = "apify~instagram-scraper"
TIKTOK_ACTOR_ID = "clockworks~tiktok-scraper"


def get_apify_token() -> Optional[str]:
    """Returns the configured Apify API token, or None if not set."""
    token = os.getenv("APIFY_API_TOKEN", "").strip()
    return token or None


def is_apify_configured() -> bool:
    return get_apify_token() is not None


def run_actor_sync(
    actor_id: str,
    run_input: Dict[str, Any],
    timeout: float = 120.0,
) -> List[Dict[str, Any]]:
    """
    Runs an Apify actor synchronously (run-sync-get-dataset-items) and returns
    the resulting dataset items as a list of dicts.

    Raises RuntimeError if APIFY_API_TOKEN is not configured or the actor run fails.
    Small scrape jobs (<=100 items) comfortably finish within Apify's 300s sync limit.
    """
    token = get_apify_token()
    if not token:
        raise RuntimeError("APIFY_API_TOKEN not configured")

    url = f"{APIFY_BASE_URL}/acts/{actor_id}/run-sync-get-dataset-items?token={token}"
    logger.info(f"Running Apify actor {actor_id} with input: {run_input}")

    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, json=run_input)
        if resp.status_code == 408:
            raise RuntimeError(
                f"Apify actor {actor_id} exceeded the 300s sync timeout. "
                "Reduce resultsLimit/maxProfileVideos or run asynchronously."
            )
        # Apify's run-sync-get-dataset-items endpoint returns 200 in most cases but 201
        # when the run completes synchronously as a newly-created resource — both are
        # success. Treating 201 as a failure here silently discarded valid scrape
        # results and fell back to the free scrapers, which is far more error-prone.
        if not (200 <= resp.status_code < 300):
            raise RuntimeError(f"Apify actor {actor_id} failed: HTTP {resp.status_code} - {resp.text[:300]}")
        items = resp.json()
        if not isinstance(items, list):
            raise RuntimeError(f"Apify actor {actor_id} returned unexpected payload shape (not a list)")
        return items
