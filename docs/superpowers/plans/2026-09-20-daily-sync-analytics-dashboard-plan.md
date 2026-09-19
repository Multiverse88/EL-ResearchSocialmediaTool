# Implementation Plan: Automated Daily Sync, Competitor Intelligence, & Analytics Dashboard

**Spec:** `docs/superpowers/specs/2026-09-20-daily-sync-analytics-dashboard-design.md`  
**Date:** 2026-09-20  
**Target Branch:** `autoresearch/session-20260916`  

---

## Phase 1: Background Daily Scheduler & Parallel Sync Pipeline

### 1.1 Goal
Create an automated background daemon that triggers at 08:00 WIB (01:00 UTC) every day, plus a manual trigger endpoint `POST /api/cron/daily-sync`, executing scraping across 5 brand targets and 3 competitor topics in parallel using multi-token rotation with a 10-post cap.

### 1.2 Tasks
- [ ] Create `src/scheduler.py`:
  - Implement WIB/UTC time conversion (`get_current_wib_time()`).
  - Implement `should_run_daily_sync(last_run_timestamp)` with auto-catchup logic.
  - Implement `execute_daily_sync(db, max_posts=10, max_workers=2)`:
    - Target brand accounts:
      - Instagram: `id.easylegal`, `id.easytax`, `id.easyoffice`
      - Threads: `id.easylegal`
      - TikTok: `id.easylegal`
    - Target competitor topics:
      - `pendirian pt` (IG + TikTok)
      - `konsultasi pajak` (IG + TikTok)
      - `virtual office jakarta` (IG + TikTok)
    - Run using `concurrent.futures.ThreadPoolExecutor(max_workers=2)`.
    - Log completion and any per-target errors to `scrape_logs` table.
  - Implement `DailySyncSchedulerThread` daemon running loop every 60 seconds.
- [ ] In `src/server.py`:
  - Start `DailySyncSchedulerThread` during application startup (`@app.on_event("startup")` or lifespan).
  - Add endpoint `POST /api/cron/daily-sync` protected by `require_api_key`.
- [ ] Create `tests/test_daily_scheduler.py`:
  - Test time calculation and catchup condition.
  - Test parallel sync execution using mock scraper functions.
  - Test endpoint `POST /api/cron/daily-sync`.

---

## Phase 2: Analytics & Historical Trends Aggregation Engine

### 2.1 Goal
Build an analytics aggregation engine that queries SQLite for daily, weekly, and monthly time-series metrics, virality scores, and competitor benchmarks.

### 2.2 Tasks
- [ ] Create `src/analytics.py`:
  - Implement `get_analytics_overview(db, timeframe_days=30)`:
    - Total posts, total views, average engagement rate, active platforms.
  - Implement `get_timeseries_trends(db, timeframe="daily"|"weekly"|"monthly", platform=None)`:
    - Date-grouped views, likes, and post counts.
  - Implement `get_viral_leaderboard(db, limit=10, is_own_brand=None, topic=None)`:
    - Top posts ranked by views / virality score with thumbnail URL, caption hook, platform, author.
  - Implement `get_competitor_comparison(db, topic=None)`:
    - Side-by-side metrics: EasyCorp accounts (`is_own_brand = 1`) vs Competitors (`is_own_brand = 0`).
- [ ] In `src/server.py`:
  - Add endpoints:
    - `GET /api/analytics/overview`
    - `GET /api/analytics/trends`
    - `GET /api/analytics/leaderboard`
    - `GET /api/analytics/competitor-comparison`
- [ ] Create `tests/test_analytics.py`:
  - Test calculation formulas, timeframe filtering, and competitor separation.

---

## Phase 3: Looker Studio-Style Web Dashboard (`/analytics`)

### 3.1 Goal
Deliver a clean, responsive single-page analytics dashboard directly served by FastAPI at `/analytics` with scorecards, Chart.js time-series graphs, topic breakdowns, and a viral hook table.

### 3.2 Tasks
- [ ] Create `src/dashboard.py` (or `src/templates/analytics.html`):
  - Modern Looker Studio layout:
    - Header with logo, current date range picker (*Hari Ini*, *7 Hari*, *30 Hari*, *Semua*), and platform selector.
    - 4 Metric Scorecards (Total Posts, Total Views, Avg Engagement, Virality Peak).
    - Chart 1: Time-Series Area Chart (Daily Views & Engagement trajectory).
    - Chart 2: Grouped Bar Chart (Brand vs Competitor performance per topic).
    - Table: Top Viral Posts Leaderboard with direct post links and hook preview.
    - Manual "Sync Sekarang" button calling `POST /api/cron/daily-sync`.
- [ ] In `src/server.py`:
  - Mount endpoint `GET /analytics` returning `HTMLResponse`.
  - Read `ANALYTICS_DASHBOARD_URL` from environment (defaulting to `/analytics`).
- [ ] Create `tests/test_dashboard.py`:
  - Test `GET /analytics` returns HTTP 200 with required HTML components.

---

## Phase 4: Open WebUI Chat & Link Integration

### 4.1 Goal
Connect the AI chat experience in Open WebUI to the new analytics dashboard and ensure responses link directly to it.

### 4.2 Tasks
- [ ] In `src/claude_client.py`:
  - Inject dashboard guidance into `DEFAULT_SYSTEM_PROMPT`:
    - Instruct the model to provide data-backed answers and append a clickable markdown link to `{ANALYTICS_DASHBOARD_URL}` when discussing trends or statistics.
- [ ] In `.env.example` and `docker-compose.yml`:
  - Add `ANALYTICS_DASHBOARD_URL`.
- [ ] Run full test suite (`python3 -m unittest discover -s tests`).
- [ ] Verify git status and commit.
