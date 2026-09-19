# Technical Design Specification: Automated Daily Sync, Competitor Intelligence, & Looker Studio-Style Analytics Dashboard

**Date:** 2026-09-20  
**Status:** Approved by User  
**Target Branch:** `autoresearch/session-20260916`  
**System:** EasyCorp Social Media Intelligence & Research Engine  

---

## 1. Executive Summary & Goals

### 1.1 Context & Problem
The existing social media research tool performs live scraping synchronously whenever a user sends an on-demand chat prompt via Open WebUI. Because scraping modern platforms (Instagram, TikTok, Threads) via anti-bot infrastructure takes 15–30 seconds per profile/topic, multi-account analysis causes long wait times (40–60s) in the chat window. Furthermore, marketing users lack a holistic historical view of surging daily, weekly, and monthly trends across EasyCorp brand accounts and competitor benchmarks.

### 1.2 Objectives
1. **Instant Chat Response (< 2 seconds):** The AI answers user queries instantly by pulling pre-warmed data from a local SQLite database, falling back to parallel live scraping only when explicitly commanded ("refresh data sekarang").
2. **Automated Daily Sync (08:00 WIB / 01:00 UTC):** A background daemon automatically scrapes the latest 10 posts from designated brand accounts and core competitor topics every morning.
3. **Dedicated Target Brand Accounts:**
   - **Instagram:** `@id.easylegal`, `@id.easytax`, `@id.easyoffice`
   - **Threads:** `@id.easylegal`
   - **TikTok:** `@id.easylegal`
4. **Competitor Intelligence via Topic Mapping:** Automatically scrape public competitor content in identical niches (`is_own_brand = 0`) to provide side-by-side virality and engagement benchmarks.
5. **Permanent Data Retention with In-Place Metric Updates:** Content is stored permanently so long-term trends (monthly/quarterly) can be analyzed, while likes, views, and comments update dynamically upon subsequent scrapes.
6. **Built-in Looker Studio-Style Dashboard (`/analytics`):** A lightweight, responsive web analytics interface accessible via a configurable domain (`ANALYTICS_DASHBOARD_URL`), featuring KPI scorecards, time-series charts, topic comparisons, and a viral hook leaderboard.
7. **Seamless Open WebUI Integration:** Clickable dashboard buttons/links embedded within AI responses, system greetings, and sidebar configurations.

---

## 2. Architecture & Data Flow

```
+---------------------------------------------------------------------------------------+
|                                    DAILY SCHEDULER                                    |
|                       Trigger: 08:00 WIB (01:00 UTC) or Catch-Up                       |
+---------------------------------------------------------------------------------------+
                                           |
                                           v
+---------------------------------------------------------------------------------------+
|                           PARALLEL EXECUTION PIPELINE                                 |
|                       ThreadPoolExecutor(max_workers=2-3)                             |
|             Rotates requests across BRIGHT_DATA_API_TOKEN pool (Token A / B)          |
+---------------------------------------------------------------------------------------+
        |                                                           |
        v                                                           v
  [Brand Accounts (5)]                                     [Competitor Topics (3)]
  - IG: @id.easylegal, @id.easytax, @id.easyoffice         - pendirian pt (IG/TT)
  - Threads: @id.easylegal                                 - konsultasi pajak (IG/TT)
  - TikTok: @id.easylegal                                  - virtual office jakarta (IG/TT)
  (Limit: 10 posts per target)                             (Limit: 10 posts per target)
        \                                                           /
         \---------------------------\ /---------------------------/
                                      v
+---------------------------------------------------------------------------------------+
|                                 SQLITE DATABASE                                       |
|  - Table: accounts (with is_own_brand = 1 for EasyCorp, 0 for competitors)           |
|  - Table: posts (UNIQUE platform + platform_post_id -> Upsert metrics)               |
|  - Table: scrape_logs (Run history & health monitoring)                               |
+---------------------------------------------------------------------------------------+
               |                                                   |
               v                                                   v
+-----------------------------+                     +-----------------------------------+
|     OPEN WEBUI CHAT         |                     |   LOOKER STUDIO-STYLE DASHBOARD   |
|  - Reads instant DB (<0.1s) |                     |   URL: /analytics                 |
|  - Progress bar for live    |                     |   - KPI Cards (Views, ER, Posts)  |
|  - Link to /analytics       |                     |   - Daily/Weekly/Monthly Trends   |
+-----------------------------+                     |   - Competitor Hook Leaderboard   |
                                                    +-----------------------------------+
```

---

## 3. Detailed Component Specifications

### 3.1 Background Daily Scheduler (`src/scheduler.py`)
- **Execution Interval:** Evaluated every 60 seconds by a daemon thread started inside FastAPI's startup lifecycle (`server.py`).
- **Timezone Awareness:** Calculates WIB (`UTC+7`). Fires when `current_time.hour == 8 and current_time.minute == 0` (or `01:00 UTC`).
- **Auto-Catchup Logic:** If the server boots after 08:00 WIB and `last_daily_sync_date != today_wib_date`, the scheduler triggers an immediate catch-up run to prevent data gaps following deployments or maintenance.
- **Manual Trigger Endpoint:** `POST /api/cron/daily-sync`
  - Guarded by `X-API-Key: API_SECRET_KEY` (or open in development if unset).
  - Returns JSON summary: total targets processed, posts added, duration, and worker statuses.

### 3.2 Target Accounts & Topic Mapping Configuration
The scheduler synchronizes the following target roster with a strict cap of **10 posts** per target:

| Target Identifier | Platform | Classification | Linked Domain |
|---|---|---|---|
| `@id.easylegal` | Instagram | Brand (`is_own_brand = 1`) | Legalitas & Pendirian PT |
| `@id.easytax` | Instagram | Brand (`is_own_brand = 1`) | Pajak & SPT |
| `@id.easyoffice` | Instagram | Brand (`is_own_brand = 1`) | Virtual Office |
| `@id.easylegal` | Threads | Brand (`is_own_brand = 1`) | Legalitas & Bisnis |
| `@id.easylegal` | TikTok | Brand (`is_own_brand = 1`) | Edukasi Legal Pendek |
| `pendirian pt` | IG & TikTok | Competitor (`is_own_brand = 0`) | Pasar Legalitas Umum |
| `konsultasi pajak` | IG & TikTok | Competitor (`is_own_brand = 0`) | Pasar Konsultan Pajak |
| `virtual office jakarta` | IG & TikTok | Competitor (`is_own_brand = 0`) | Pasar Sewa Kantor |

### 3.3 Database Retention & Upsert Rules (`src/db.py`)
- **Retention:** Permanent (`DELETE` operations are strictly disallowed during scheduled jobs).
- **Upsert Invariants:**
  - `posts` table uses `UNIQUE(platform, platform_post_id)`.
  - When an existing post is scraped again, the record is updated in-place:
    ```sql
    ON CONFLICT(platform, platform_post_id) DO UPDATE SET
        likes = excluded.likes,
        comments = excluded.comments,
        views = excluded.views,
        engagement_rate = excluded.engagement_rate,
        scraped_at = excluded.scraped_at
    ```
  - Original `posted_at` timestamp is preserved verbatim to ensure accurate historical time-series aggregation.

### 3.4 Analytics & Trends Engine (`src/analytics.py`)
Provides analytical aggregation across 3 discrete timeframes:
1. **Daily (1–3 Days):** Immediate viral spikes, day-over-day changes.
2. **Weekly (7 Days):** Content format distribution (Reels vs Carousel vs TikTok) and week-over-week growth percentage ($\pm\%$).
3. **Monthly (30 Days):** Pillar engagement shift and brand vs competitor share of voice.

#### Core REST Endpoints:
- `GET /api/analytics/overview`  
  Returns top-level KPIs: Total monitored posts, total views, average engagement rate, and active brand breakdown.
- `GET /api/analytics/trends?timeframe=daily|weekly|monthly&platform=all|instagram|tiktok|threads`  
  Returns time-series data points (date, views, likes, count) for line and bar charts.
- `GET /api/analytics/leaderboard?limit=10&is_own_brand=all|1|0`  
  Returns top-ranking posts sorted by virality score / views, displaying author, platform, engagement, and initial caption hook.
- `GET /api/analytics/competitor-comparison`  
  Returns comparative breakdown between EasyCorp brand accounts and market competitors in the same topics.

### 3.5 Looker Studio-Style Web Dashboard (`/analytics`)
- **Route:** `GET /analytics` served directly by FastAPI as a standalone, responsive HTML5/CSS/JavaScript single-page application.
- **Visual Design (Looker Studio Inspired):**
  - Modern clean theme with high-contrast data visualization (Chart.js via CDN).
  - Top navigation with Quick Date Range picker (*Hari Ini*, *7 Hari Terakhir*, *30 Hari Terakhir*, *Semua*).
  - Platform filter pills (*Semua*, *Instagram*, *TikTok*, *Threads*).
  - 4 Key Metric Scorecards with upward/downward trend indicators ($\pm\%$).
  - Dual Main Charts:
    1. Time-Series Area Chart: Daily views and likes trajectory.
    2. Grouped Bar Chart: Brand vs Competitor performance per topic.
  - Interactive Table: Top 10 viral posts with direct permalinks and hook analysis.
  - Manual "Sync Sekarang" button with modal confirmation and live spinner.
- **Configurable Domain URL:**  
  - Controlled by environment variable: `ANALYTICS_DASHBOARD_URL`.
  - Defaults to relative path `/analytics` or temporary domain (e.g., `https://social-analytics.easylegal.my.id/analytics`).
  - Chat responses link directly to `ANALYTICS_DASHBOARD_URL`.

### 3.6 AI Chat & Open WebUI Integration (`src/claude_client.py`)
- **Instant Response Default:** AI retrieves factual summaries directly from SQLite in < 0.1s.
- **Dashboard Action Link Injection:** When answering queries involving statistics, analytics, or comparisons, the system prompt instructs the assistant to append an interactive markdown button/link:
  ```markdown
  📊 **Ingin lihat grafik interaktif selengkapnya?**
  [👉 Buka Dashboard Analytics & Tren Lengkap]({ANALYTICS_DASHBOARD_URL})
  ```
- **Live Progress Streaming:** When an explicit live scrape is requested, SSE reasoning chunks stream real-time progress messages (`"Mengambil post Instagram @id.easytax..."`) directly to Open WebUI.

---

## 4. Error Handling, Isolation, & Resilience

1. **Per-Target Failure Isolation:** In the daily sync, if a single account fails (e.g. TikTok anti-bot block or temporary private profile), it logs a warning in `scrape_logs` and continues processing the remaining 7 targets without interruption.
2. **Multi-Token Failover:** Bright Data API calls rotate automatically between Token A and Token B. If Token A receives HTTP 429 or network timeout, the request fails over seamlessly to Token B.
3. **Database Concurrency (WAL Mode):** SQLite is initialized with `PRAGMA journal_mode = WAL;` and `PRAGMA synchronous = NORMAL;`, permitting simultaneous read queries from chat users while background threads write incoming scrape data.
4. **Environment Fallback:** If `ANALYTICS_DASHBOARD_URL` is omitted, all generated links fall back safely to `/analytics`.

---

## 5. Verification & Testing Strategy

1. **Unit & Integration Tests:**
   - Test scheduler time conversion (08:00 WIB == 01:00 UTC) and catch-up trigger logic.
   - Test parallel execution across mocked multi-token endpoints.
   - Test database upsert idempotency and permanent retention.
   - Test analytics aggregation queries across daily, weekly, and monthly intervals.
   - Test dashboard endpoint `GET /analytics` returning valid 200 HTML with embedded charts.
2. **End-to-End Verification:**
   - Execute `POST /api/cron/daily-sync` and verify database records, scrape logs, and dashboard chart updates.
   - Verify Open WebUI SSE streaming returns instant context for seeded accounts and outputs valid dashboard links.
