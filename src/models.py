from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Literal, Optional, TypedDict
import uuid

if TYPE_CHECKING:
    from .chat_actions import ActionExecutionResult


class ChatMessage(TypedDict):
    role: Literal["user", "assistant"]
    content: str


FreshnessStatus = Literal[
    "fresh",
    "stale",
    "refreshed",
    "refresh_failed",
    "disabled",
    "unknown",
]


@dataclass(frozen=True)
class Subject:
    kind: Literal["topic", "account", "accounts", "competitor", "none"]
    keys: List[str]
    confidence: float
    resolution_source: Literal["explicit", "history", "action", "classifier"]


@dataclass(frozen=True)
class FreshnessInfo:
    status: FreshnessStatus
    data_as_of: Optional[str]
    ttl_hours: float
    error: Optional[str]


@dataclass(frozen=True)
class EvidenceRecord:
    source_id: str
    source_kind: Literal["post", "account"]
    platform: Literal["instagram", "tiktok", "threads"]
    account: Optional[str]
    topic: Optional[str]
    metrics: Dict[str, Optional[int]]
    posted_at: Optional[str]
    scraped_at: str
    url: Optional[str]
    missing_fields: List[str]


@dataclass(frozen=True)
class AnswerConstraints:
    unavailable_metrics: List[str]
    warnings: List[str]


@dataclass(frozen=True)
class PreparedTurn:
    request_id: str
    query: str
    history: List[ChatMessage]
    subject: Subject
    action_result: Optional["ActionExecutionResult"]
    freshness: FreshnessInfo
    evidence: List[EvidenceRecord]
    constraints: AnswerConstraints


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


@dataclass
class Account:
    id: str
    platform: str  # "instagram" | "tiktok" | "threads"
    username: str
    is_own_brand: bool
    created_at: str
    monitoring_enabled: bool = True
    follower_count: Optional[int] = None

    @classmethod
    def create(
        cls,
        platform: str,
        username: str,
        is_own_brand: bool = False,
        account_id: Optional[str] = None,
        created_at: Optional[str] = None,
        monitoring_enabled: bool = True,
        follower_count: Optional[int] = None,
    ) -> Account:
        return cls(
            id=account_id or str(uuid.uuid4()),
            platform=platform.lower(),
            username=username.lower().strip().lstrip("@"),
            is_own_brand=is_own_brand,
            created_at=created_at or datetime.now(timezone.utc).isoformat(),
            monitoring_enabled=monitoring_enabled,
            follower_count=follower_count,
        )

    def to_dict(self) -> dict:
        return asdict(self)
@dataclass
class Topic:
    id: str
    keyword: str
    category: str
    created_at: str

    @classmethod
    def create(
        cls,
        keyword: str,
        category: str = "Umum",
        topic_id: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> Topic:
        return cls(
            id=topic_id or str(uuid.uuid4()),
            keyword=keyword.strip().lower(),
            category=category.strip(),
            created_at=created_at or datetime.now(timezone.utc).isoformat(),
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Post:
    id: str
    account_id: str
    platform_post_id: str
    caption: str
    media_url: str
    likes: int
    comments: int
    views: Optional[int]
    posted_at: str
    scraped_at: str
    platform: str = ""
    topic: str = ""
    content_type: str = ""
    post_url: str = ""
    @classmethod
    def create(
        cls,
        account_id: str,
        platform_post_id: str,
        caption: str,
        media_url: str,
        likes: int,
        comments: int,
        views: Optional[int] = None,
        posted_at: Optional[str] = None,
        scraped_at: Optional[str] = None,
        post_id: Optional[str] = None,
        platform: str = "",
        topic: str = "",
        content_type: str = "",
        post_url: str = "",
    ) -> Post:
        return cls(
            id=post_id or str(uuid.uuid4()),
            account_id=account_id,
            platform_post_id=str(platform_post_id),
            caption=caption or "",
            media_url=media_url or "",
            likes=int(likes),
            comments=int(comments),
            views=int(views) if views is not None else None,
            posted_at=posted_at or datetime.now(timezone.utc).isoformat(),
            scraped_at=scraped_at or datetime.now(timezone.utc).isoformat(),
            platform=platform.lower() if platform else "",
            topic=topic.strip().lower() if topic else "",
            content_type=content_type.strip().lower(),
            post_url=post_url or "",
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ScrapeLog:
    id: str
    platform: str
    status: str  # "success" | "failed"
    error_message: Optional[str]
    run_at: str
    target: str = ""  # username or topic/keyword this scrape run was about, "" for batch/summary runs

    @classmethod
    def create(
        cls,
        platform: str,
        status: str,
        error_message: Optional[str] = None,
        run_at: Optional[str] = None,
        log_id: Optional[str] = None,
        target: str = "",
    ) -> ScrapeLog:
        return cls(
            id=log_id or str(uuid.uuid4()),
            platform=platform.lower(),
            status=status.lower(),
            error_message=error_message,
            run_at=run_at or datetime.now(timezone.utc).isoformat(),
            target=target,
        )

    def to_dict(self) -> dict:
        return asdict(self)

@dataclass
class TopicScrape:
    id: str
    keyword: str
    platform: str
    posts_found: int
    scraped_at: str
    status: str

    @classmethod
    def create(
        cls,
        keyword: str,
        platform: str,
        posts_found: int = 0,
        status: str = "success",
        scraped_at: Optional[str] = None,
        scrape_id: Optional[str] = None,
    ) -> TopicScrape:
        return cls(
            id=scrape_id or str(uuid.uuid4()),
            keyword=keyword.strip().lower(),
            platform=platform.lower(),
            posts_found=posts_found,
            scraped_at=scraped_at or datetime.now(timezone.utc).isoformat(),
            status=status.lower(),
        )

    def to_dict(self) -> dict:
        return asdict(self)
