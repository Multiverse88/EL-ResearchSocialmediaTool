from __future__ import annotations

import logging
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from .db import Database
from .models import Account

logger = logging.getLogger("chat_actions")

SUPPORTED_PLATFORMS = ("instagram", "tiktok", "threads")
MAX_POSTS_LIMIT = 100
MIN_TARGETS = 2
MAX_TARGETS = 4
DEFAULT_MAX_POSTS = 30
ProgressCallback = Optional[Callable[[str], None]]

ACTION_TYPES = (
    "scrape_profile",
    "research_topic",
    "compare_profiles",
    "monitor_account",
    "replace_monitored_account",
    "stop_monitoring",
)


# ---------------------------------------------------------------------------
# Typed action plan
# ---------------------------------------------------------------------------

@dataclass
class ScrapeProfileAction:
    platform: str = ""
    username: str = ""
    max_posts: int = DEFAULT_MAX_POSTS
    force_refresh: bool = False
    type: str = "scrape_profile"


@dataclass
class ResearchTopicAction:
    keyword: str = ""
    platforms: List[str] = field(default_factory=lambda: list(SUPPORTED_PLATFORMS))
    max_posts_per_platform: int = DEFAULT_MAX_POSTS
    force_refresh: bool = False
    type: str = "research_topic"


@dataclass
class CompareProfilesAction:
    targets: List[Dict[str, str]] = field(default_factory=list)
    max_posts: int = DEFAULT_MAX_POSTS
    force_refresh: bool = False
    type: str = "compare_profiles"


@dataclass
class MonitorAccountAction:
    platform: str = ""
    username: str = ""
    max_posts: int = DEFAULT_MAX_POSTS
    type: str = "monitor_account"


@dataclass
class ReplaceMonitoredAccountAction:
    platform: str = ""
    old_username: str = ""
    new_username: str = ""
    max_posts: int = DEFAULT_MAX_POSTS
    type: str = "replace_monitored_account"


@dataclass
class StopMonitoringAction:
    platform: str = ""
    username: str = ""
    type: str = "stop_monitoring"


Action = Any  # one of the six dataclasses above


@dataclass
class ActionPlan:
    actions: List[Action] = field(default_factory=list)
    analysis_request: str = ""
    needs_clarification: bool = False
    clarification_question: Optional[str] = None


@dataclass
class ActionReceipt:
    action_type: str
    platform: Optional[str]
    target: str
    backend: str
    success: bool
    posts_collected: int = 0
    used_cache: bool = False
    cache_age_minutes: Optional[float] = None
    error: Optional[str] = None
    detail: str = ""


@dataclass
class ActionExecutionResult:
    receipts: List[ActionReceipt] = field(default_factory=list)
    matched_topic: Optional[str] = None
    matched_account: Optional[Tuple[str, str]] = None  # (platform, username)
    matched_accounts: List[Tuple[str, str]] = field(default_factory=list)  # 2+ accounts (compare_profiles)
    clarification: Optional[str] = None
    context_text: str = ""
    status_lines: List[str] = field(default_factory=list)

    def receipts_as_dicts(self) -> List[Dict[str, Any]]:
        return [asdict(r) for r in self.receipts]


def _empty_result() -> ActionExecutionResult:
    return ActionExecutionResult()


# ---------------------------------------------------------------------------
# Deterministic fast-path parser
# ---------------------------------------------------------------------------
# Handles the explicit command phrasings enumerated in the design spec directly,
# with no model round-trip. The AI planner (wired in claude_client.py) is only
# consulted when this parser finds nothing AND the message shows some other sign
# of action intent — see `has_action_intent`.

_MENTION_RE = re.compile(r"@([a-zA-Z0-9_.]{1,30})")
_MAX_POSTS_RE = re.compile(r"\b(\d{1,3})\s*(post|postingan|video)\b", re.IGNORECASE)
_FORCE_REFRESH_RE = re.compile(
    r"\b(terbaru|refresh|scrape ulang|ambil ulang|update sekarang|sekarang|latest|now)\b",
    re.IGNORECASE,
)
_STOP_MONITOR_RE = re.compile(r"\b(berhenti|stop|hentikan)\b.{0,20}\b(monitor|pantau|memantau)\b", re.IGNORECASE)
_MONITOR_RE = re.compile(r"\b(mulai\s+)?(monitor|pantau|memantau)\b", re.IGNORECASE)
_REPLACE_RE = re.compile(r"\b(ganti|ubah|replace)\b.{0,15}\b(akun|monitoring|target)\b", re.IGNORECASE)
_COMPARE_RE = re.compile(r"\b(bandingkan|compare)\b", re.IGNORECASE)

# Matches an account referenced WITHOUT "@" — e.g. "akun instagram id.easylegal",
# "akun id.easylegal di tiktok", "profil instagram id.easylegal". Chat messages asking
# about a specific profile very often skip the "@" and any cari/scrape verb entirely
# ("saya mau riset soal akun instagram id.easylegal"), so this is how parse_deterministic
# identifies a target account when no "@mention" is present.
_PLATFORM_WORD = r"(?:instagram|ig|tiktok|threads)"
_BARE_ACCOUNT_PATTERNS = [
    re.compile(
        rf"\bakun\s+(?:di\s+)?(?P<platform>{_PLATFORM_WORD})\s+@?(?P<username>[a-zA-Z0-9_.]{{2,30}})\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\bakun\s+@?(?P<username>[a-zA-Z0-9_.]{{2,30}})\s+(?:di\s+)?(?P<platform>{_PLATFORM_WORD})\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\bprofil(?:e)?\s+(?:di\s+)?(?P<platform>{_PLATFORM_WORD})\s+@?(?P<username>[a-zA-Z0-9_.]{{2,30}})\b",
        re.IGNORECASE,
    ),
]
_BARE_ACCOUNT_USERNAME_BLOCKLIST = {
    "kami", "saya", "kita", "anda", "kalian", "milik", "punya", "kami", "ini", "itu",
    "baru", "lama", "yang", "tersebut", "sendiri", "brand", "kompetitor",
}

_REPLACE_STOP_WORDS = {
    "ganti", "akun", "ubah", "replace", "menjadi", "jadi", "dengan", "ke", "monitoring",
    "target", "dari", "untuk", "yang", "tolong", "bisa", "coba", "dong",
}

_ACTION_INTENT_RE = re.compile(
    r"@|\bmonitor\b|\bpantau\b|\bganti akun\b|\bganti monitoring\b|\bstop monitoring\b|"
    r"\bberhenti monitor\b|\bbandingkan akun\b|\bcompare akun\b|\bscrape akun\b|\bscrape profil\b|"
    rf"\bakun\s+(?:di\s+)?{_PLATFORM_WORD}\b|\bakun\s+\S+\s+(?:di\s+)?{_PLATFORM_WORD}\b|"
    rf"\bprofil(?:e)?\s+(?:di\s+)?{_PLATFORM_WORD}\b",
    re.IGNORECASE,
)
_COMPETITOR_TERM_RE = re.compile(r"\b(kompetitor|competitor)\b", re.IGNORECASE)
_COMPETITOR_ANALYSIS_RE = re.compile(
    r"\b(konten|postingan?|performa|views?|tayangan|likes?|komentar|interaksi|engagement|"
    r"bandingkan|komparasi|benchmark|terbaik|terbesar|atm|amati|tiru|modifikasi)\b",
    re.IGNORECASE,
)


def is_competitor_analysis_intent(message: str) -> bool:
    """True for read-only competitor comparison/ATM questions, not monitoring commands."""
    return bool(
        _COMPETITOR_TERM_RE.search(message)
        and _COMPETITOR_ANALYSIS_RE.search(message)
        and not _MONITOR_RE.search(message)
        and not _REPLACE_RE.search(message)
        and not _STOP_MONITOR_RE.search(message)
    )




def has_action_intent(message: str) -> bool:
    """Cheap pre-filter so the AI planner is only invoked for messages that plausibly
    request a mutating/scraping action — avoids an LLM round-trip (and its cost/latency)
    for ordinary research questions, which the existing topic-research flow already
    handles well."""
    return bool(_ACTION_INTENT_RE.search(message))


def _extract_mentions(message: str) -> List[str]:
    return [m.group(1).lower() for m in _MENTION_RE.finditer(message)]


def _extract_max_posts(message: str, default: int = DEFAULT_MAX_POSTS) -> int:
    m = _MAX_POSTS_RE.search(message)
    if not m:
        return default
    try:
        return max(1, min(MAX_POSTS_LIMIT, int(m.group(1))))
    except ValueError:
        return default


def _extract_force_refresh(message: str) -> bool:
    return bool(_FORCE_REFRESH_RE.search(message))


def _infer_platform(db: Database, username: str, msg_lower: str) -> str:
    if "tiktok" in msg_lower:
        return "tiktok"
    if "threads" in msg_lower:
        return "threads"
    if "instagram" in msg_lower or re.search(r"\big\b", msg_lower):
        return "instagram"
    norm_user = username.lower().strip().lstrip("@")
    if norm_user.startswith("id."):
        return "instagram"
    if norm_user.endswith("_tiktok"):
        return "tiktok"
    for plat in ("instagram", "tiktok", "threads"):
        if db.get_account_by_username(plat, username):
            return plat
    return "instagram"


def _extract_bare_account_mention(message: str) -> Optional[Tuple[str, str]]:
    """Extracts (username, platform) from phrasing like "akun instagram id.easylegal"
    or "akun id.easylegal di tiktok" — no "@" required. Rejects common pronouns/filler
    words as a username since, unlike an "@mention", this path has no sigil to anchor
    on and could otherwise misfire on phrases like "akun kami di instagram"."""
    for pat in _BARE_ACCOUNT_PATTERNS:
        m = pat.search(message)
        if not m:
            continue
        username = m.group("username").lower().strip(".")
        platform_word = m.group("platform").lower()
        if platform_word in ("instagram", "ig"):
            platform = "instagram"
        elif platform_word == "threads":
            platform = "threads"
        else:
            platform = "tiktok"
        if username and username not in _BARE_ACCOUNT_USERNAME_BLOCKLIST:
            return username, platform
    return None


def resolve_account_reference(db: Database, message: str) -> Optional[Tuple[str, str]]:
    """Resolves an account mentioned in chat text, but only when it exists in the DB."""
    msg_lower = message.lower()
    for username in _extract_mentions(message):
        platform = _infer_platform(db, username, msg_lower)
        if db.get_account_by_username(platform, username):
            return platform, username
    bare = _extract_bare_account_mention(message)
    if bare:
        username, platform = bare
        if db.get_account_by_username(platform, username):
            return platform, username
    known = _extract_known_account_mentions(db, message)
    if len(known) == 1:
        return known[0]
    return None


def _extract_known_account_mentions(db: Database, message: str) -> List[Tuple[str, str]]:
    """Finds account usernames from `message` that are already registered in the DB —
    no "@" or platform word required. Resolves platform using `_infer_platform` so
    handles like `id.easytax` or `id.easyoffice` correctly target Instagram even if an
    old TikTok entry exists in the database.
    """
    tokens = set(re.findall(r"[a-zA-Z0-9_.]+", message.lower()))
    if not tokens:
        return []
    msg_lower = message.lower()
    found: List[Tuple[str, str]] = []
    seen = set()
    for acc in db.list_accounts():
        candidate = acc.username.lower().lstrip("@")
        if candidate in tokens and candidate not in seen:
            platform = _infer_platform(db, candidate, msg_lower)
            found.append((platform, candidate))
            seen.add(candidate)
    return found


def _resolve_brand_account(db: Database, message: str) -> Optional[Account]:
    """Resolves an unambiguous monitored account referenced by its brand token.

    An explicit platform in the message narrows otherwise-ambiguous brands that use
    the same username across Instagram, TikTok, and Threads.
    """
    msg_lower = message.lower()
    requested_platform = None
    if "instagram" in msg_lower or re.search(r"\big\b", msg_lower):
        requested_platform = "instagram"
    elif "tiktok" in msg_lower:
        requested_platform = "tiktok"
    elif "threads" in msg_lower:
        requested_platform = "threads"

    words = [
        w for w in re.findall(r"[a-zA-Z0-9_.]+", msg_lower)
        if w not in _REPLACE_STOP_WORDS and len(w) > 2
    ]
    monitored = [
        account for account in db.list_accounts()
        if account.monitoring_enabled
        and (requested_platform is None or account.platform == requested_platform)
    ]
    matches = []
    for acc in monitored:
        for word in words:
            if word in acc.username or acc.username in word:
                matches.append(acc)
                break
    unique = list({account.id: account for account in matches}.values())
    if len(unique) == 1:
        return unique[0]
    return None


def parse_deterministic(db: Database, message: str) -> Optional[ActionPlan]:
    msg_lower = message.lower()
    mentions = _extract_mentions(message)

    # 1. Stop monitoring
    if _STOP_MONITOR_RE.search(msg_lower):
        if mentions:
            username = mentions[0]
            platform = _infer_platform(db, username, msg_lower)
            return ActionPlan(actions=[StopMonitoringAction(platform=platform, username=username)])
        acc = _resolve_brand_account(db, message)
        if acc:
            return ActionPlan(actions=[StopMonitoringAction(platform=acc.platform, username=acc.username)])
        return ActionPlan(
            actions=[],
            needs_clarification=True,
            clarification_question="Akun mana yang ingin dihentikan monitoringnya?",
        )

    # 2. Replace monitored account
    if _REPLACE_RE.search(msg_lower):
        max_posts = _extract_max_posts(message)
        if len(mentions) >= 2:
            old_username, new_username = mentions[0], mentions[1]
            platform = _infer_platform(db, old_username, msg_lower)
            return ActionPlan(actions=[ReplaceMonitoredAccountAction(
                platform=platform, old_username=old_username, new_username=new_username, max_posts=max_posts,
            )])
        if len(mentions) == 1:
            new_username = mentions[0]
            old_acc = _resolve_brand_account(db, message)
            if old_acc:
                return ActionPlan(actions=[ReplaceMonitoredAccountAction(
                    platform=old_acc.platform, old_username=old_acc.username,
                    new_username=new_username, max_posts=max_posts,
                )])
            return ActionPlan(
                actions=[],
                needs_clarification=True,
                clarification_question=f"Akun monitoring mana yang ingin diganti menjadi @{new_username}?",
            )
        return ActionPlan(
            actions=[],
            needs_clarification=True,
            clarification_question="Akun mana yang ingin diganti, dan diganti menjadi username apa?",
        )

    # 3. Monitor
    if _MONITOR_RE.search(msg_lower):
        if mentions:
            username = mentions[0]
            platform = _infer_platform(db, username, msg_lower)
            return ActionPlan(actions=[MonitorAccountAction(platform=platform, username=username)])
        return ActionPlan(
            actions=[],
            needs_clarification=True,
            clarification_question="Akun mana yang ingin mulai dimonitor?",
        )

    # 4. Compare profiles — explicit "bandingkan" with 2+ @mentions, OR 2+ already-known
    # account usernames named bare (no "@", no "bandingkan") like "riset akun id.easytax
    # dan id.easyoffice": naming several registered accounts together is itself compare-like
    # intent, since there's no single subject to fall through to section 5 for.
    known_accounts = _extract_known_account_mentions(db, message)
    if _COMPARE_RE.search(msg_lower) and len(mentions) >= 2:
        targets = [
            {"platform": _infer_platform(db, u, msg_lower), "username": u}
            for u in mentions[:MAX_TARGETS]
        ]
    elif not mentions and len(known_accounts) >= 2:
        targets = [
            {"platform": p, "username": u} for p, u in known_accounts[:MAX_TARGETS]
        ]
    else:
        targets = None
    if targets:
        max_posts = _extract_max_posts(message)
        force_refresh = _extract_force_refresh(message)
        return ActionPlan(actions=[CompareProfilesAction(
            targets=targets, max_posts=max_posts, force_refresh=force_refresh,
        )])

    # 5. Scrape a specific profile — naming an account (via "@mention", the bare
    # "akun <platform> <name>" phrasing, or a single already-known account named bare)
    # is itself unambiguous "tell me about this profile" intent; no cari/scrape/riset
    # verb is required on top of it.
    username = None
    platform = None
    if mentions:
        username = mentions[0]
        platform = _infer_platform(db, username, msg_lower)
    else:
        bare = _extract_bare_account_mention(message)
        if bare:
            username, platform = bare
        elif len(known_accounts) == 1:
            platform, username = known_accounts[0]
        elif re.search(r"\b(akun|profil(?:e)?)\b", msg_lower):
            brand_account = _resolve_brand_account(db, message)
            if brand_account:
                platform, username = brand_account.platform, brand_account.username
    if username:
        max_posts = _extract_max_posts(message)
        force_refresh = _extract_force_refresh(message)
        return ActionPlan(actions=[ScrapeProfileAction(
            platform=platform, username=username, max_posts=max_posts, force_refresh=force_refresh,
        )])

    return None

# ---------------------------------------------------------------------------
# AI-planner plan decoding (planner itself lives in claude_client.py — this
# module only knows how to turn its raw JSON dict into typed actions)
# ---------------------------------------------------------------------------

_ACTION_BUILDERS: Dict[str, Callable[[Dict[str, Any]], Action]] = {
    "scrape_profile": lambda d: ScrapeProfileAction(
        platform=str(d.get("platform", "")), username=str(d.get("username", "")),
        max_posts=int(d.get("max_posts", DEFAULT_MAX_POSTS) or DEFAULT_MAX_POSTS),
        force_refresh=bool(d.get("force_refresh", False)),
    ),
    "research_topic": lambda d: ResearchTopicAction(
        keyword=str(d.get("keyword", "")),
        platforms=[str(p) for p in d.get("platforms") or SUPPORTED_PLATFORMS],
        max_posts_per_platform=int(d.get("max_posts_per_platform", DEFAULT_MAX_POSTS) or DEFAULT_MAX_POSTS),
        force_refresh=bool(d.get("force_refresh", False)),
    ),
    "compare_profiles": lambda d: CompareProfilesAction(
        targets=[
            {"platform": str(t.get("platform", "")), "username": str(t.get("username", ""))}
            for t in (d.get("targets") or []) if isinstance(t, dict)
        ],
        max_posts=int(d.get("max_posts", DEFAULT_MAX_POSTS) or DEFAULT_MAX_POSTS),
        force_refresh=bool(d.get("force_refresh", False)),
    ),
    "monitor_account": lambda d: MonitorAccountAction(
        platform=str(d.get("platform", "")), username=str(d.get("username", "")),
        max_posts=int(d.get("max_posts", DEFAULT_MAX_POSTS) or DEFAULT_MAX_POSTS),
    ),
    "replace_monitored_account": lambda d: ReplaceMonitoredAccountAction(
        platform=str(d.get("platform", "")),
        old_username=str(d.get("old_username", "")),
        new_username=str(d.get("new_username", "")),
        max_posts=int(d.get("max_posts", DEFAULT_MAX_POSTS) or DEFAULT_MAX_POSTS),
    ),
    "stop_monitoring": lambda d: StopMonitoringAction(
        platform=str(d.get("platform", "")), username=str(d.get("username", "")),
    ),
}


def plan_from_dict(raw: Dict[str, Any]) -> Optional[ActionPlan]:
    """Converts the AI planner's raw JSON dict into typed actions. Malformed entries
    are dropped individually rather than failing the whole plan — validation catches
    anything still wrong afterward."""
    if not isinstance(raw, dict):
        return None
    actions: List[Action] = []
    for entry in raw.get("actions") or []:
        if not isinstance(entry, dict):
            continue
        action_type = entry.get("type")
        builder = _ACTION_BUILDERS.get(action_type)
        if not builder:
            continue
        try:
            actions.append(builder(entry))
        except Exception:
            continue
    return ActionPlan(
        actions=actions,
        analysis_request=str(raw.get("analysis_request", "") or ""),
        needs_clarification=bool(raw.get("needs_clarification", False)),
        clarification_question=raw.get("clarification_question"),
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_and_normalize(plan: ActionPlan) -> Tuple[ActionPlan, List[str]]:
    """Enforces platform/limit/target guardrails. Invalid individual actions are
    dropped with an error recorded; the plan never executes an out-of-bounds action."""
    errors: List[str] = []
    valid_actions: List[Action] = []

    for action in plan.actions:
        action_type = getattr(action, "type", None)
        if action_type not in ACTION_TYPES:
            errors.append(f"Unknown action type: {action_type}")
            continue

        if action_type in ("scrape_profile", "monitor_account", "stop_monitoring"):
            action.platform = (action.platform or "").lower().strip()
            action.username = (action.username or "").strip().lstrip("@").lower()
            if action.platform not in SUPPORTED_PLATFORMS:
                errors.append(f"Unsupported platform: {action.platform!r}")
                continue
            if not action.username:
                errors.append("Missing target username")
                continue
            if hasattr(action, "max_posts"):
                action.max_posts = max(1, min(MAX_POSTS_LIMIT, int(action.max_posts or DEFAULT_MAX_POSTS)))
            valid_actions.append(action)

        elif action_type == "replace_monitored_account":
            action.platform = (action.platform or "").lower().strip()
            action.old_username = (action.old_username or "").strip().lstrip("@").lower()
            action.new_username = (action.new_username or "").strip().lstrip("@").lower()
            action.max_posts = max(1, min(MAX_POSTS_LIMIT, int(action.max_posts or DEFAULT_MAX_POSTS)))
            if action.platform not in SUPPORTED_PLATFORMS:
                errors.append(f"Unsupported platform: {action.platform!r}")
                continue
            if not action.new_username:
                errors.append("Missing destination username")
                continue
            if action.old_username == action.new_username:
                errors.append("Source and destination usernames must differ")
                continue
            valid_actions.append(action)

        elif action_type == "research_topic":
            action.keyword = (action.keyword or "").strip().lower()
            if not action.keyword:
                errors.append("Missing research keyword")
                continue
            action.platforms = [p.lower() for p in action.platforms if p.lower() in SUPPORTED_PLATFORMS] or list(SUPPORTED_PLATFORMS)
            action.max_posts_per_platform = max(1, min(MAX_POSTS_LIMIT, int(action.max_posts_per_platform or DEFAULT_MAX_POSTS)))
            valid_actions.append(action)

        elif action_type == "compare_profiles":
            targets = []
            seen = set()
            for t in action.targets:
                plat = str(t.get("platform", "")).lower().strip()
                user = str(t.get("username", "")).strip().lstrip("@").lower()
                if plat not in SUPPORTED_PLATFORMS or not user:
                    continue
                key = (plat, user)
                if key in seen:
                    continue
                seen.add(key)
                targets.append({"platform": plat, "username": user})
            if len(targets) < MIN_TARGETS:
                errors.append("compare_profiles needs at least two distinct targets")
                continue
            action.targets = targets[:MAX_TARGETS]
            action.max_posts = max(1, min(MAX_POSTS_LIMIT, int(action.max_posts or DEFAULT_MAX_POSTS)))
            valid_actions.append(action)

    plan.actions = valid_actions
    return plan, errors


# ---------------------------------------------------------------------------
# Freshness + execution
# ---------------------------------------------------------------------------

def default_ttl_hours() -> float:
    try:
        return float(os.getenv("TOPIC_STALENESS_HOURS", "24"))
    except ValueError:
        return 24.0


def _age_from_timestamp(timestamp: Optional[str]) -> Optional[float]:
    """Returns the age of `timestamp` in hours, or None if unparseable/absent."""
    if not timestamp:
        return None
    try:
        dt = datetime.fromisoformat(timestamp)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() / 3600
    except Exception:
        return None


def _get_or_create_account(db: Database, platform: str, username: str, monitoring_enabled: bool = False) -> Account:
    acc = db.get_account_by_username(platform, username)
    if acc:
        return acc
    acc = Account.create(platform=platform, username=username, monitoring_enabled=monitoring_enabled)
    return db.upsert_account(acc)


def _run_profile_scrape(
    db: Database,
    account: Account,
    max_posts: int,
    progress_callback: ProgressCallback = None,
) -> Tuple[int, Optional[str], str]:
    if account.platform == "instagram":
        from .scrapers.instagram import scrape_instagram_profile
        if progress_callback is not None:
            return scrape_instagram_profile(
                db, account, max_posts=max_posts, progress_callback=progress_callback,
            )
        return scrape_instagram_profile(db, account, max_posts=max_posts)
    if account.platform == "threads":
        if progress_callback:
            progress_callback(f"Mengambil post Threads @{account.username}…")
        from .scrapers.threads import scrape_threads_profile
        return scrape_threads_profile(db, account, max_posts=max_posts)
    if progress_callback:
        progress_callback(f"Mengambil video TikTok @{account.username}…")
    from .scrapers.tiktok import scrape_tiktok_profile
    count, err, backend = scrape_tiktok_profile(db, account, max_posts=max_posts)
    # Resilient fallback: if TikTok returns 0 posts / fails, and handle is an EasyCorp id.* handle,
    # automatically try Instagram before giving up!
    if (count == 0 or err) and account.username.startswith("id."):
        logger.info(
            "TikTok scrape for @%s yielded no posts (%s). Automatically trying Instagram fallback...",
            account.username, err,
        )
        from .scrapers.instagram import scrape_instagram_profile
        ig_acc = _get_or_create_account(db, "instagram", account.username, monitoring_enabled=False)
        if progress_callback is not None:
            return scrape_instagram_profile(db, ig_acc, max_posts=max_posts, progress_callback=progress_callback)
        return scrape_instagram_profile(db, ig_acc, max_posts=max_posts)
    return count, err, backend


def _execute_scrape_profile(
    db: Database,
    action: ScrapeProfileAction,
    ttl_hours: float,
    progress_callback: ProgressCallback = None,
) -> ActionReceipt:
    acc = _get_or_create_account(db, action.platform, action.username, monitoring_enabled=False)
    age_hours = _age_from_timestamp(db.get_account_freshness(acc.id))
    if age_hours is not None and age_hours < ttl_hours and not action.force_refresh:
        age_minutes = age_hours * 60
        receipt = ActionReceipt(
            action_type="scrape_profile", platform=action.platform, target=action.username,
            backend="cache", success=True, used_cache=True, cache_age_minutes=age_minutes,
            detail=f"Menggunakan data cache @{action.username} (umur {age_minutes:.0f} menit)",
        )
        if progress_callback:
            progress_callback(receipt.detail)
        return receipt

    if progress_callback:
        progress_callback(f"Menyiapkan scraping @{action.username}…")
    count, err, backend = _run_profile_scrape(
        db, acc, action.max_posts, progress_callback=progress_callback,
    )
    if err:
        receipt = ActionReceipt(
            action_type="scrape_profile", platform=action.platform, target=action.username,
            backend=backend, success=False, error=err,
            detail=f"Gagal mengambil data @{action.username}: {err}",
        )
    else:
        receipt = ActionReceipt(
            action_type="scrape_profile", platform=action.platform, target=action.username,
            backend=backend, success=True, posts_collected=count,
            detail=f"Berhasil mengambil @{action.username} via {backend}: {count} postingan baru",
        )
    if progress_callback:
        progress_callback(receipt.detail)
    return receipt


def _execute_research_topic(db: Database, action: ResearchTopicAction, ttl_hours: float) -> ActionReceipt:
    age_hours = _age_from_timestamp(db.get_topic_last_scraped(action.keyword))
    if age_hours is not None and age_hours < ttl_hours and not action.force_refresh:
        age_minutes = age_hours * 60
        return ActionReceipt(
            action_type="research_topic", platform=None, target=action.keyword,
            backend="cache", success=True, used_cache=True, cache_age_minutes=age_minutes,
            detail=f"Menggunakan data cache topik '{action.keyword}' (umur {age_minutes:.0f} menit)",
        )

    from .scrapers.keyword_scraper import scrape_topic_content
    result = scrape_topic_content(db, action.keyword, max_posts_per_platform=action.max_posts_per_platform)
    added = result.get("total_posts_added", 0)
    ig_err = (result.get("instagram") or {}).get("error")
    tt_err = (result.get("tiktok") or {}).get("error")
    if added == 0 and ig_err and tt_err:
        return ActionReceipt(
            action_type="research_topic", platform=None, target=action.keyword,
            backend="scraper", success=False, error=f"instagram: {ig_err}; tiktok: {tt_err}",
            detail=f"Gagal mengambil data terbaru untuk topik '{action.keyword}'",
        )
    return ActionReceipt(
        action_type="research_topic", platform=None, target=action.keyword,
        backend="scraper", success=True, posts_collected=added,
        detail=f"Riset topik '{action.keyword}': {added} postingan baru ditemukan",
    )


def _execute_compare_profiles(
    db: Database,
    action: CompareProfilesAction,
    ttl_hours: float,
    progress_callback: ProgressCallback = None,
) -> List[ActionReceipt]:
    receipts = []
    for t in action.targets:
        sub_action = ScrapeProfileAction(
            platform=t["platform"], username=t["username"],
            max_posts=action.max_posts, force_refresh=action.force_refresh,
        )
        receipts.append(_execute_scrape_profile(
            db, sub_action, ttl_hours, progress_callback=progress_callback,
        ))
    return receipts


def _execute_monitor_account(
    db: Database,
    action: MonitorAccountAction,
    progress_callback: ProgressCallback = None,
) -> ActionReceipt:
    acc = _get_or_create_account(db, action.platform, action.username, monitoring_enabled=True)
    if not acc.monitoring_enabled:
        acc = db.set_account_monitoring(acc.id, True)

    count, err, backend = _run_profile_scrape(
        db, acc, action.max_posts, progress_callback=progress_callback,
    )
    if err:
        return ActionReceipt(
            action_type="monitor_account", platform=action.platform, target=action.username,
            backend=backend, success=False, error=err,
            detail=f"Monitoring @{action.username} diaktifkan, tapi pengambilan data awal gagal: {err}",
        )
    return ActionReceipt(
        action_type="monitor_account", platform=action.platform, target=action.username,
        backend=backend, success=True, posts_collected=count,
        detail=f"Mulai monitor @{action.username}: {count} postingan tersimpan",
    )


def _execute_stop_monitoring(db: Database, action: StopMonitoringAction) -> ActionReceipt:
    acc = db.get_account_by_username(action.platform, action.username)
    if not acc:
        return ActionReceipt(
            action_type="stop_monitoring", platform=action.platform, target=action.username,
            backend="monitoring", success=False, error="not_found",
            detail=f"@{action.username} tidak ditemukan di daftar monitoring",
        )
    db.set_account_monitoring(acc.id, False)
    return ActionReceipt(
        action_type="stop_monitoring", platform=action.platform, target=action.username,
        backend="monitoring", success=True,
        detail=f"Berhenti monitor @{action.username} (data historis tetap tersimpan)",
    )


def _execute_replace_monitored_account(
    db: Database,
    action: ReplaceMonitoredAccountAction,
    progress_callback: ProgressCallback = None,
) -> ActionReceipt:
    old_acc = db.get_account_by_username(action.platform, action.old_username)
    new_acc = db.get_account_by_username(action.platform, action.new_username)

    if not old_acc and not new_acc:
        target_acc = _get_or_create_account(db, action.platform, action.new_username, monitoring_enabled=True)
    elif not new_acc:
        target_acc = db.rename_account_username(old_acc.id, action.new_username)
        if not target_acc.monitoring_enabled:
            target_acc = db.set_account_monitoring(target_acc.id, True)
    else:
        if old_acc:
            db.set_account_monitoring(old_acc.id, False)
        target_acc = db.set_account_monitoring(new_acc.id, True)

    count, err, backend = _run_profile_scrape(
        db, target_acc, action.max_posts, progress_callback=progress_callback,
    )
    if err:
        return ActionReceipt(
            action_type="replace_monitored_account", platform=action.platform, target=target_acc.username,
            backend=backend, success=False, error=err,
            detail=f"Monitoring dialihkan ke @{target_acc.username}, tapi pengambilan data awal gagal: {err}",
        )
    return ActionReceipt(
        action_type="replace_monitored_account", platform=action.platform, target=target_acc.username,
        backend=backend, success=True, posts_collected=count,
        detail=f"Monitoring diganti dari @{action.old_username or '(baru)'} ke @{target_acc.username}: {count} postingan tersimpan",
    )


def _render_context(receipts: List[ActionReceipt]) -> str:
    if not receipts:
        return ""
    lines = ["[AKSI YANG BARU DIJALANKAN OLEH SISTEM]:"]
    for r in receipts:
        status = "berhasil" if r.success else "gagal"
        lines.append(f"- {r.action_type} @{r.target} ({status}): {r.detail}")
    lines.append(
        "Gunakan hasil aksi di atas sebagai fakta terkini. Jangan mengklaim data 'terbaru' "
        "untuk aksi yang gagal atau memakai cache."
    )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

PlannerFn = Callable[[str, List[Dict[str, Any]]], Optional[Dict[str, Any]]]


class ChatActionOrchestrator:
    def __init__(self, ttl_hours: Optional[float] = None):
        self.ttl_hours = ttl_hours if ttl_hours is not None else default_ttl_hours()

    def plan_and_execute(
        self,
        db: Database,
        message: str,
        history: List[Dict[str, Any]],
        planner: Optional[PlannerFn] = None,
        progress_callback: ProgressCallback = None,
    ) -> ActionExecutionResult:
        if is_competitor_analysis_intent(message):
            return _empty_result()

        plan = parse_deterministic(db, message)

        if plan is None and planner is not None and has_action_intent(message):
            try:
                raw = planner(message, history)
            except Exception as exc:
                logger.warning(f"Chat action planner failed: {exc}")
                raw = None
            if raw:
                plan = plan_from_dict(raw)

        if plan is None:
            return _empty_result()

        if plan.needs_clarification and not plan.actions:
            return ActionExecutionResult(clarification=plan.clarification_question)

        plan, errors = validate_and_normalize(plan)
        if errors and not plan.actions:
            return ActionExecutionResult(
                clarification=plan.clarification_question or "Bisa perjelas akun, platform, atau topik yang dimaksud?",
            )
        if not plan.actions:
            return _empty_result()

        receipts: List[ActionReceipt] = []
        matched_topic: Optional[str] = None
        matched_account: Optional[Tuple[str, str]] = None
        matched_accounts: List[Tuple[str, str]] = []
        for action in plan.actions:
            action_type = getattr(action, "type", None)
            if action_type == "scrape_profile":
                receipt = _execute_scrape_profile(
                    db, action, self.ttl_hours, progress_callback=progress_callback,
                )
                receipts.append(receipt)
                matched_account = (receipt.platform, receipt.target)
            elif action_type == "research_topic":
                if progress_callback:
                    progress_callback(f"Mengambil data terbaru untuk topik '{action.keyword}'…")
                receipt = _execute_research_topic(db, action, self.ttl_hours)
                receipts.append(receipt)
                if progress_callback:
                    progress_callback(receipt.detail)
                matched_topic = action.keyword
            elif action_type == "compare_profiles":
                compare_receipts = _execute_compare_profiles(
                    db, action, self.ttl_hours, progress_callback=progress_callback,
                )
                receipts.extend(compare_receipts)
                matched_accounts = [(r.platform, r.target) for r in compare_receipts]
            elif action_type == "monitor_account":
                receipt = _execute_monitor_account(
                    db, action, progress_callback=progress_callback,
                )
                receipts.append(receipt)
                matched_account = (receipt.platform, receipt.target)
            elif action_type == "replace_monitored_account":
                receipt = _execute_replace_monitored_account(
                    db, action, progress_callback=progress_callback,
                )
                receipts.append(receipt)
                matched_account = (receipt.platform, receipt.target)
            elif action_type == "stop_monitoring":
                receipts.append(_execute_stop_monitoring(db, action))

        return ActionExecutionResult(
            receipts=receipts,
            matched_topic=matched_topic,
            matched_account=matched_account,
            matched_accounts=matched_accounts,
            context_text=_render_context(receipts),
            status_lines=[r.detail for r in receipts],
        )
