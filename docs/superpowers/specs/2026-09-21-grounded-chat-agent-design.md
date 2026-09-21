# Grounded Chat Agent: Provider-Neutral Turn Pipeline (Stage P1)

**Date:** 2026-09-21
**Status:** Design approved for spec-writing; not yet approved for implementation.
**Stage:** P1 of a three-stage roadmap (P1 this document; P2 bounded tool loop; P3 observability — see §9).

## 1. Context & Problem Statement

The approved decision behind this document: build one provider-neutral grounded turn pipeline first, then add a two-round read-only tool loop on top of it (Stage P2, not covered here). `ChatActionOrchestrator` remains the sole authority for mutations and scraping throughout — the chat/model layer never gains a second way to write to the database or trigger an external scrape. This is deliberately not "add more prompt text"; the existing prompt-enrichment path has already been pushed hard and the remaining gaps are structural, not phrasing.

Provider choice currently changes what the user gets, not just how it is produced:

- The 9router (OpenAI-compatible) path preloads evidence through `_build_router_context`; native Anthropic uses separate one-round tool behavior; the local fallback uses templates. Which provider handled the request changes freshness behavior, evidence available to the model, conversational memory, and ultimately answer quality (`src/claude_client.py:819-1377`).
- The action layer underneath this is already sound and is reused as-is: deterministic parsing, typed plans, validation, limits, freshness, receipts, clarification, and failure containment all exist and work (`src/chat_actions.py:347-599`, `634-755`, `855-943`).
- Prompt enrichment already does heavy lifting in `_build_router_context` (`src/claude_client.py:819-961`). More prompt text helps in the short term but cannot by itself create iterative retrieval, provider parity, citation validation, or observability — those require a shared data contract and shared control flow, which is what this spec defines.

The fix is a canonical, internal response contract — `PreparedTurn` in, `TurnResult` out — that both provider adapters consume and produce identically. The canonical contract stays internal to the pipeline. `/chat` and `/v1/chat/completions` remain backward-compatible adapters that translate `TurnResult` into their existing external response shapes; their external schemas do not change (`src/server.py:406-553`).

Some of the specific defects listed below were already fixed by a separate, already-shipped P0 change (`8d4bfec`, "Fix 5 P0 correctness bugs in the AI chat pipeline", covered by `tests/test_p0_chat_fixes.py`): explicit provider-mode selection, the engagement-rate/engagements-per-post metric split, real (non-fabricated) token usage reporting, and a unified competitor-analysis short-circuit across call paths. Where a section below depends on one of those fixes, it is called out explicitly as **resolved by P0** — this document formalizes and generalizes those fixes into typed contracts rather than reopening them as gaps.

## 2. PreparedTurn Contract

`PreparedTurn` is the single, immutable input every provider adapter consumes. It is built once per turn by a consolidated `_prepare_turn` step (see §6) that replaces `_resolve_matched_topic`, `_resolve_conversation_account`, and `_build_router_context`'s context-assembly responsibility. No adapter is permitted to derive its own subject, freshness, or evidence — if an adapter needs a fact, that fact must already be on `PreparedTurn`.

```python
@dataclass(frozen=True)
class PreparedTurn:
    request_id: str
    query: str
    history: List[ChatMessage]
    subject: Subject
    action_result: Optional[ActionExecutionResult]
    freshness: FreshnessInfo
    evidence: List[EvidenceRecord]
    constraints: AnswerConstraints


class ChatMessage(TypedDict):
    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True)
class Subject:
    kind: Literal["topic", "account", "accounts", "competitor", "none"]
    keys: List[str]
    confidence: float
    resolution_source: Literal["explicit", "history", "action", "classifier"]


FreshnessStatus = Literal["fresh", "stale", "refreshed", "refresh_failed", "disabled", "unknown"]


@dataclass(frozen=True)
class FreshnessInfo:
    status: FreshnessStatus
    data_as_of: Optional[str]   # ISO 8601 UTC
    ttl_hours: float
    error: Optional[str]        # user-safe; never a raw exception/provider payload


@dataclass(frozen=True)
class EvidenceRecord:
    source_id: str                       # stable, e.g. "post:482910" or "account:17"
    source_kind: Literal["post", "account"]
    platform: Literal["instagram", "tiktok", "threads"]
    account: Optional[str]               # canonical account key this record belongs to
    topic: Optional[str]                 # canonical topic key, when retrieved via a topic query
    metrics: Dict[str, Optional[int]]    # e.g. {"likes": 40, "comments": 3, "views": None}
    posted_at: Optional[str]             # ISO 8601 UTC; original publish time
    scraped_at: str                      # ISO 8601 UTC; when this record was collected
    url: Optional[str]
    missing_fields: List[str]            # explicit names of unavailable fields, e.g. ["views", "reach"]


@dataclass(frozen=True)
class AnswerConstraints:
    unavailable_metrics: List[str]   # e.g. ["reach", "impressions"]
    warnings: List[str]              # e.g. ["missing historic URLs", "missing follower counts"]
```

Field-by-field description and invariants:

- **`request_id: str`** — generated once at endpoint entry (`src/server.py`), before `PreparedTurn` construction. It is stable for the entire turn, used for buffered and streaming responses alike, and is the correlation key for every log line and action receipt produced while handling that turn. It is never regenerated mid-turn.
- **`query: str`** — the current user turn's text. Always the literal text the user sent for this turn, never a rewritten or router-expanded version.
- **`history: List[ChatMessage]`** — only normalized `user`/`assistant` messages; system prompts, tool messages, and raw provider payloads never appear here. History is bounded by both a maximum message count and a maximum character budget; when the budget is exceeded, the oldest turns are dropped first, not the newest.
- **`subject: Subject`** — the resolved conversational subject.
  - `kind` is one of `topic`, `account`, `accounts` (plural, multi-account comparison), `competitor`, or `none`.
  - `keys` holds the canonical key(s) for that subject. Cardinality must match `kind`: `none` ⇒ `keys == []`; `topic`, `account`, `competitor` ⇒ exactly one key; `accounts` ⇒ two to four keys, mirroring the existing `compare_profiles` action's target bounds (`src/chat_actions.py`, `MIN_TARGETS`/`MAX_TARGETS`). This is also where the "which call path is a competitor request" decision now lives, per §4 — it is decided once during subject resolution, not re-derived per adapter.
  - `confidence` is a float in `[0.0, 1.0]`.
  - `resolution_source` records how the subject was determined: `explicit` (named in the current message), `history` (carried from a prior turn), `action` (returned by the action orchestrator), or `classifier` (topic-intent classifier).
- **`action_result: Optional[ActionExecutionResult]`** — the existing `ActionExecutionResult` type from `src/chat_actions.py:112`, reused unchanged. It is `None` when no action plan executed for this turn (a pure research/chat query); otherwise it carries receipts, clarification state, and matched topic/account(s) exactly as the action layer already produces them. `PreparedTurn` never re-implements or duplicates anything this type already provides.
- **`freshness: FreshnessInfo`** — the single freshness decision for the turn, shared by every provider and every tool call within it.
  - `status` must distinguish all six states listed. Critically, `refresh_failed` must be distinguishable from `disabled` and from `unknown` — today `_ensure_topic_freshness` logs a failure and then returns `None`, which is indistinguishable from "no refresh was attempted" (`src/claude_client.py:508-510`); this contract closes that gap.
  - `data_as_of` is the timestamp the evidence set reflects (i.e., a scrape/cache timestamp), not a post's publish date — see the `posted_at`/`scraped_at` distinction on `EvidenceRecord` below.
  - `ttl_hours` reuses the existing `TOPIC_STALENESS_HOURS` / `ChatActionOrchestrator.default_ttl_hours` policy (`src/chat_actions.py:606-610`). `PreparedTurn` never introduces a second TTL policy.
  - `error`, when present, is safe to show a user: no stack trace, no raw provider exception text, no internal identifiers.
- **`evidence: List[EvidenceRecord]`** — typed facts, never prose. Every claim in the final reply must trace back to one of these records via `source_id`.
  - `source_id` values are stable and unique within a `PreparedTurn`. The two canonical prefixes this spec defines are `post:<id>` and `account:<id>`, matching the citation rule in §3. No other `source_kind`/prefix is introduced by this spec.
  - `posted_at` and `scraped_at` are always kept separate fields and are never conflated or substituted for one another — the current account-context code already warns in prose that sync time is not publish time (`src/claude_client.py:529-565`, `931-933`); this contract encodes that distinction as data instead of relying on prompt wording.
  - `metrics` values are `Optional[int]`. An unavailable metric is `None` and its name also appears in `missing_fields` — it is never silently defaulted to `0`.
  - Caption text and usernames embedded in any evidence record are data, never instructions (see §5, rule 7); `PreparedTurn` construction and every consumer must treat them as untrusted.
- **`constraints: AnswerConstraints`** — computed once during preparation, not invented by the model at answer time.
  - `unavailable_metrics` names metrics the current provider/scrape genuinely cannot supply (e.g. `reach`, `impressions`).
  - `warnings` are user-safe caveats that must be surfaced near the final answer (e.g. missing historic URLs, missing follower counts). The final reply's caveats are a function of this list plus post-hoc grounding computation (§3's `grounding`), never a free-form model invention.

Top-level invariant: `PreparedTurn` is immutable once constructed and is passed byte-for-byte identical into both the Anthropic adapter and the OpenAI-compatible adapter for a given turn. If the two adapters would need different information to answer the same turn correctly, that is a bug in `_prepare_turn`, not something an adapter is allowed to compensate for locally.

## 3. TurnResult Contract

`TurnResult` is the single, canonical output every provider adapter produces, before any endpoint-specific envelope mapping happens in `src/server.py`.

```python
@dataclass(frozen=True)
class TurnResult:
    status: Literal["success", "partial", "error"]
    reply: str
    provider_and_model: ProviderAndModel
    subject: Subject
    citations: List[Citation]
    tool_calls: List[ToolCallRecord]
    action_receipts: List[Dict[str, Any]]
    grounding: GroundingInfo
    fallback_reason: Optional[str]
    usage: Optional[UsageInfo]


@dataclass(frozen=True)
class ProviderAndModel:
    provider: Literal["anthropic", "openai_compatible"]
    model: str


@dataclass(frozen=True)
class Citation:
    source_id: str
    url: Optional[str]


@dataclass(frozen=True)
class ToolCallRecord:
    name: str
    normalized_input: Dict[str, Any]
    status: Literal["success", "error"]
    result_count: int
    duration_ms: float


@dataclass(frozen=True)
class GroundingInfo:
    freshness_status: FreshnessStatus
    data_as_of: Optional[str]
    evidence_count: int
    missing_fields: List[str]
    unsupported_claim_count: int


@dataclass(frozen=True)
class UsageInfo:
    prompt_tokens: Optional[int]
    completion_tokens: Optional[int]
    total_tokens: Optional[int]
```

Field-by-field description and invariants:

- **`status`** — `success`, `partial`, or `error`. `partial` specifically means: the answer is usable, but its evidence set includes something stale, failed to refresh, or is missing — never a silent degrade that looks identical to `success`.
- **`reply: str`** — the final user-visible prose. This is what `/chat` and `/v1/chat/completions` surface, unchanged, to their respective callers.
- **`provider_and_model: ProviderAndModel`** — the actual provider mode and model that produced this reply, not the configured default or the initially-attempted one. If a fallback occurred, this reflects whatever actually generated `reply`.
- **`subject: Subject`** — copied verbatim from the `PreparedTurn.subject` that produced this result. It is never re-derived at output time.
- **`citations: List[Citation]`** — every `source_id` the reply actually cites, each optionally paired with a URL taken from the matching `EvidenceRecord`. Invariant: every `source_id` in `citations` must exist in the `evidence` list of the `PreparedTurn` this turn consumed. A citation pointing at a nonexistent `source_id` is a contract violation, not a warning.
- **`tool_calls: List[ToolCallRecord]`** — name, normalized input, status, result count, and duration for each tool invocation made while producing this reply. This list never contains raw provider SDK objects or unnormalized request/response payloads — only the five fields above, which must be identical in shape regardless of which provider made the call.
- **`action_receipts: List[Dict[str, Any]]`** — the existing receipt dictionaries already produced by the action orchestrator, preserved unchanged. Invariant: receipts are present on both the success path and the fallback path whenever an action actually executed — a fallback to a different provider or to the local template handler must not silently drop receipts for actions that already ran.
- **`grounding: GroundingInfo`** — the post-hoc grounding summary for this specific reply: the freshness status and `data_as_of` it was produced under, how many evidence records were available, which fields were missing, and how many numeric/factual claims could not be tied to a `source_id` (see the citation rule below — this count should normally be `0` because unsupported claims are removed before the reply is finalized).
- **`fallback_reason: Optional[str]`** — a machine-readable reason string (or `null` when no fallback occurred). It is never a raw provider exception string exposed to the user or logged as if it were user-facing text.
- **`usage: Optional[UsageInfo]`** — actual provider-reported usage when the provider returns it; otherwise `null`. This formalizes, as a typed contract, the P0 fix that removed the character-count token fiction previously present at `src/server.py:515-519`: P1 does not reopen that gap, it generalizes "real usage or null" into `TurnResult.usage` so every provider adapter is held to the same rule going forward.

**Citation rule (invariant).** Every numeric claim, named top post, date, ranking, or link in `reply` must reference exactly one `source_id` from the evidence used to produce it, formatted as `[post:<id>]` or `[account:<id>]`. A final validator checks every referenced ID against the `PreparedTurn.evidence` list before `TurnResult` is returned. Any numeric claim that cannot be tied to a valid `source_id` is either removed from the reply or triggers exactly one correction pass to re-ground or drop it — it is never left in the final reply uncited, and it is never allowed to trigger unbounded retries.

## 4. Provider Parity Design

### Current gaps

1. **Provider mode selection** — previously inferred from API key prefix and whether the base URL contained `anthropic.com` (`src/claude_client.py:79-88`, `124-128`). **Resolved by P0**: an explicit `CHAT_PROVIDER_MODE` environment variable now drives this at `ClaudeChatHandler.__init__`. P1 builds on top of this by having the turn runner consume that explicit mode directly, rather than re-deriving or duplicating the decision.
2. The 9router (OpenAI-compatible) path receives one large system prompt plus preloaded DB evidence assembled by `_build_router_context`, `_call_openai_router`, and `stream_router_chat` (`src/claude_client.py:819-1101`). This is still an open gap: that context-assembly responsibility is provider-specific today.
3. The Anthropic adapter receives `CLAUDE_TOOLS_SPEC`, may execute one batch of tool calls, and then makes a final call without tools available (`_claude_tool_use_loop`, `src/claude_client.py:1104-1232`). Despite its name, this is not a loop — it is still an open gap relative to genuine iterative tool use (full iterative behavior is Stage P2; P1's job is to normalize this adapter's *output shape*, not add rounds).
4. Unknown-topic classification always calls the OpenAI-compatible `/chat/completions` endpoint regardless of the active provider (`_classify_topic_intent`, `src/claude_client.py:386-440`), so native Anthropic sessions lack parity on topic classification. Still an open gap.

### Target design

- Both adapters consume the same `PreparedTurn` (§2) and expose the same normalized output shape, `AssistantStep(text, tool_calls, usage)`, built from the same tool catalog (§6, `src/tools.py`).
- The Anthropic adapter maps native Anthropic tool-use blocks onto that normalized shape. The OpenAI-compatible adapter uses native tool calls only if 9router's tool-call support is verified; otherwise it keeps the current strict-JSON pattern. Either way, loop semantics (how many rounds, when to stop) are identical between the two adapters.
- Both the buffered and streaming code paths call the same underlying orchestration. Streaming changes event transport only — it never changes subject resolution, competitor-request handling, freshness decisions, tool availability, or fallback behavior.
- Competitor analysis currently already behaves identically across all three call paths — **resolved by P0**, which made `_claude_tool_use_loop` short-circuit competitor requests the same way the other two paths do. P1 keeps that unified behavior but relocates the decision of "is this a competitor request" into `Subject` resolution inside `PreparedTurn` (§2), so it is decided once per turn rather than checked separately inside each call path.
- `ClaudeChatHandler.process_chat` and `stream_chat` remain compatibility facades. They delegate to one turn runner; they do not maintain separate decision trees for buffered versus streaming, or for Anthropic versus OpenAI-compatible.

### Current vs. target, by provider

**Current state**

| Dimension | `anthropic` | `openai_compatible` (9router) |
|---|---|---|
| Provider-mode selection | Explicit via `CHAT_PROVIDER_MODE` (P0) | Explicit via `CHAT_PROVIDER_MODE` (P0) |
| Turn context construction | Uses `CLAUDE_TOOLS_SPEC` tool definitions; minimal preloaded evidence | `_build_router_context` preloads one large system prompt plus DB evidence (`src/claude_client.py:819-961`) |
| Tool execution rounds | `_claude_tool_use_loop` runs at most one tool batch, then a final call with no tools available, despite its name (`src/claude_client.py:1104-1232`) | `_call_openai_router` / `stream_router_chat` rely on preloaded context rather than iterative tool calls (`src/claude_client.py:819-1101`) |
| Topic-intent classification | Delegates to the OpenAI-compatible `/chat/completions` endpoint regardless of active provider (`_classify_topic_intent`, `src/claude_client.py:386-440`) | Native call path for this classifier |
| Competitor analysis short-circuit | Unified across all three call paths, including `_claude_tool_use_loop` (P0) | Unified across all three call paths (P0) |

**Target (P1)**

| Dimension | `anthropic` | `openai_compatible` (9router) |
|---|---|---|
| Turn context construction | Consumes shared `PreparedTurn.evidence`; builds no provider-specific factual prose | Consumes the same `PreparedTurn.evidence`; `_build_router_context`'s prose-building responsibility is retired |
| Tool execution rounds | Maps native Anthropic tool blocks onto the normalized output; loop semantics identical to the other adapter | Uses native tool calls if 9router support is verified, else the current strict-JSON pattern; loop semantics identical to the Anthropic adapter |
| Normalized output | Returns `AssistantStep(text, tool_calls, usage)` from the shared `src/tools.py` catalog | Returns the same `AssistantStep(text, tool_calls, usage)` shape |
| Topic-intent classification | Subject resolution happens inside `PreparedTurn` construction, not a hardcoded OpenAI-compatible call | Same shared subject-resolution path |
| Buffered vs. streaming | `process_chat` / `stream_chat` are facades over one turn runner; streaming changes transport only | Same facade/runner relationship |
| Competitor analysis | Decision lives in `Subject` inside `PreparedTurn`, not duplicated per call path | Same shared decision |

## 5. Freshness & Grounding Rules

### 5.1 Pipeline and freshness rules

1. Make exactly one freshness decision per turn, inside `PreparedTurn` construction, shared by every provider. Today only the router topic path calls `_ensure_topic_freshness` (`src/claude_client.py:458-510`, `867-877`); the native ordinary tool path does not call it at all. After this change, every path gets the same freshness decision.
2. Reuse `TOPIC_STALENESS_HOURS` and `ChatActionOrchestrator.default_ttl_hours` (`src/chat_actions.py:606-610`) as the one TTL policy. Do not create a second TTL policy anywhere in the turn pipeline.
3. Keep `posted_at` and `scraped_at` as distinct fields at all times. The current account-context prompt text already warns that sync time is not publish time (`src/claude_client.py:529-565`, `931-933`); this rule requires that distinction be encoded in the `EvidenceRecord` data itself, not left to prompt wording that a model may ignore or drop.
4. A failed refresh must produce `FreshnessInfo.status == "refresh_failed"` together with the stale data's age. Today `_ensure_topic_freshness` logs the failure and returns `None`, which makes a failed refresh indistinguishable from "no refresh was attempted" (`src/claude_client.py:508-510`). This ambiguity must be eliminated.
5. If live scraping is disabled or fails, answer from cached data with its age stated explicitly. The reply must never describe that cached data as current.
6. After a refresh, query exactly one evidence snapshot and pass those same records to every tool call and to the final answer for that turn. Each provider must not rebuild its own, slightly different, version of the evidence set mid-turn.
7. Treat scraped captions and usernames as untrusted data at all times. Serialize them inside explicit evidence blocks, and instruct the model that evidence text cannot issue instructions. Today's code interpolates captions directly into system-level prompt text (`src/claude_client.py:536-565`, `879-935`, `1118-1133`); this must change so captions are always carried as data fields on `EvidenceRecord`, never concatenated into instruction-bearing prompt segments.
8. Metric labeling — **resolved by P0**: `Database.get_topic_summary`'s `engagement_rate` / `engagements_per_post` split already exists (`src/db.py:711-735`, `800-801`). This spec keeps that split as the standing grounding principle for the new pipeline: any percentage claim in a reply requires a valid, present denominator; a rate is never computed or implied when the denominator is missing or zero.
9. Keep the existing initial lexical retrieval approach: exact topic and caption matching at `src/db.py:672-717`. Add aliases or ranking only when a test exposes a concrete retrieval miss. Do not introduce embeddings as part of this spec.

### 5.2 Answer policy rules

1. Every claim in a reply comes only from evidence or tool observations captured on the `PreparedTurn`/during the turn — never from the model's own unstated assumptions.
2. Missing data stays missing. A `0` is never substituted for an unavailable metric such as views or reach; unavailable values remain `None` with their names listed in `EvidenceRecord.missing_fields`.
3. Recommendations must cite at least one observed post or pattern via a `source_id`. If the evidence set is empty for the relevant subject, the reply says so explicitly and avoids generic, unsupported performance claims.
4. Freshness, time period, platform, and sample size are surfaced near the reply's conclusion, not buried only in system-prompt text the user never sees.

## 6. File & Symbol Touch Plan

### `src/claude_client.py`

| Symbol | Change |
|---|---|
| `ClaudeChatHandler.__init__` | Provider mode is already explicit (P0). P1 adds turn-runner wiring on top of that explicit mode. |
| `process_chat` / `stream_chat` | Become compatibility facades that delegate to one shared turn runner instead of maintaining separate decision trees. |
| `_resolve_matched_topic`, `_resolve_conversation_account`, `_build_router_context` | Consolidated into a new `_prepare_turn` step that produces `PreparedTurn`; these stop building provider-specific factual prose. |
| `_call_openai_router`, `stream_router_chat`, `_claude_tool_use_loop` | Become thin adapters that return the normalized `AssistantStep(text, tool_calls, usage)` shape. |
| `_ensure_topic_freshness` | Returns a structured `FreshnessInfo`-shaped status; never returns an ambiguous `None` on refresh failure. |
| `_local_fallback_handler` | Consumes the already-prepared evidence from `PreparedTurn`; the arbitrary first-word performance claims currently generated here are removed. |

**Goal:** one subject/freshness/evidence decision tree shared by every call path.

### `src/tools.py`

| Symbol | Change |
|---|---|
| `CLAUDE_TOOLS_SPEC` | Becomes the single validated tool catalog shared by both provider adapters. |
| `execute_claude_tool` | Returns normalized evidence output shared by both providers. |
| Existing read-only tool functions | Reused as-is; outputs conform to `EvidenceRecord`. |

**Goal:** a single validated tool catalog with normalized evidence outputs for both providers. Any provider-specific constant is renamed only as part of a clean cutover that updates every caller — no aliases or shims left behind.

### `src/db.py`

| Symbol | Change |
|---|---|
| `get_topic_summary` | Metric semantics already partly corrected (P0); confirmed as the source of truth for `EvidenceRecord.metrics`. |
| `get_topic_last_scraped` | Feeds `FreshnessInfo`. |
| `query_posts` | Feeds `EvidenceRecord` construction for post-level evidence. |
| `get_topic_account_breakdown` | Feeds account-level evidence. |

**Goal:** correct metric semantics (already partly done via P0) and return the provenance fields already stored in the database. No schema migration unless stable source IDs are found to be missing.

### `src/models.py`

| Symbol | Change |
|---|---|
| Existing dataclasses | Reused; small new internal dataclasses (e.g. the `PreparedTurn`/`TurnResult` family from §2–§3) are added only where plain typed dictionaries would be unclear. |

**Goal:** no persistent memory tables are introduced.

### `src/server.py`

| Symbol | Change |
|---|---|
| `chat_endpoint` | Maps the canonical `TurnResult` onto the existing `/chat` response envelope. |
| `openai_compatible_chat` | Maps the canonical `TurnResult` onto the existing `/v1/chat/completions` envelope, including request ID and final SSE completion metadata where the envelope allows it. |

**Goal:** existing external response envelopes stay unchanged; usage reporting stays real-or-null (already done via P0, now formalized by `TurnResult.usage`).

### `openwebui_tool.py`

| Symbol | Change |
|---|---|
| `Tools` methods | Remain a thin HTTP adapter only. |

**Goal:** no duplication of agent planning, subject resolution, freshness policy, or citation logic inside this file.

## 7. Test Plan

### Retain (unchanged, still must pass)

- Action exception containment and endpoint non-500 behavior: `tests/test_chat_action_resilience.py:13-101`.
- Topic confidence, filler quota protection, TTL, disabled refresh, scrape failure, and progress ordering: `tests/test_live_scrape_on_chat.py:35-189`.
- Existing action parsing/validation coverage in `tests/test_chat_actions.py`.
- Existing context behavior in `tests/test_router_context_enrichment.py`.
- The P0 regression tests already in `tests/test_p0_chat_fixes.py` (provider mode, engagement metric split, real usage, competitor parity, receipt preservation).

### Add or update

- `tests/test_router_context_enrichment.py`: explicit topic beats a historical account; an account pronoun follow-up persists across turns; `PreparedTurn.evidence` is identical for the Anthropic and OpenAI-compatible adapters given the same inputs; a malicious/prompt-injecting caption remains inert data rather than altering behavior.
- `tests/test_live_scrape_on_chat.py`: `refresh_failed`, `disabled`, `stale`, and `refreshed` are all distinguishable outcomes; `data_as_of` reflects scrape time while individually cited posts retain their own publish time.
- `tests/test_chat_action_resilience.py`: receipts survive router failure, an empty stream, a malformed chunk, and a deterministic fallback.
- `tests/test_server_e2e.py`: `/chat` and the buffered `/v1` endpoint expose equivalent reply/grounding semantics; SSE emits the same final outcome as the buffered path; usage is always either actual provider usage or `null`, never a guess.

### Evaluation cases

1. Known topic plus unknown filler in the same message.
2. A genuinely new topic, exercising both classifier success and classifier failure.
3. An account follow-up using a bare pronoun reference (e.g. "nya").
4. A two-account comparison.
5. A date/platform-filtered post search.
6. Missing views versus a legitimate zero-views post (must not be conflated).
7. A stale cache under each of: refresh success, refresh failure, and refresh disabled.
8. Prompt-injection text embedded inside a scraped caption.
9. A provider returning an empty or malformed response.
10. A competitor-analysis request through both the buffered and the streaming route.

### Assertion style

Assert on `subject`, evidence rows, freshness status, tool-call sequence, citations, and unsupported-claim count. Do not pin exact reply prose — tests must survive rewording of the natural-language answer as long as the underlying contract fields are correct.

## 8. Scope Boundary / Do-Not-Build

Explicitly out of scope for this spec:

- No LangChain/LlamaIndex or new orchestration dependency.
- No vector database or embeddings before lexical evaluation proves a concrete need.
- No persistent free-form AI memory. Bounded request history plus an explicit active subject is enough.
- No autonomous monitoring or account mutations inside the model loop.
- No unbounded ReAct loop, recursive retries, multi-agent system, or background swarm.
- No message broker or job framework yet. Consider async refresh only if measured p95 scrape latency makes the synchronous policy unacceptable.
- No second tool implementation in `openwebui_tool.py`.
- No larger system prompt as a substitute for canonical evidence, citations, freshness status, and provider parity.

## 9. Future Stages (Reference Only)

**Stage P2 — bounded read-only tool loop.** Once the canonical `PreparedTurn`/`TurnResult` pipeline and provider parity from this document are in place, a follow-up spec will add a bounded, two-round, read-only tool loop on top of it, letting a provider request one additional round of tool evidence before finalizing an answer. That stage is a separate specification to be written later; no implementation detail beyond this one-line description is part of this document.

**Stage P3 — observability.** A further follow-up spec will add observability (structured logging/metrics/tracing) over the turn pipeline established here. That stage is also a separate specification to be written later; no implementation detail beyond this one-line description is part of this document.
