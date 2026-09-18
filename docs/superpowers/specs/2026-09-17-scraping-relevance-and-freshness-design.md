# Scraping Relevance and Freshness Pipeline

## Problem

Topic scraping produces irrelevant or stale data because the system has four compounding gaps:

1. **Hashtag-only keyword mapping**: Multi-word queries like `pendirian PT` or `izin usaha baru` are stripped down to `#pendirianpt` or `#izinusahabaru`, which frequently return zero results or off-topic spam.
2. **Missing recency constraints**: Calls to Apify actors do not pass date filters (`onlyPostsNewerThan`) or newest-first sorting, causing returned batches to be dominated by older posts.
3. **False-positive freshness checks**: `Database.get_topic_last_scraped` treats a topic as fresh whenever any post matches `topic = ? OR caption LIKE ?`, regardless of whether real topic-level scraping has run or when the content was actually published.
4. **Passive collection**: Content is refreshed only when an end-user asks a chat question, with no automated periodic refresh for high-priority topics.

## Goals

1. Expand topic keywords into smart multi-tag and search queries rather than a single collapsed alphanumeric slug.
2. Enforce explicit recency bounds on scraper actor inputs and queries.
3. Replace fuzzy topic freshness checks with an explicit `topic_scrapes` audit log recording exact keyword, timestamp, item count, and status.
4. Separate `content_freshness` (how recently the post was published) from `scrape_freshness` (how recently the scraper ran).
5. Provide a lightweight background refresh worker for registered topics so data is warm before users ask.

## Non-goals

- Scraping private profiles or bypassing platform anti-scraping protections.
- Unlimited historical scraping (bounded to configured window, e.g. last 30 to 90 days).
- Changing the OpenWebUI OpenAI-compatible endpoint schema.

## Architecture

```text
User Query / Scheduled Trigger
           │
           ▼
Topic Normalizer & Expander
   ├─ expand "pendirian PT" -> ["pendirianpt", "ptperorangan", "legalkonsultan"]
   └─ extract search keywords + hashtags
           │
           ▼
Scraper Dispatcher (Apify / Fallbacks)
   ├─ inject recency boundary (onlyPostsNewerThan / date cutoff)
   └─ sort newest first
           │
           ▼
Ingest & Relevance Filter
   ├─ caption keyword density & language check
   ├─ upsert posts with posted_at + scraped_at
   └─ record topic_scrape audit entry
           │
           ▼
Database & Freshness Gate
   └─ evaluate true topic freshness from topic_scrapes table
```

## Modules and Contracts

### 1. `src/scrapers/keyword_scraper.py`
- Add `expand_topic_queries(keyword: str) -> List[str]` to generate realistic hashtags and search terms for Indonesian corporate/legal context.
- Update Apify inputs to pass `onlyPostsNewerThan` and recency parameters supported by actors.
- Attribute results to source query while preserving author metadata.

### 2. `src/db.py`
- Add a dedicated `topic_scrapes` table:
  - `id`: string UUID
  - `keyword`: normalized topic string
  - `platform`: instagram | tiktok | all
  - `posts_found`: integer
  - `scraped_at`: ISO8601 UTC timestamp
  - `status`: success | failed | empty
- Update `get_topic_last_scraped(keyword)` to query `topic_scrapes` directly rather than fuzzy `caption LIKE` matching.

### 3. `src/claude_client.py`
- Update `_ensure_topic_freshness` to evaluate both:
  - Scrape age (when the scraper last ran for this topic).
  - Content age (newest `posted_at` available for the topic).
- Expose freshness diagnostics in stream reasoning chunks.

### 4. Background Scheduler
- Add periodic background job runner in `src/scrapers/runner.py` for all topics with `monitoring_enabled=True`.

## Verification Plan

1. **Unit tests**:
   - Query expansion produces multi-token variants for legal/corporate topics.
   - Apify payload includes expected recency and sort parameters.
   - `get_topic_last_scraped` returns None when only unrelated caption matches exist.
2. **Integration tests**:
   - Scrape topic records `topic_scrapes` audit entry.
   - Subsequent chat calls respect configured staleness TTL.
