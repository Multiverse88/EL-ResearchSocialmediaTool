from __future__ import annotations

import hmac
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .models import Account
from .db import Database
from .claude_client import ClaudeChatHandler
from .scrapers.runner import run_scraping_job, seed_default_accounts_if_empty

logger = logging.getLogger("server")

DB_PATH = os.getenv("DATABASE_PATH", "social_media.db")
_db: Optional[Database] = None
_claude_handler: Optional[ClaudeChatHandler] = None


def get_db() -> Database:
    global _db
    if _db is None:
        _db = Database(DB_PATH)
        seed_default_accounts_if_empty(_db)
    return _db


def get_claude_handler() -> ClaudeChatHandler:
    global _claude_handler
    if _claude_handler is None:
        _claude_handler = ClaudeChatHandler()
    return _claude_handler


# --- Write-endpoint API key auth ---
# Set API_SECRET_KEY in the environment to require `X-API-Key` (or `Authorization: Bearer`)
# on state-changing endpoints. Left unset, auth is skipped (zero-config internal tool default)
# but a warning is logged once so the gap is visible in logs.
API_SECRET_KEY = os.getenv("API_SECRET_KEY", "").strip()
if not API_SECRET_KEY:
    logger.warning(
        "API_SECRET_KEY is not set. Write endpoints (POST /accounts, /topics, /scrape/run, "
        "/api/seed-sample-data) are UNAUTHENTICATED. Set API_SECRET_KEY in Dokploy Environment "
        "to require an X-API-Key header on those routes."
    )


def require_api_key(
    x_api_key: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
) -> None:
    secret = os.getenv("API_SECRET_KEY", "").strip()
    if not secret:
        return
    provided = x_api_key
    if not provided and authorization and authorization.lower().startswith("bearer "):
        provided = authorization[7:].strip()
    if provided != secret:
        raise HTTPException(status_code=401, detail="Missing or invalid API key. Provide X-API-Key header.")


# --- Chat-action authorization ---
# Set CHAT_ACTION_API_KEY in the environment to require `X-API-Key` (or `Authorization:
# Bearer`) on the chat endpoints. Chat can translate natural-language messages into
# Bright Data scrapes and monitoring mutations (see chat_actions.py), so — unlike the
# zero-config internal-tool default for plain CRUD writes above — this is meant to be
# set in any deployment reachable by more than the operator's own OpenWebUI instance.
# Left unset, chat stays open (matches this project's existing zero-config default) but
# a warning is logged once so the gap is visible in logs.
CHAT_ACTION_API_KEY = os.getenv("CHAT_ACTION_API_KEY", "").strip()
if not CHAT_ACTION_API_KEY:
    logger.warning(
        "CHAT_ACTION_API_KEY is not set. Chat endpoints (POST /chat, /v1/chat/completions) are "
        "UNAUTHENTICATED and can trigger Bright Data scraping/monitoring mutations from any caller. "
        "Set CHAT_ACTION_API_KEY in Dokploy Environment and configure it as your OpenWebUI "
        "instance's OPENAI_API_KEY to restrict chat-driven actions to your internal OpenWebUI."
    )


def require_chat_action_key(
    x_api_key: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
) -> None:
    secret = os.getenv("CHAT_ACTION_API_KEY", "").strip()
    if not secret:
        return
    provided = x_api_key
    if not provided and authorization and authorization.lower().startswith("bearer "):
        provided = authorization[7:].strip()
    if not provided or not hmac.compare_digest(provided, secret):
        raise HTTPException(status_code=401, detail="Missing or invalid API key. Provide X-API-Key or Authorization: Bearer header.")


# --- Simple in-memory rate limiter for chat endpoints ---
# Bounds request volume per client IP so a runaway script/loop can't burn 9router budget.
_chat_request_log: Dict[str, List[float]] = {}


def enforce_chat_rate_limit(request: Request) -> None:
    limit = int(os.getenv("CHAT_RATE_LIMIT_PER_MINUTE", "20"))
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()
    window_start = now - 60.0
    history = _chat_request_log.setdefault(client_ip, [])
    history[:] = [t for t in history if t > window_start]
    if len(history) >= limit:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded ({limit} pesan/menit). Coba lagi sebentar.",
        )
    history.append(now)


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_db()
    get_claude_handler()
    yield
    global _db
    if _db:
        _db.close()
        logger.info("Database closed.")


app = FastAPI(
    title="EasyCorp Social Media Intelligence & AI Chat API",
    description="Automated social media scraping pipeline & Claude tool-use chat panel API",
    version="1.0.0",
    lifespan=lifespan,
)

_default_allowed_origins = [
    "https://easylegal-socialmediaresearchtool-kftevw-d3117c-157-10-252-77.sslip.io",
    "http://100.81.215.57:8000",
    "http://100.81.215.57:3080",
    "http://localhost:8000",
    "http://localhost:3080",
]
_allowed_origins_env = os.getenv("ALLOWED_ORIGINS", "").strip()
ALLOWED_ORIGINS = (
    [o.strip() for o in _allowed_origins_env.split(",") if o.strip()]
    if _allowed_origins_env
    else _default_allowed_origins
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Pydantic Request Models
class CreateAccountRequest(BaseModel):
    platform: str = Field(..., description="Platform: instagram | tiktok")
    username: str = Field(..., description="Account username without @")
    is_own_brand: bool = Field(False, description="True if EasyCorp brand, False if competitor")

class CreateTopicRequest(BaseModel):
    keyword: str = Field(..., description="Kata kunci topik riset (contoh: 'pendirian PT')")
    category: str = Field("Umum", description="Kategori topik (Legalitas, Pajak, Office, dll)")


class ScrapeTopicRequest(BaseModel):
    keyword: str = Field(..., description="Kata kunci yang ingin discrape kontennya")
    max_posts: int = Field(25, ge=5, le=100)
class ChatRequest(BaseModel):
    message: Optional[str] = None
    messages: Optional[List[Dict[str, Any]]] = None
    model: Optional[str] = None
    stream: Optional[bool] = None


class ScrapeRunRequest(BaseModel):
    platform: Optional[str] = None
    username: Optional[str] = None
    max_posts: int = 30


# REST Endpoints (PRD Section 7)

@app.get("/api/health")
def get_health():
    return {
        "status": "healthy",
        "service": "social-media-research",
        "timestamp": time.time(),
        "database": DB_PATH,
    }


@app.get("/accounts")
def list_accounts():
    """GET /accounts - List akun yang dimonitor."""
    db_inst = get_db()
    accounts = db_inst.list_accounts()
    return {
        "status": "success",
        "count": len(accounts),
        "data": [acc.to_dict() for acc in accounts],
    }

@app.get("/accounts/search")
def search_accounts(q: str = Query(..., description="Keyword untuk mencari username"), platform: Optional[str] = Query(None)):
    """GET /accounts/search?q=...&platform=instagram - Cari akun berdasarkan keyword."""
    if not q or not q.strip():
        raise HTTPException(status_code=400, detail="Parameter 'q' wajib diisi")
    db_inst = get_db()
    accounts = db_inst.search_accounts(keyword=q, platform=platform)
    return {
        "status": "success",
        "count": len(accounts),
        "data": [acc.to_dict() for acc in accounts],
    }



@app.post("/accounts", dependencies=[Depends(require_api_key)])
def create_account(payload: CreateAccountRequest):
    """POST /accounts - Tambah akun baru untuk discrape."""
    platform = payload.platform.strip().lower()
    if platform not in ("instagram", "tiktok"):
        raise HTTPException(status_code=400, detail="Platform must be 'instagram' or 'tiktok'")

    account = Account.create(
        platform=platform,
        username=payload.username,
        is_own_brand=payload.is_own_brand,
    )
    saved = get_db().upsert_account(account)
    return {
        "status": "success",
        "message": f"Account @{saved.username} ({saved.platform}) registered",
        "data": saved.to_dict(),
    }


@app.get("/posts")
def get_posts(
    account_id: Optional[str] = Query(None),
    platform: Optional[str] = Query(None),
    username: Optional[str] = Query(None),
    keyword: Optional[str] = Query(None),
    topic: Optional[str] = Query(None),
    order_by: str = Query("posted_at", description="Sort by: posted_at | likes | views | comments"),
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """GET /posts?keyword=&topic=&platform=&order_by=&limit=&offset="""
    posts = get_db().query_posts(
        account_id=account_id,
        platform=platform,
        username=username,
        keyword=keyword,
        topic=topic,
        order_by=order_by,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )
    return {
        "status": "success",
        "count": len(posts),
        "data": posts,
    }


@app.get("/posts/summary")
def get_posts_summary(
    account_id: Optional[str] = Query(None),
    username: Optional[str] = Query(None),
    platform: Optional[str] = Query(None),
):
    """GET /posts/summary?account_id="""
    db_inst = get_db()
    target_account = None
    if account_id:
        target_account = db_inst.get_account(account_id)
    elif username and platform:
        target_account = db_inst.get_account_by_username(platform, username)
    elif username:
        for p in ("instagram", "tiktok"):
            target_account = db_inst.get_account_by_username(p, username)
            if target_account:
                break

    if not target_account:
        raise HTTPException(status_code=404, detail="Account not found. Please provide valid account_id or username.")

    summary = db_inst.get_account_summary(target_account.id)
    if not summary:
        return {
            "status": "success",
            "account": target_account.to_dict(),
            "summary": {
                "total_posts": 0,
                "total_likes": 0,
                "avg_likes": 0.0,
                "total_comments": 0,
                "avg_comments": 0.0,
                "total_views": 0,
                "avg_views": 0.0,
            },
        }

    return {
        "status": "success",
        "account": target_account.to_dict(),
        "summary": summary,
    }

# Topic Research Endpoints

@app.get("/topics")
def list_topics():
    """GET /topics - List topik/kata kunci yang sedang diriset."""
    topics = get_db().list_topics()
    return {
        "status": "success",
        "count": len(topics),
        "data": [t.to_dict() for t in topics],
    }


@app.post("/topics", dependencies=[Depends(require_api_key)])
def create_topic(payload: CreateTopicRequest):
    """POST /topics - Daftarkan kata kunci / topik baru untuk riset."""
    from .models import Topic
    topic = Topic.create(keyword=payload.keyword, category=payload.category)
    saved = get_db().upsert_topic(topic)
    return {
        "status": "success",
        "message": f"Topik '{saved.keyword}' berhasil didaftarkan untuk riset",
        "data": saved.to_dict(),
    }


@app.get("/topics/summary")
def get_topic_summary_endpoint(
    keyword: str = Query(..., description="Kata kunci yang ingin diriset"),
    platform: Optional[str] = Query(None, description="Filter: instagram | tiktok"),
):
    """GET /topics/summary?keyword=&platform= - Riset performa topik dan postingan viral."""
    summary = get_db().get_topic_summary(keyword=keyword, platform=platform)
    return {
        "status": "success",
        "data": summary,
    }


@app.get("/topics/compare")
def compare_topics_endpoint(
    keywords: str = Query(..., description="Daftar kata kunci dipisah koma (misal: 'pendirian PT,virtual office,pajak')"),
):
    """GET /topics/compare?keywords= - Bandingkan performa antar beberapa topik konten."""
    kw_list = [k.strip() for k in keywords.split(",") if k.strip()]
    result = get_db().compare_topics(kw_list)
    return {
        "status": "success",
        "data": result,
    }


@app.post("/topics/scrape", dependencies=[Depends(require_api_key)])
def scrape_topic_endpoint(payload: ScrapeTopicRequest, background_tasks: BackgroundTasks):
    """POST /topics/scrape - Trigger scraping konten media sosial berdasarkan topik/hashtag."""
    from .scrapers.keyword_scraper import scrape_topic_content
    background_tasks.add_task(
        scrape_topic_content,
        db=get_db(),
        keyword=payload.keyword,
        max_posts_per_platform=payload.max_posts,
    )
    return {
        "status": "accepted",
        "message": f"Scraping konten untuk topik '{payload.keyword}' dimulai di background.",
    }



@app.post("/chat", dependencies=[Depends(enforce_chat_rate_limit), Depends(require_chat_action_key)])
def chat_endpoint(payload: ChatRequest):
    """
    POST /chat - Endpoint utama chat panel.
    Terima pesan user, panggil Claude API dengan tool use, return jawaban natural language & data.
    """
    user_msg = payload.message
    if not user_msg and payload.messages:
        user_messages = [m.get("content") for m in payload.messages if m.get("role") == "user"]
        if user_messages:
            user_msg = user_messages[-1]
            if isinstance(user_msg, list):
                user_msg = " ".join([b.get("text", "") for b in user_msg if isinstance(b, dict)])

    if not user_msg:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    try:
        result = get_claude_handler().process_chat(
            db=get_db(),
            message=user_msg,
            conversation_history=payload.messages[:-1] if payload.messages else None,
        )
    except Exception as exc:
        # Chat is user-facing: an unhandled exception anywhere in the answer pipeline
        # must never surface as a raw 500 — always give the user a usable response.
        logger.error(f"Unhandled error in /chat for message {user_msg!r}: {exc}", exc_info=True)
        result = {
            "status": "error",
            "user_query": user_msg,
            "reply": "Maaf, terjadi kesalahan internal saat memproses permintaan Anda. Silakan coba lagi.",
        }
    return result


@app.get("/v1/models")
def list_openai_models():
    """
    OpenAI-compatible models discovery endpoint for Open WebUI.
    """
    return {
        "object": "list",
        "data": [
            {
                "id": "social-media-claude-agent",
                "object": "model",
                "created": 1700000000,
                "owned_by": "easycorp",
                "permission": [],
                "root": "social-media-claude-agent",
                "parent": None,
            },
            {
                "id": "easylegal-marketing-ai",
                "object": "model",
                "created": 1700000000,
                "owned_by": "easycorp",
                "permission": [],
                "root": "easylegal-marketing-ai",
                "parent": None,
            },
        ],
    }


@app.post("/v1/chat/completions", dependencies=[Depends(enforce_chat_rate_limit), Depends(require_chat_action_key)])
def openai_compatible_chat(payload: ChatRequest):
    """
    OpenAI-compatible endpoint for Open WebUI, LibreChat, and standard AI webchat clients.
    Streams live SSE chunks (content + reasoning) by default so Open WebUI renders the
    typing/thinking animation; pass stream=false for a single buffered JSON response.
    """
    user_msg = payload.message
    if not user_msg and payload.messages:
        for m in reversed(payload.messages):
            if m.get("role") == "user":
                user_msg = m.get("content")
                break

    if not user_msg:
        user_msg = "Halo"

    history = payload.messages[:-1] if payload.messages else None
    model_name = payload.model or "social-media-claude-agent"
    db_inst = get_db()
    handler = get_claude_handler()

    if payload.stream is False:
        try:
            result = handler.process_chat(db=db_inst, message=str(user_msg), conversation_history=history)
        except Exception as exc:
            logger.error(f"Unhandled error in buffered /v1/chat/completions for message {user_msg!r}: {exc}", exc_info=True)
            result = {"reply": "Maaf, terjadi kesalahan internal saat memproses permintaan Anda. Silakan coba lagi."}
        reply_content = result.get("reply", "")
        return {
            "id": f"chatcmpl-{int(time.time())}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model_name,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": reply_content,
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": len(str(user_msg)) // 4,
                "completion_tokens": len(reply_content) // 4,
                "total_tokens": (len(str(user_msg)) + len(reply_content)) // 4,
            },
        }

    def _sse_generator():
        chat_id = f"chatcmpl-{int(time.time())}"
        created = int(time.time())

        def _chunk(delta: Dict[str, Any], finish_reason: Optional[str] = None) -> str:
            payload_obj = {
                "id": chat_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model_name,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
            }
            return f"data: {json.dumps(payload_obj, ensure_ascii=False)}\n\n"

        yield _chunk({"role": "assistant"})
        try:
            for piece in handler.stream_chat(db=db_inst, message=str(user_msg), conversation_history=history):
                text = piece.get("text", "")
                if not text:
                    continue
                if piece.get("type") == "reasoning":
                    yield _chunk({"reasoning_content": text})
                else:
                    yield _chunk({"content": text})
        except Exception as exc:
            logger.error(f"Streaming error: {exc}")
            yield _chunk({"content": f"Terjadi kesalahan saat memproses permintaan: {exc}"})

        yield _chunk({}, finish_reason="stop")
        yield "data: [DONE]\n\n"

    return StreamingResponse(_sse_generator(), media_type="text/event-stream")

@app.post("/api/seed-sample-data", dependencies=[Depends(require_api_key)])
def seed_sample_data(posts_per_account: int = Query(25, ge=5, le=100)):
    """
    Populates realistic marketing sample posts for EasyLegal, EasyTax, EasyOffice,
    and competitor accounts for instant testing and AI chat analysis.
    """
    from .sample_data import seed_marketing_sample_data

    current_db = get_db()
    acc_count, posts_count = seed_marketing_sample_data(current_db, posts_per_account=posts_per_account)

    return {
        "status": "success",
        "message": f"Berhasil menambahkan {posts_count} postingan sample riset untuk {acc_count} akun.",
        "accounts_count": acc_count,
        "posts_count": posts_count,
    }


@app.post("/scrape/run", dependencies=[Depends(require_api_key)])
def trigger_scrape(payload: ScrapeRunRequest, background_tasks: BackgroundTasks):
    """
    Triggers scraping on demand in background task.
    """
    background_tasks.add_task(
        run_scraping_job,
        db=get_db(),
        platform=payload.platform,
        username=payload.username,
        max_posts_per_account=payload.max_posts,
    )
    return {
        "status": "accepted",
        "message": f"Scraping job queued for background execution (target: {payload.username or payload.platform or 'all accounts'})",
    }


@app.get("/scrape/logs")
def get_scrape_logs(limit: int = Query(50, ge=1, le=100)):
    """GET /scrape/logs - List riwayat log scraping."""
    logs = get_db().list_scrape_logs(limit=limit)
    return {
        "status": "success",
        "count": len(logs),
        "data": logs,
    }


# Static Web Dashboard Mount
os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def serve_dashboard():
    """Serves the internal web-based dashboard."""
    index_file = "static/index.html"
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return {"message": "EasyCorp Social Media Intelligence API is running. Visit /docs for API documentation."}
