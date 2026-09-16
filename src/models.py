from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional
import uuid


@dataclass
class Account:
    id: str
    platform: str  # "instagram" | "tiktok"
    username: str
    is_own_brand: bool
    created_at: str
    monitoring_enabled: bool = True

    @classmethod
    def create(
        cls,
        platform: str,
        username: str,
        is_own_brand: bool = False,
        account_id: Optional[str] = None,
        created_at: Optional[str] = None,
        monitoring_enabled: bool = True,
    ) -> Account:
        return cls(
            id=account_id or str(uuid.uuid4()),
            platform=platform.lower(),
            username=username.lower().strip().lstrip("@"),
            is_own_brand=is_own_brand,
            created_at=created_at or datetime.now(timezone.utc).isoformat(),
            monitoring_enabled=monitoring_enabled,
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

    @classmethod
    def create(
        cls,
        platform: str,
        status: str,
        error_message: Optional[str] = None,
        run_at: Optional[str] = None,
        log_id: Optional[str] = None,
    ) -> ScrapeLog:
        return cls(
            id=log_id or str(uuid.uuid4()),
            platform=platform.lower(),
            status=status.lower(),
            error_message=error_message,
            run_at=run_at or datetime.now(timezone.utc).isoformat(),
        )

    def to_dict(self) -> dict:
        return asdict(self)
