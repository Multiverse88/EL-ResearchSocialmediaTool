# Chat-to-Apify Action Orchestrator

## Problem

The chat path treats every message as a research question. A command such as `rekam datanya ganti jadi id.easylegal` is resolved as a topic query for `rekam`; it neither changes the monitored account nor invokes a profile scraper. The AI tool registry contains read-only research tools, while Apify is reached only from explicit scraper functions. A valid `APIFY_API_TOKEN` therefore does not make arbitrary chat commands execute Apify actions.

The deployed OpenAI-compatible chat endpoint also accepts calls without authenticating the caller. Allowing chat-driven mutations on that endpoint without a new authorization boundary would expose Apify spend and database state to unauthorized callers.

## Goals

1. Translate Indonesian or English chat requests into typed scraping and monitoring actions.
2. Execute profile and topic collection through the existing Apify-backed scraper pipeline.
3. Keep data at most six hours stale unless the user explicitly forces a refresh.
4. Distinguish one-time profile research from scheduled monitoring.
5. Allow authenticated OpenWebUI users to execute actions automatically without a confirmation turn.
6. Stream truthful action progress and compose the final answer from persisted results.
7. Preserve existing posts when monitoring targets are renamed or disabled.

## Non-goals

- Letting the model choose arbitrary Apify actors, URLs, SQL, or shell commands.
- Permanently deleting scraped history from a chat command.
- Authorizing individual OpenWebUI roles; every user who can access the configured internal OpenWebUI instance has the same action capability.
- Replacing the existing normalizers, ingest pipeline, or scheduled runner.
- Treating an AI response as proof that an external scrape succeeded.

## Decisions

The approved behavior is:

- A hybrid AI planner plus typed executor.
- Automatic execution after planning; no confirmation turn.
- OpenWebUI-internal authorization through a shared bearer secret.
- A profile becomes scheduled only after an explicit monitoring request.
- A six-hour freshness TTL, with explicit refresh language bypassing the cache.
- Monitoring removal preserves historical accounts and posts.

## Architecture

```text
OpenWebUI
  │ Authorization: Bearer CHAT_ACTION_API_KEY
  ▼
Chat endpoint
  ├─ authenticate caller
  ├─ produce typed action plan
  ├─ validate limits and targets
  ├─ apply freshness policy
  ├─ execute database and Apify actions
  ├─ persist normalized results
  └─ compose/stream a grounded response
```

The model proposes intent. Application code owns authorization, validation, actor selection, execution, persistence, and receipts.

### Modules

#### `src/chat_actions.py`

New deep module containing:

- Pydantic plan and action schemas.
- Planner prompt construction and JSON decoding.
- Deterministic fallback parsing for common explicit commands.
- Plan validation.
- Freshness decisions.
- Typed action execution.
- Structured execution receipts consumed by response composition.

The module exposes one orchestration entry point for buffered chat and one iterator-compatible entry point for streaming status events. Callers do not import individual parser or executor helpers.

#### `src/claude_client.py`

- Calls the planner before the existing research response path.
- Supplies recent conversation turns to resolve follow-up references.
- Runs the deterministic fallback if the router is unavailable or returns invalid JSON.
- Adds execution receipts and fresh database facts to the final model context.
- Emits progress events before final answer content in streaming mode.
- Never states that data is fresh unless the receipt records a successful scrape or a valid cache hit.

#### `src/db.py` and `src/models.py`

- Add `monitoring_enabled` to `Account`.
- Add account freshness lookup.
- Add monitored-account listing.
- Add operations to enable/disable monitoring and replace a monitored username.
- Invalidate affected account, post-query, summary, and username caches after every mutation.

#### `src/scrapers/runner.py`

Scheduled jobs select only accounts with `monitoring_enabled=true`. One-time chat scraping calls the existing platform profile scraper directly and does not enroll the account in scheduled monitoring.

#### `src/server.py`

- Protect `/chat` and `/v1/chat/completions` with the internal chat-action key.
- Accept `Authorization: Bearer` and `X-API-Key` forms.
- Compare secrets with `hmac.compare_digest`.
- Reject unauthorized requests before planner/model/Apify work.
- Keep `/api/health` and `/v1/models` available for service discovery and health checks.

#### Deployment configuration

- Add `CHAT_ACTION_API_KEY` to `.env.example` and the API service environment.
- Configure OpenWebUI's `OPENAI_API_KEY` from the same deployment secret.
- Remove the hardcoded OpenWebUI key from `docker-compose.yml`.
- Never log or include the key in an AI prompt.

## Action contract

The planner returns this envelope:

```json
{
  "actions": [],
  "analysis_request": "string",
  "needs_clarification": false,
  "clarification_question": null
}
```

`actions` is a discriminated union with these types:

### `scrape_profile`

Fields:

- `platform`: `instagram` or `tiktok`
- `username`: normalized without `@`
- `max_posts`: integer from 1 through 100
- `force_refresh`: boolean

Semantics: collect once if stale or forced. The account may exist for attribution and history, but remains outside scheduled monitoring unless already monitored.

### `research_topic`

Fields:

- `keyword`: non-empty normalized phrase
- `platforms`: non-empty subset of `instagram`, `tiktok`
- `max_posts_per_platform`: integer from 1 through 100
- `force_refresh`: boolean

Semantics: use the existing keyword scraper and ingest paths. Actor selection remains internal to the scraper implementation.

### `compare_profiles`

Fields:

- `targets`: two through four `{platform, username}` targets
- `max_posts`: integer from 1 through 100
- `force_refresh`: boolean

Semantics: refresh stale targets independently, then compare available database results. A failed target produces a partial receipt rather than suppressing successful targets.

### `monitor_account`

Fields:

- `platform`
- `username`
- `max_posts`

Semantics: create or resolve the account, set `monitoring_enabled=true`, and run an initial profile scrape regardless of freshness.

### `replace_monitored_account`

Fields:

- `platform`
- `old_username`
- `new_username`
- `max_posts`

Semantics:

- If the destination does not exist, rename the source account in place. Its ID, posts, brand flag, creation time, and monitoring state remain intact.
- If the destination exists, disable monitoring on the source and enable it on the destination. Both accounts retain their historical posts.
- Run an initial scrape for the destination.
- If the external scrape fails, retain the requested monitoring change and return an explicit failed-scrape receipt.

### `stop_monitoring`

Fields:

- `platform`
- `username`

Semantics: set `monitoring_enabled=false`. Preserve the account and posts for historical analysis.

## Planning and validation

The planner receives the user message, relevant recent turns, supported action schema, and current monitored-account names. It does not receive credentials or unrestricted database access.

The validator enforces:

- Only the six action types above.
- Only Instagram and TikTok.
- Valid normalized usernames and non-empty keywords.
- At most four distinct targets per message.
- At most 100 posts per target.
- No actor ID, URL, SQL, command, or arbitrary extension field.
- Explicit source and destination for account replacement.

If a source account cannot be determined uniquely, the plan becomes a clarification response and no mutation or Apify call occurs. The system may infer `easylegal_id` from “akun EasyLegal” only when exactly one monitored Instagram account unambiguously matches that brand token.

The deterministic fallback recognizes explicit variants of:

- scrape/search/refresh a profile;
- research a topic or hashtag;
- compare named profiles;
- start monitoring a named profile;
- replace one named monitored profile with another;
- stop monitoring a named profile.

Fallback parsing follows the same typed schema and validator. It cannot bypass limits.

## Actor routing

Application code owns this mapping:

- Instagram profile/topic actions use `apify/instagram-scraper` through the existing Instagram and keyword scraper adapters.
- TikTok profile/topic actions use `clockworks/tiktok-scraper` through the existing TikTok and keyword scraper adapters.

The planner cannot override actor IDs. Existing free scraper fallbacks remain available only when Apify is genuinely unconfigured or its adapter reports failure. Receipts identify which backend actually ran so the response does not imply Apify was used when a fallback supplied the data.

## Freshness policy

- Default TTL: six hours for both account and topic freshness, configured by the existing `TOPIC_STALENESS_HOURS` environment variable.
- `terbaru`, `refresh`, `scrape ulang`, `ambil ulang`, `sekarang`, and equivalent English phrases set `force_refresh=true`.
- Monitoring enrollment and replacement always force one initial scrape.
- Profile freshness uses the newest `scraped_at` for that account.
- Topic freshness uses the existing topic freshness query.
- A cache hit emits its data age in the execution receipt.
- A failed refresh may expose older cached facts only when the response labels their age and the failure.

## Monitoring migration

Schema initialization adds:

```sql
monitoring_enabled INTEGER NOT NULL DEFAULT 1
```

Existing accounts remain monitored after deployment, preserving current scheduled behavior. New account creation chooses the value explicitly:

- REST `POST /accounts`: `true`, because registration is an explicit monitoring action.
- Default seed accounts: `true`.
- `monitor_account`: `true`.
- One-time profile scrape: `false` for a newly created account.
- Per-author attribution created by topic scraping: `false`.

The runner queries monitored accounts rather than all accounts. This prevents one-time and attribution-only accounts from silently expanding scheduled Apify usage.

## Streaming behavior

The streaming endpoint emits truthful, user-visible milestones:

1. `Memahami permintaan…`
2. `Rencana: scrape profil Instagram @id.easylegal`
3. Either `Menggunakan data cache (umur 42 menit)…` or `Menjalankan Apify…`
4. `Berhasil menyimpan 27 postingan` or a precise failure message.
5. `Menganalisis hasil…`
6. Final grounded answer.

The buffered endpoint returns the same receipts in structured metadata alongside the natural-language reply.

## Error semantics

- Invalid or unavailable planner: deterministic fallback.
- Ambiguous fallback: clarification with zero side effects.
- Missing Apify token: execute the existing configured fallback and report the backend used.
- Apify quota/network/actor failure: return failure and cached-data age; never label stale data as refreshed.
- Partial comparison failure: analyze successful targets and identify unavailable targets.
- Monitoring database mutation followed by scrape failure: retain the mutation and report that collection failed.
- Username conflict during replacement: switch monitoring state without deleting either account's history.
- Unauthorized request: `401`, with no planner or scraper call.
- Rate-limited request: `429`, with no planner or scraper call.

## Security and spend controls

- Shared deployment secret gates action-capable chat endpoints.
- Existing per-IP chat rate limiting remains active after authentication.
- Target and post limits are validated in application code.
- Every external run records action type, target, requested limit, selected backend, success/failure, and collected count without credentials.
- Permanent data deletion is absent from the chat action schema.
- Prompts and scraped captions are data, never executable instructions.

## Verification

Permanent behavior tests cover:

1. A stale profile request invokes the platform scraper and answers from newly persisted posts.
2. A fresh profile request uses the database and does not invoke a scraper.
3. Explicit refresh bypasses the six-hour TTL.
4. A one-time profile scrape leaves `monitoring_enabled=false`.
5. `monitor_account` enables scheduling and performs an initial scrape.
6. Account replacement preserves history and changes the scheduled target.
7. Destination conflicts preserve both histories and transfer monitoring state.
8. `stop_monitoring` removes the account from runner targets without deleting posts.
9. Topic-created author accounts remain outside scheduled monitoring.
10. Requests with missing or incorrect bearer credentials cannot invoke the planner or scraper.
11. Unknown actions, actor IDs, URLs, and out-of-range limits fail validation.
12. Planner failure reaches deterministic fallback.
13. Ambiguous mutation produces clarification and zero side effects.
14. Apify failure is reported and never represented as fresh success.
15. Partial comparisons retain successful results and name failed targets.
16. Streaming events appear in execution order and match the execution receipt.

The final smoke scenario sends an authenticated OpenAI-compatible chat request through the actual FastAPI endpoint with Apify HTTP mocked at its adapter boundary. It verifies authorization, planning, profile ingestion, database state, progress events, and the final grounded response. A separate deployment check verifies `APIFY_API_TOKEN` and `CHAT_ACTION_API_KEY` are present inside the API container before a paid actor is invoked.

## Acceptance criteria

- “Cari 20 post terbaru @id.easylegal” invokes the Instagram Apify path when data is stale or explicitly refreshed, persists normalized posts, and answers from those posts.
- “Mulai monitor @id.easylegal” enrolls the account in scheduled jobs.
- A one-time profile search does not enroll the account.
- “Ganti akun EasyLegal dari @easylegal_id menjadi @id.easylegal” changes the scheduled target, preserves historical posts, and performs an initial scrape.
- “Berhenti monitor @id.easylegal” stops future scheduled scraping without deleting historical data.
- Calls outside the configured OpenWebUI trust boundary cannot spend Apify credit or mutate monitoring state.
- The response always distinguishes a successful Apify refresh, a non-Apify fallback, a fresh cache hit, stale cached data, and a failed scrape.
