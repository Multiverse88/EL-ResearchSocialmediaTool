# Bright Data Scraper Cutover Design

## Goal

Replace Apify completely with Bright Data for Instagram and TikTok profile and topic research. Preserve the existing database and ingestion contracts, keep the current free local fallbacks, and make broad-topic Instagram research work through Bright Data SERP discovery followed by structured Instagram collection.

The user-facing chat remains synchronous: it waits for the scrape result, reports real collected records, and never presents provider or access failures as a valid zero-post research result.

## Scope

### Included

- Instagram profile posts and reels through Bright Data.
- TikTok profile posts through Bright Data.
- TikTok topic discovery by keyword through Bright Data.
- Instagram broad-topic discovery through Bright Data SERP, followed by Instagram Posts/Reels collection.
- Bright Data synchronous responses and asynchronous snapshot polling.
- Normalization into the existing `raw_post` shapes consumed by `ingest_scraped_batch`.
- Existing topic expansion, canonical topic labels, freshness behavior, account attribution, database schema, and chat action orchestration.
- Existing free fallbacks: Instaloader for Instagram; TikTokApi/Playwright and raw HTML for TikTok.
- Configuration, deployment documentation, logs, user-facing backend labels, and tests.

### Excluded

- Background-job APIs, webhooks, queues, and UI job-status screens.
- Scraper Studio custom collectors.
- Bright Data Web Unlocker or Browser API.
- Changes to analytics formulas, topic expansion semantics, database schema, or monitored-account behavior.
- Collecting follower history, reach, impressions, or other metrics not returned by the selected record datasets.

## Provider Architecture

Create `src/scrapers/bright_data_client.py` as the only module that communicates with Bright Data. It owns:

- `BRIGHT_DATA_API_TOKEN` lookup.
- `BRIGHT_DATA_SERP_ZONE` lookup for Instagram broad-topic discovery.
- Bearer authentication.
- Synchronous `/datasets/v3/scrape` requests.
- Asynchronous `/datasets/v3/trigger` requests.
- Snapshot progress polling and result download.
- A default 180-second overall wait timeout and configurable poll interval.
- Bright Data error classification without leaking credentials.

Delete `src/scrapers/apify_client.py`. Remove `APIFY_API_TOKEN`, actor IDs, Apify-specific input payloads, Apify logs, and Apify backend labels. This is a clean cutover: no provider compatibility alias and no runtime provider selector.

Official Bright Data dataset IDs are constants in `bright_data_client.py`. The integration uses the documented Instagram and TikTok dataset contracts rather than user-created Scraper Studio collectors. Dataset IDs are not deployment configuration because changing one changes the response schema and therefore requires a code and adapter review.

## Client Contract

The Bright Data client exposes operations at the dataset-request level rather than platform-specific business methods:

- Run a synchronous scrape and return a list of records when Bright Data returns data immediately.
- Recognize HTTP 202 or a response containing `snapshot_id`, then poll the snapshot.
- Trigger discovery explicitly through `/trigger`, poll until terminal state, and download JSON records.
- Return only JSON object records; malformed top-level payloads are provider errors.

The poller accepts `starting` and `running`, succeeds only on `ready`, and fails on `failed`, `canceled`, or timeout. It uses a monotonic deadline so request and sleep time cannot extend the configured overall timeout.

## Data Flows

### Instagram profile

1. Build the canonical profile URL from the normalized username.
2. Run Bright Data discovery for posts from the profile URL.
3. Run Bright Data discovery for reels from the profile URL.
4. Normalize both record types into the existing Instagram `raw_post` contract.
5. Merge by platform post ID, sort by posting time, and enforce the combined `max_posts` limit.
6. Pass the result to `ingest_scraped_batch` with the requested account.
7. Return backend `bright_data` on success.

If Bright Data is unavailable, returns provider error records only, or produces a malformed payload, fall back to Instaloader. A clean empty dataset is distinct from provider failure: it may trigger the coverage fallback, but it is reported as a genuine no-record result if the fallback is also clean and empty. When both providers fail, preserve both provider errors in the final error string.

### TikTok profile

1. Build the canonical TikTok profile URL.
2. Run Bright Data post discovery by profile URL.
3. Normalize records into the TikTok `raw_post` contract.
4. Sort, cap to `max_posts`, and pass to `ingest_scraped_batch` with the requested account.
5. Return backend `bright_data` on success.

If Bright Data is unavailable or fails, use TikTokApi/Playwright, then raw HTML according to the current local fallback order.

### TikTok topic

1. Expand the canonical topic through `expand_topic_queries`.
2. Submit each query to Bright Data TikTok post discovery by keyword.
3. Normalize records and attribute each post to its returned profile username.
4. Dedupe by TikTok post ID across all variants.
5. Filter records older than `since` when provided.
6. Stamp every stored post with the canonical `topic_label`, never the variant query.
7. Enforce the platform quota and ingest.

### Instagram broad topic

Bright Data's Instagram record APIs collect known targets and profile-derived posts; broad content discovery uses the official Bright Data social-listener pattern:

1. Expand the canonical topic through `expand_topic_queries`.
2. Query Bright Data SERP for Instagram post and reel URLs using narrow site-qualified searches.
3. Extract only canonical `instagram.com/p/` and `instagram.com/reel/` URLs.
4. Normalize URLs and dedupe them across all query variants before paid record collection.
5. Split URLs by post/reel type and send them to the corresponding Bright Data Instagram dataset in batches within API limits.
6. Normalize the structured records, filter by `since`, attribute returned usernames, stamp the canonical topic label, enforce the platform quota, and ingest.

A SERP failure affects Instagram topic research only. TikTok topic discovery and both profile scrapers continue independently.

## Normalization

Provider response shape is isolated in small adapter functions located with the relevant platform scraper. Downstream ingestion does not learn Bright Data field names.

Instagram adapters map, with defensive alternatives where official schemas differ between posts and reels:

- post ID or shortcode
- posting username
- description/caption
- primary image or video URL
- likes
- comments
- video views/play count when present
- posted timestamp
- content type (`feed` or `reel`)

TikTok adapters map:

- post ID
- profile username
- description
- video URL
- likes/digg count
- comments
- play count/views
- creation timestamp

Missing engagement counts become zero only when the field is absent or null. Negative sentinel values are clamped to zero. A record without a stable post ID is rejected. Provider error records are not treated as content.

## Freshness and Limits

The existing `since` value remains an application contract. Bright Data inputs do not need to support a uniform date field: normalized records are filtered locally by their posting timestamp before ingestion. Unparseable timestamps are retained only when no `since` filter was requested.

`max_posts` and `max_posts_per_platform` remain hard output caps. Discovery may inspect more candidates only where required to compensate for invalid, duplicate, or old records, but it must not fan out without a bounded candidate cap.

For Instagram topic research, SERP URLs are deduplicated before the paid Instagram record request. For both platforms, records are deduplicated across expanded query variants before ingestion.

## Error Handling

- `401 Unauthorized`: token is invalid or missing required credentials; do not retry.
- `403 Forbidden`: product, dataset, compliance, or zone access is not active; do not retry.
- `429 Too Many Requests`: honor `Retry-After` once. Without it, wait two seconds and retry once. Never retry immediately or repeatedly.
- Other `4xx`: fail with the HTTP status and a bounded, sanitized provider message.
- `5xx` and transport failures on read-only progress/download requests: one bounded retry is allowed. Do not automatically retry a trigger or scrape POST after an ambiguous transport failure because that can create a duplicate paid job.
- Snapshot `failed` or `canceled`: fail immediately with snapshot status/details.
- Snapshot timeout: fail after the overall 180-second deadline; do not claim partial success.
- Mixed record/error snapshots: retain valid records and report the failed inputs in logs. If no valid record remains and error records exist, treat the operation as failed. A clean empty record list remains a genuine no-record result.

No log, exception, or user-facing error may include bearer tokens. Error bodies are length-bounded.

## User-Facing Behavior

Chat-driven scraping continues to block until the Bright Data run completes, times out, or falls back. Existing progress callbacks are reused with provider-neutral Indonesian messages such as:

- preparing discovery
- waiting for Bright Data results
- normalizing and deduplicating records
- storing posts

Receipts identify `bright_data` only when Bright Data produced the ingested result. A configuration or provider failure must be described as a failure or fallback, not as a valid research finding of zero posts.

## Configuration

Required for all Bright Data scraper calls:

- `BRIGHT_DATA_API_TOKEN`

Required for Instagram broad-topic SERP discovery:

- `BRIGHT_DATA_SERP_ZONE`

Optional operational controls:

- `BRIGHT_DATA_TIMEOUT_SECONDS` (default `180`)
- `BRIGHT_DATA_POLL_INTERVAL_SECONDS` (default `5`)

Remove `APIFY_API_TOKEN` from examples and deployment instructions. Keep existing local-fallback credentials and settings.

## Tests

### Client tests

- Missing token.
- Immediate successful JSON response.
- HTTP 202 or snapshot response followed by `starting`/`running`/`ready` and result download.
- Snapshot `failed` and `canceled`.
- Overall timeout using a controlled clock or patched sleep.
- `401`, `403`, and other bounded errors.
- `429` honors `Retry-After` and retries only once.
- Malformed response and non-list result rejection.
- Token never appears in errors or logs.

### Adapter tests

- Instagram post schema.
- Instagram reel schema and view-count precedence.
- TikTok post schema.
- Missing ID rejection.
- Null and negative metric handling.
- Timestamp normalization.

### Flow tests

- Bright Data is preferred when configured.
- Instagram profile merges posts and reels and enforces one combined limit.
- Instagram falls back to Instaloader on provider failure.
- TikTok falls back through Playwright and HTML according to availability.
- TikTok topic uses keyword discovery and preserves canonical topic labels.
- Instagram broad topic uses SERP, deduplicates URLs before collection, and preserves canonical topic labels.
- `since` removes old records.
- Partial provider error records do not hide valid records.
- No Apify import, environment key, backend label, or documentation reference remains.

### Verification

Run the full unit suite once after integration. Then perform real smoke checks using the available Bright Data token:

1. Authenticate and collect one known Instagram profile.
2. Collect one known TikTok profile.
3. Discover a narrow TikTok keyword.
4. Run one Instagram broad-topic SERP-to-post flow.

Classify live failures precisely as invalid token, Scraper API not activated, dataset access denied, SERP zone missing, quota/rate limit, snapshot failure, or empty real result. Do not weaken mocked tests or silently use local fallbacks to claim Bright Data verification.

## Migration Acceptance Criteria

- Apify code and configuration are removed completely.
- Profile scraping works through Bright Data for Instagram and TikTok when access is active.
- TikTok topics use native Bright Data keyword discovery.
- Instagram broad topics use Bright Data SERP discovery followed by Instagram record collection.
- All ingested records retain current account, topic, deduplication, freshness, and analytics behavior.
- Chat receipts and logs report the backend that actually produced data.
- Provider failure is distinguishable from a valid zero-record result.
- Existing local fallbacks remain functional.
- Full automated test suite passes.
- Live smoke testing either succeeds or produces a precise access/configuration diagnosis.