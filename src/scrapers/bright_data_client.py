from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

import httpx

logger = logging.getLogger("scrapers.bright_data")

BRIGHT_DATA_BASE_URL = "https://api.brightdata.com"
INSTAGRAM_POSTS_DATASET_ID = "gd_lk5ns7kz21pck8jpis"
INSTAGRAM_REELS_DATASET_ID = "gd_lyclm20il4r5helnj"
TIKTOK_POSTS_DATASET_ID = "gd_lu702nij2f790tmv9h"
TIKTOK_PROFILES_DATASET_ID = "gd_l1villgoiiidt09ci"
THREADS_PROFILES_DATASET_ID = "gd_mde7jg3ld2h3hnnf2"  # profile info + embedded recent posts, one sync call


class BrightDataError(RuntimeError):
    """A sanitized Bright Data configuration, transport, or API failure."""


_token_lock = threading.Lock()
_token_counter = 0


def get_bright_data_tokens() -> List[str]:
    """Returns all configured Bright Data API tokens as a list.
    Supports single token or comma/space-separated tokens in BRIGHT_DATA_API_TOKEN
    or BRIGHT_DATA_API_TOKENS for multi-account pool and failover.
    """
    raw = os.getenv("BRIGHT_DATA_API_TOKENS") or os.getenv("BRIGHT_DATA_API_TOKEN") or ""
    return [t.strip() for t in re.split(r"[,;\s]+", raw) if t.strip()]


def get_bright_data_token() -> Optional[str]:
    """Returns the primary (first) configured token, or None."""
    tokens = get_bright_data_tokens()
    return tokens[0] if tokens else None


def get_bright_data_serp_zones() -> List[str]:
    """Returns all configured SERP zones. Supports single or comma-separated list."""
    raw = os.getenv("BRIGHT_DATA_SERP_ZONES") or os.getenv("BRIGHT_DATA_SERP_ZONE") or ""
    return [z.strip() for z in re.split(r"[,;\s]+", raw) if z.strip()]


def get_bright_data_serp_zone() -> Optional[str]:
    """Returns the primary (first) configured SERP zone, or None."""
    zones = get_bright_data_serp_zones()
    return zones[0] if zones else None


def is_bright_data_configured() -> bool:
    return len(get_bright_data_tokens()) > 0


def _mask_token(token: str) -> str:
    if not token or len(token) < 8:
        return "***"
    return f"{token[:4]}...{token[-4:]}"


def _get_rotating_tokens() -> List[str]:
    """Returns the list of tokens ordered starting from the current round-robin index,
    so consecutive requests naturally distribute load across all available accounts."""
    global _token_counter
    tokens = get_bright_data_tokens()
    if not tokens:
        return []
    with _token_lock:
        start_idx = _token_counter % len(tokens)
        _token_counter += 1
    return [tokens[(start_idx + i) % len(tokens)] for i in range(len(tokens))]


def _get_rotating_serp_pairs() -> List[Tuple[str, str]]:
    """Returns list of (token, zone) pairs ordered for round-robin rotation and failover."""
    global _token_counter
    tokens = get_bright_data_tokens()
    zones = get_bright_data_serp_zones()
    if not tokens:
        raise BrightDataError("BRIGHT_DATA_API_TOKEN not configured")
    if not zones:
        raise BrightDataError("BRIGHT_DATA_SERP_ZONE not configured")
    with _token_lock:
        start_idx = _token_counter % len(tokens)
        _token_counter += 1
    pairs = []
    for i in range(len(tokens)):
        idx = (start_idx + i) % len(tokens)
        tok = tokens[idx]
        zn = zones[idx] if idx < len(zones) else zones[0]
        pairs.append((tok, zn))
    return pairs

def _positive_float_env(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def _headers(token: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _safe_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
        if isinstance(payload, dict):
            detail = payload.get("message") or payload.get("error") or payload.get("detail")
            if isinstance(detail, dict):
                detail = detail.get("message") or str(detail)
            if detail:
                return str(detail)[:300]
    except Exception:
        pass
    return (response.text or "").strip()[:300]


def _raise_http_error(response: httpx.Response, operation: str) -> None:
    status = response.status_code
    detail = _safe_detail(response)
    if status == 401:
        message = "Bright Data authentication failed (HTTP 401): check BRIGHT_DATA_API_TOKEN"
    elif status == 403:
        message = "Bright Data access denied (HTTP 403): activate the requested dataset/product or SERP zone"
    elif status == 429:
        message = "Bright Data rate limit exceeded (HTTP 429)"
    else:
        message = f"Bright Data {operation} failed: HTTP {status}"
    if detail:
        message += f" - {detail}"
    raise BrightDataError(message)


def _post_with_rate_limit_retry(
    client: httpx.Client,
    url: str,
    *,
    headers: Dict[str, str],
    params: Optional[Dict[str, Any]] = None,
    payload: Any,
) -> httpx.Response:
    try:
        response = client.post(url, headers=headers, params=params, json=payload)
        if response.status_code != 429:
            return response
        retry_after = response.headers.get("Retry-After")
        try:
            wait_seconds = max(0.0, float(retry_after)) if retry_after else 2.0
        except ValueError:
            wait_seconds = 2.0
        time.sleep(wait_seconds)
        return client.post(url, headers=headers, params=params, json=payload)
    except httpx.HTTPError as exc:
        raise BrightDataError(
            f"Bright Data request failed: {exc.__class__.__name__}"
        ) from exc


def _get_with_retry(
    client: httpx.Client,
    url: str,
    *,
    headers: Dict[str, str],
    params: Optional[Dict[str, Any]] = None,
) -> httpx.Response:
    try:
        response = client.get(url, headers=headers, params=params)
        if response.status_code != 429 and response.status_code < 500:
            return response
        retry_after = response.headers.get("Retry-After")
        try:
            wait_seconds = max(0.0, float(retry_after)) if retry_after else 2.0
        except ValueError:
            wait_seconds = 2.0
        time.sleep(wait_seconds)
        return client.get(url, headers=headers, params=params)
    except httpx.HTTPError as exc:
        raise BrightDataError(
            f"Bright Data request failed: {exc.__class__.__name__}"
        ) from exc


def _snapshot_id(response: httpx.Response) -> Optional[str]:
    """Detects a genuine async-accepted response. Only trusts the `snapshot_id` key —
    NEVER falls back to a generic `id` field, since real data records (e.g. a TikTok
    account's own `id`, an Instagram post's `id`) legitimately carry that key too, which
    previously caused a synchronously-complete single-record response to be misdetected
    as an async trigger and polled against a snapshot that never existed."""
    try:
        payload = response.json()
    except Exception:
        return None
    if isinstance(payload, dict):
        value = payload.get("snapshot_id")
        return str(value) if value else None
    return None


def _records_from_response(response: httpx.Response, operation: str) -> List[Dict[str, Any]]:
    content_type = response.headers.get("content-type", "")
    text = response.text or ""
    if "jsonl" in content_type.lower():
        records: List[Dict[str, Any]] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except Exception as exc:
                raise BrightDataError(f"Bright Data {operation} returned invalid JSONL") from exc
            if isinstance(record, dict):
                records.append(record)
        return records
    try:
        payload = response.json()
    except Exception as exc:
        raise BrightDataError(f"Bright Data {operation} returned invalid JSON") from exc
    if isinstance(payload, list):
        return [record for record in payload if isinstance(record, dict)]
    if isinstance(payload, dict):
        return [payload]
    raise BrightDataError(f"Bright Data {operation} returned an unexpected payload shape")


def wait_for_snapshot(
    snapshot_id: str,
    *,
    token: Optional[str] = None,
    timeout: Optional[float] = None,
    poll_interval: Optional[float] = None,
    client: Optional[httpx.Client] = None,
) -> List[Dict[str, Any]]:
    auth_token = token or get_bright_data_token()
    if not auth_token:
        raise BrightDataError("BRIGHT_DATA_API_TOKEN not configured")
    timeout_seconds = timeout or _positive_float_env("BRIGHT_DATA_TIMEOUT_SECONDS", 180.0)
    interval_seconds = poll_interval or _positive_float_env("BRIGHT_DATA_POLL_INTERVAL_SECONDS", 5.0)
    deadline = time.monotonic() + timeout_seconds
    owned_client = client is None
    http = client or httpx.Client(timeout=timeout_seconds)
    headers = _headers(auth_token)
    try:
        while True:
            if time.monotonic() >= deadline:
                raise BrightDataError(
                    f"Bright Data snapshot {snapshot_id} timed out after {timeout_seconds:g}s"
                )
            response = _get_with_retry(
                http,
                f"{BRIGHT_DATA_BASE_URL}/datasets/v3/progress/{snapshot_id}",
                headers=headers,
            )
            if not (200 <= response.status_code < 300):
                _raise_http_error(response, "snapshot progress check")
            try:
                progress = response.json()
            except Exception as exc:
                raise BrightDataError("Bright Data progress endpoint returned invalid JSON") from exc
            status = str(progress.get("status", "")).lower() if isinstance(progress, dict) else ""
            if status == "ready":
                break
            if status in {"failed", "canceled"}:
                detail = progress.get("error") or progress.get("message") or status
                raise BrightDataError(
                    f"Bright Data snapshot {snapshot_id} {status}: {str(detail)[:300]}"
                )
            if status not in {"starting", "running"}:
                raise BrightDataError(
                    f"Bright Data snapshot {snapshot_id} returned unknown status {status!r}"
                )
            time.sleep(min(interval_seconds, max(0.0, deadline - time.monotonic())))

        response = _get_with_retry(
            http,
            f"{BRIGHT_DATA_BASE_URL}/datasets/v3/snapshot/{snapshot_id}",
            headers=headers,
            params={"format": "json"},
        )
        if not (200 <= response.status_code < 300):
            _raise_http_error(response, "snapshot download")
        return _records_from_response(response, "snapshot download")
    finally:
        if owned_client:
            http.close()


def run_dataset(
    dataset_id: str,
    inputs: List[Dict[str, Any]],
    *,
    query: Optional[Dict[str, Any]] = None,
    timeout: Optional[float] = None,
    poll_interval: Optional[float] = None,
) -> List[Dict[str, Any]]:
    tokens = _get_rotating_tokens()
    if not tokens:
        raise BrightDataError("BRIGHT_DATA_API_TOKEN not configured")
    if not inputs:
        return []

    timeout_seconds = timeout or _positive_float_env("BRIGHT_DATA_TIMEOUT_SECONDS", 180.0)
    params: Dict[str, Any] = {
        "dataset_id": dataset_id,
        "include_errors": "true",
    }
    if query:
        params.update(query)
    url = f"{BRIGHT_DATA_BASE_URL}/datasets/v3/scrape"

    last_error: Optional[Exception] = None
    for attempt_idx, token in enumerate(tokens):
        deadline = time.monotonic() + timeout_seconds
        logger.info(
            "Running Bright Data dataset %s (%s inputs, token %s)",
            dataset_id, len(inputs), _mask_token(token),
        )
        try:
            with httpx.Client(timeout=timeout_seconds) as client:
                response = _post_with_rate_limit_retry(
                    client,
                    url,
                    headers=_headers(token),
                    params=params,
                    payload={"input": inputs},
                )
                if not (200 <= response.status_code < 300):
                    _raise_http_error(response, "dataset scrape")
                snapshot_id = _snapshot_id(response)
                if snapshot_id:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise BrightDataError(
                            f"Bright Data dataset {dataset_id} timed out after {timeout_seconds:g}s"
                        )
                    return wait_for_snapshot(
                        snapshot_id,
                        token=token,
                        timeout=remaining,
                        poll_interval=poll_interval,
                        client=client,
                    )
                if response.status_code == 202:
                    raise BrightDataError("Bright Data returned HTTP 202 without a snapshot_id")
                return _records_from_response(response, "dataset scrape")
        except (BrightDataError, Exception) as exc:
            last_error = exc
            if attempt_idx + 1 < len(tokens):
                logger.warning(
                    "Bright Data dataset %s failed with token %s: %s. Failing over to next token in pool...",
                    dataset_id, _mask_token(token), exc,
                )
                continue
            raise

    if last_error:
        raise last_error
    return []


def search_instagram_urls(query: str, *, limit: int) -> List[str]:
    if limit <= 0:
        return []
    pairs = _get_rotating_serp_pairs()
    search_url = "https://www.google.com/search?" + urlencode(
        {"q": query, "num": min(limit, 100), "hl": "id", "gl": "id", "brd_json": "1"}
    )
    timeout_seconds = _positive_float_env("BRIGHT_DATA_TIMEOUT_SECONDS", 180.0)

    last_error: Optional[Exception] = None
    for attempt_idx, (token, zone) in enumerate(pairs):
        payload = {"zone": zone, "url": search_url, "format": "raw"}
        try:
            with httpx.Client(timeout=min(timeout_seconds, 60.0)) as client:
                response = _post_with_rate_limit_retry(
                    client,
                    f"{BRIGHT_DATA_BASE_URL}/request",
                    headers=_headers(token),
                    payload=payload,
                )
            if not (200 <= response.status_code < 300):
                _raise_http_error(response, "SERP request")
            try:
                data = response.json()
            except Exception as exc:
                raise BrightDataError("Bright Data SERP returned invalid JSON") from exc
            organic = data.get("organic", []) if isinstance(data, dict) else []
            urls: List[str] = []
            for item in organic:
                if not isinstance(item, dict):
                    continue
                link = item.get("link")
                if isinstance(link, str) and link:
                    urls.append(link)
                    if len(urls) >= limit:
                        break
            return urls
        except (BrightDataError, Exception) as exc:
            last_error = exc
            if attempt_idx + 1 < len(pairs):
                logger.warning(
                    "Bright Data SERP failed with token %s / zone %s: %s. Failing over to next token...",
                    _mask_token(token), zone, exc,
                )
                continue
            raise

    if last_error:
        raise last_error
    return []
