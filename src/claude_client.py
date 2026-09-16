from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

import anthropic
import httpx
from .db import Database
from .tools import CLAUDE_TOOLS_SPEC, execute_claude_tool

logger = logging.getLogger("backend.claude")

DEFAULT_SYSTEM_PROMPT = """
Anda adalah AI Social Media Content & Topic Researcher untuk EasyCorp (EasyLegal, EasyTax, EasyOffice).
Fokus utama Anda adalah melakukan riset topik dan kata kunci konten media sosial (Instagram & TikTok), menganalisis tren performa, menemukan konten viral, dan merekomendasikan ide konten berkinerja tinggi bagi tim marketing.

Panduan:
1. Berikan analisis berbasis data yang objektif dan terstruktur.
2. Jelaskan metrik utama seperti total posts, rata-rata likes, views, dan engagement rate.
3. Berikan rekomendasi taktis (misal: hook pembuka konten, format video Reels/TikTok vs carousel).
4. Jawab dalam Bahasa Indonesia yang profesional, ramah, dan solutif untuk tim marketing.
5. Tulis jawaban dalam paragraf atau bullet Markdown yang mengalir natural. JANGAN PERNAH memakai notasi internal/scratchpad seperti "[nama section] -> skipped: alasan" - itu terlihat seperti catatan debug, bukan jawaban untuk manusia.
6. Kalau ada bagian yang diminta user tapi datanya memang tidak tersedia di database (lihat blok [KETERBATASAN DATA SAAT INI] di bawah kalau ada), sampaikan itu dalam satu-dua kalimat jujur dan natural (bukan notasi teknis), lalu tetap berikan insight terbaik dari data lain yang memang tersedia. Jangan pernah mengarang angka untuk metrik yang tidak tersedia.
"""

SEED_TOPICS = [
    "pendirian pt", "izin usaha oss", "konsultasi pajak", "merek dagang hki",
    "kontrak kerja", "perjanjian bisnis", "perizinan", "laporan spt tahunan",
    "sewa virtual office", "npwp badan usaha", "perubahan akta", "legalitas umkm",
    "biaya pembuatan pt", "syarat izin edar bpom", "rekening bank perusahaan",
    "pt pma", "virtual office", "pajak", "hki", "merek", "oss", "legalitas",
]

STOP_WORDS = {
    "saya", "kami", "kita", "kamu", "anda", "dia", "mereka",
    "butuh", "ingin", "mau", "minta", "tolong", "bisa", "buatkan",
    "riset", "cari", "topik", "konten", "apa", "yang", "tentang",
    "bagaimana", "gimana", "dong", "media", "sosial", "di", "dan",
    "atau", "dari", "ke", "untuk", "ini", "itu", "ada", "apakah",
    "halo", "hai", "tes", "test", "ya", "kan", "nih"
}


class ClaudeChatHandler:
    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None, base_url: Optional[str] = None):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self.model = model or os.getenv("CLAUDE_MODEL", "Thinking")
        self.base_url = base_url or os.getenv("ANTHROPIC_BASE_URL") or os.getenv("OPENAI_API_BASE_URL")

        # Initialize Anthropic SDK only if it's a native Anthropic key
        if self.api_key and self.api_key.startswith("sk-ant-"):
            self.client = anthropic.Anthropic(api_key=self.api_key, base_url=self.base_url)
        else:
            self.client = None

    def _resolve_topics(self, db: Database) -> List[str]:
        """
        Returns known topic keywords for matching, combining live topics discovered
        via scraping/seeding (tabel `topics`) with a static seed list for cold-start.
        Longer phrases first so specific matches win over generic substrings.
        """
        try:
            db_topics = [t.keyword for t in db.list_topics()]
        except Exception:
            db_topics = []
        merged = sorted(set(db_topics) | set(SEED_TOPICS), key=len, reverse=True)
        return merged

    def process_chat(
        self,
        db: Database,
        message: str,
        conversation_history: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Processes a marketing topic research query.
        Uses 9router / OpenAI-compatible gateway or Claude API if key is set,
        otherwise uses local intent matching fallback.
        """
        if not message or not message.strip():
            return {"status": "error", "message": "Pesan chat tidak boleh kosong"}

        if not self.api_key:
            logger.info("No AI API key configured. Running local intent fallback handler.")
            return self._local_fallback_handler(db, message)

        # Route 1: 9router or OpenAI-compatible router
        is_openai_router = (
            not self.api_key.startswith("sk-ant-")
            or (self.base_url and "anthropic.com" not in self.base_url)
        )

        if is_openai_router:
            try:
                return self._call_openai_router(db, message, conversation_history or [])
            except Exception as exc:
                logger.error(f"Error calling 9router/OpenAI gateway: {exc}. Falling back to local handler.")
                fallback = self._local_fallback_handler(db, message)
                fallback["warning"] = f"AI Router notice: {str(exc)} (Menampilkan hasil dari query database internal)."
                return fallback

        # Route 2: Native Anthropic Claude API
        if self.client:
            try:
                return self._claude_tool_use_loop(db, message, conversation_history or [])
            except Exception as exc:
                logger.error(f"Error calling Claude API: {exc}. Falling back to local handler.")
                fallback = self._local_fallback_handler(db, message)
                fallback["warning"] = f"Claude API notice: {str(exc)} (Menampilkan hasil dari query database internal)."
                return fallback

        return self._local_fallback_handler(db, message)

    def stream_chat(
        self,
        db: Database,
        message: str,
        conversation_history: Optional[List[Dict[str, Any]]] = None,
    ):
        """
        Generator version of process_chat for live streaming (typing/thinking animation).
        Yields {"type": "reasoning"|"content", "text": str} chunks as they become available.
        Falls back to a single content chunk when streaming isn't possible.
        """
        history = conversation_history or []

        if not message or not message.strip():
            yield {"type": "content", "text": "Pesan chat tidak boleh kosong"}
            return

        if not self.api_key:
            fallback = self._local_fallback_handler(db, message)
            yield {"type": "content", "text": fallback.get("reply", "")}
            return

        is_openai_router = (
            not self.api_key.startswith("sk-ant-")
            or (self.base_url and "anthropic.com" not in self.base_url)
        )

        if is_openai_router:
            yield from self.stream_router_chat(db, message, history)
            return

        if self.client:
            try:
                result = self._claude_tool_use_loop(db, message, history)
                yield {"type": "content", "text": result.get("reply", "")}
            except Exception as exc:
                logger.error(f"Error calling Claude API: {exc}. Falling back to local handler.")
                fallback = self._local_fallback_handler(db, message)
                yield {"type": "content", "text": fallback.get("reply", "")}
            return

        fallback = self._local_fallback_handler(db, message)
        yield {"type": "content", "text": fallback.get("reply", "")}

    def _resolve_matched_topic(
        self,
        db: Database,
        user_message: str,
        history: List[Dict[str, Any]],
    ) -> str:
        """Matches the user's message (or recent history, for follow-ups) to a known/likely topic keyword."""
        msg_lower = user_message.lower()
        matched_topic = None
        for t in self._resolve_topics(db):
            if t in msg_lower:
                matched_topic = t
                break
        # Look back in history if this is a follow-up query like "coba buat list nya" or "di akun mana saja"
        if not matched_topic and history:
            for past_msg in reversed(history):
                past_content = str(past_msg.get("content", "")).lower()
                for t in self._resolve_topics(db):
                    if t in past_content:
                        matched_topic = t
                        break
                if matched_topic:
                    break

        if not matched_topic:
            words = [w for w in re.findall(r"\b[a-zA-Z0-9_]+\b", msg_lower) if len(w) > 2 and w not in STOP_WORDS]
            matched_topic = words[0] if words else "pendirian PT"
        return matched_topic

    def _ensure_topic_freshness(self, db: Database, matched_topic: str) -> Optional[str]:
        """
        Live-scrapes the topic before answering if we have no data yet, or the newest data is
        older than TOPIC_STALENESS_HOURS (default 6h). Runs synchronously (blocks the chat
        response) — this trades response latency for data freshness, and consumes scraper
        quota (Apify credit, or risks free-tier IP/account rate limiting) on every genuinely
        new or stale topic a user asks about. Disable via ENABLE_LIVE_SCRAPE_ON_CHAT=false.
        Returns a short human-readable status string if a scrape ran, else None.
        """
        if os.getenv("ENABLE_LIVE_SCRAPE_ON_CHAT", "true").lower() in ("false", "0", "no"):
            return None

        try:
            staleness_hours = float(os.getenv("TOPIC_STALENESS_HOURS", "6"))
        except ValueError:
            staleness_hours = 6.0

        needs_scrape = True
        last_scraped = db.get_topic_last_scraped(matched_topic)
        if last_scraped:
            try:
                from datetime import datetime, timezone
                last_dt = datetime.fromisoformat(last_scraped)
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                age_hours = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
                needs_scrape = age_hours >= staleness_hours
            except Exception:
                needs_scrape = True

        if not needs_scrape:
            return None

        try:
            from .scrapers.keyword_scraper import scrape_topic_content
            max_posts = int(os.getenv("MAX_POSTS_PER_SCRAPE", "30"))
            logger.info(f"Live scrape-on-chat: fetching fresh data for topic '{matched_topic}'")
            result = scrape_topic_content(db, matched_topic, max_posts_per_platform=max_posts)
            added = result.get("total_posts_added", 0)
            return (
                f"🔍 Mengambil data terbaru untuk topik \"{matched_topic}\" dari Instagram & TikTok "
                f"({added} postingan baru ditemukan)...\n\n"
            )
        except Exception as exc:
            logger.warning(f"Live scrape-on-chat failed for topic '{matched_topic}': {exc}")
            return None

    def _build_router_context(
        self,
        db: Database,
        user_message: str,
        history: List[Dict[str, Any]],
        matched_topic: Optional[str] = None,
    ):
        """
        Resolves topic (unless already provided by the caller), live-scrapes it if stale,
        pulls factual DB context, and builds the router request payload.
        """
        if matched_topic is None:
            matched_topic = self._resolve_matched_topic(db, user_message, history)
            self._ensure_topic_freshness(db, matched_topic)

        # Pull real data from database for this topic to inject as factual context
        topic_data = db.get_topic_summary(matched_topic)
        viral_posts = db.query_posts(topic=matched_topic, order_by="likes", limit=8)
        ig_summary = db.get_topic_summary(matched_topic, platform="instagram")
        tt_summary = db.get_topic_summary(matched_topic, platform="tiktok")
        account_breakdown = db.get_topic_account_breakdown(matched_topic, limit=8)

        context_text = f"""
[DATA FAKTUAL HASIL SCRAPING MEDIA SOSIAL]:
Topik / Kata Kunci: '{matched_topic}'
Total Postingan Termonitor (semua platform): {topic_data.get('total_posts', 0)} post
Rata-Rata Likes per Post: {topic_data.get('avg_likes', 0):,} likes
Puncak Likes Tertinggi: {topic_data.get('max_likes', 0):,} likes
Rata-Rata Views (Video TikTok/Reels): {topic_data.get('avg_views', 0):,} views
Engagement Rate Rata-Rata: {topic_data.get('engagement_rate', 0)}%

Breakdown per Platform:
- Instagram: {ig_summary.get('total_posts', 0)} post, rata-rata {ig_summary.get('avg_likes', 0):,} likes
- TikTok: {tt_summary.get('total_posts', 0)} post, rata-rata {tt_summary.get('avg_likes', 0):,} likes, rata-rata {tt_summary.get('avg_views', 0):,} views

Daftar Postingan Viral Terkait (Gunakan data akun dan metrik berikut jika user bertanya akun mana atau minta daftar postingan):
"""
        for idx, p in enumerate(viral_posts, 1):
            v_txt = f"{p['views']:,} views" if p.get("views") is not None else "Photo post"
            context_text += f"{idx}. Akun @{p['username']} [{p['platform'].upper()}]: \"{p['caption'][:120]}...\" (Likes: {p['likes']:,}, Views: {v_txt})\n"

        context_text += "\nAkun Paling Aktif Membahas Topik Ini (jumlah post & rata-rata likes yang tertangkap scraping):\n"
        if account_breakdown:
            for idx, a in enumerate(account_breakdown, 1):
                context_text += (
                    f"{idx}. @{a['username']} [{a['platform'].upper()}]: {a['post_count']} post, "
                    f"rata-rata {a['avg_likes']:,} likes, likes tertinggi {a['max_likes']:,}\n"
                )
        else:
            context_text += "(Belum ada akun yang tertangkap scraping untuk topik ini.)\n"

        context_text += """
[KETERBATASAN DATA SAAT INI]:
- Reach/impression spesifik untuk Instagram Reels TIDAK tersedia (sistem hanya mencatat likes, comments, views).
- Follower count dan frekuensi posting per akun TIDAK tersedia (sistem hanya mencatat jumlah post & likes yang tertangkap scraping, bukan profil akun).
Jangan mengarang angka untuk dua hal di atas jika ditanya user.
"""

        system_instruction = (
            f"{DEFAULT_SYSTEM_PROMPT}\n\n"
            f"Berikut data hasil scraping terkini yang relevan dengan topik '{matched_topic}':\n"
            f"{context_text}\n"
            f"Gunakan data faktual di atas untuk menjawab pertanyaan tim marketing secara mendalam, lengkap, dan sertakan insight atau ide taktis."
        )

        # Format 9router endpoint URL (ensure /v1/chat/completions)
        base = (self.base_url or os.getenv("OPENAI_API_BASE_URL") or "https://router9-9router-bba7ab-157-10-252-77.sslip.io").rstrip("/")
        if not base.endswith("/v1"):
            base += "/v1"
        endpoint_url = f"{base}/chat/completions"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        target_model = self.model
        if not target_model or target_model.lower() in ("vision", "claude-3-5-sonnet-20241022", "default"):
            target_model = "Thinking"

        # Include conversation history so follow-up questions work
        messages = [{"role": "system", "content": system_instruction}]
        for m in history:
            role = m.get("role")
            content = m.get("content")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": str(content)})
        messages.append({"role": "user", "content": user_message})

        return endpoint_url, headers, target_model, messages, matched_topic, topic_data

    def _call_openai_router(
        self,
        db: Database,
        user_message: str,
        history: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Calls 9router / OpenAI-compatible endpoint with enriched database context (buffered, non-streaming)."""
        endpoint_url, headers, target_model, messages, matched_topic, topic_data = self._build_router_context(
            db, user_message, history
        )
        payload = {
            "model": target_model,
            "stream": True,
            "messages": messages,
            "reasoning_effort": "high",
        }

        full_content = ""
        reasoning = ""
        with httpx.Client(timeout=60.0) as client:
            with client.stream("POST", endpoint_url, headers=headers, json=payload) as resp:
                if resp.status_code != 200:
                    raise RuntimeError(f"9router HTTP {resp.status_code}")
                for line in resp.iter_lines():
                    if line.startswith("data: "):
                        raw = line[6:].strip()
                        if raw == "[DONE]":
                            break
                        try:
                            chunk = json.loads(raw)
                            choices = chunk.get("choices", [])
                            if choices:
                                delta = choices[0].get("delta", {})
                                if "content" in delta and delta["content"]:
                                    full_content += delta["content"]
                                if "reasoning_content" in delta and delta["reasoning_content"]:
                                    reasoning += delta["reasoning_content"]
                        except Exception:
                            pass

        final_reply = full_content.strip() or reasoning.strip()
        if not final_reply:
            return self._local_fallback_handler(db, user_message)

        return {
            "status": "success",
            "user_query": user_message,
            "model": target_model,
            "tool_used": "research_topic",
            "tool_results": [{"tool": "research_topic", "topic": matched_topic, "data": topic_data}],
            "reply": final_reply,
        }

    def stream_router_chat(self, db: Database, user_message: str, history: List[Dict[str, Any]]):
        """
        Generator that relays live SSE chunks from 9router as they arrive, so the client
        (Open WebUI) can render the typing/thinking animation in real time.
        Yields dicts: {"type": "reasoning"|"content", "text": str} or {"type": "error", "text": str}.
        """
        matched_topic = self._resolve_matched_topic(db, user_message, history)
        freshness_status = self._ensure_topic_freshness(db, matched_topic)
        if freshness_status:
            yield {"type": "reasoning", "text": freshness_status}

        endpoint_url, headers, target_model, messages, matched_topic, topic_data = self._build_router_context(
            db, user_message, history, matched_topic=matched_topic
        )
        payload = {
            "model": target_model,
            "stream": True,
            "messages": messages,
            "reasoning_effort": "high",
        }

        got_any_output = False
        try:
            with httpx.Client(timeout=90.0) as client:
                with client.stream("POST", endpoint_url, headers=headers, json=payload) as resp:
                    if resp.status_code != 200:
                        raise RuntimeError(f"9router HTTP {resp.status_code}")
                    for line in resp.iter_lines():
                        if not line.startswith("data: "):
                            continue
                        raw = line[6:].strip()
                        if raw == "[DONE]":
                            break
                        try:
                            chunk = json.loads(raw)
                        except Exception:
                            continue
                        choices = chunk.get("choices", [])
                        if not choices:
                            continue
                        delta = choices[0].get("delta", {})
                        if delta.get("reasoning_content"):
                            got_any_output = True
                            yield {"type": "reasoning", "text": delta["reasoning_content"]}
                        if delta.get("content"):
                            got_any_output = True
                            yield {"type": "content", "text": delta["content"]}
        except Exception as exc:
            logger.error(f"Error streaming from 9router: {exc}")
            fallback = self._local_fallback_handler(db, user_message)
            yield {"type": "content", "text": fallback.get("reply", "")}
            return

        if not got_any_output:
            fallback = self._local_fallback_handler(db, user_message)
            yield {"type": "content", "text": fallback.get("reply", "")}


    def _claude_tool_use_loop(
        self,
        db: Database,
        user_message: str,
        history: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Multi-turn tool-use loop with Claude API."""
        messages = list(history)
        messages.append({"role": "user", "content": user_message})

        tools_used = []
        tool_results_data = []

        response = self.client.messages.create(
            model=self.model,
            max_tokens=1500,
            system=DEFAULT_SYSTEM_PROMPT,
            messages=messages,
            tools=CLAUDE_TOOLS_SPEC,
        )

        if response.stop_reason == "tool_use":
            tool_calls = [block for block in response.content if block.type == "tool_use"]
            tool_result_contents = []

            for tool_call in tool_calls:
                tool_name = tool_call.name
                tool_input = tool_call.input
                tools_used.append(tool_name)

                logger.info(f"Claude invoking tool: {tool_name} with input: {tool_input}")
                tool_output = execute_claude_tool(db, tool_name, tool_input)
                tool_results_data.append({
                    "tool": tool_name,
                    "input": tool_input,
                    "output": tool_output,
                })

                tool_result_contents.append({
                    "type": "tool_result",
                    "tool_use_id": tool_call.id,
                    "content": json.dumps(tool_output, ensure_ascii=False),
                })

            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_result_contents})

            final_response = self.client.messages.create(
                model=self.model,
                max_tokens=1500,
                system=DEFAULT_SYSTEM_PROMPT,
                messages=messages,
            )

            final_text = ""
            for block in final_response.content:
                if block.type == "text":
                    final_text += block.text

            return {
                "status": "success",
                "user_query": user_message,
                "model": self.model,
                "tool_used": tools_used[0] if tools_used else None,
                "tools_used": tools_used,
                "tool_results": tool_results_data,
                "reply": final_text,
            }

        else:
            final_text = ""
            for block in response.content:
                if block.type == "text":
                    final_text += block.text

            return {
                "status": "success",
                "user_query": user_message,
                "model": self.model,
                "tool_used": None,
                "tools_used": [],
                "tool_results": [],
                "reply": final_text,
            }

    def _local_fallback_handler(self, db: Database, message: str) -> Dict[str, Any]:
        """Local topic and keyword intent handler when AI router is not reachable."""
        msg_lower = message.lower()
        accounts = db.list_accounts()

        # 1. Compare Topics Intent
        if any(w in msg_lower for w in ["bandingkan topik", "bandingkan kata kunci", "compare topik", "vs", "versus"]):
            found_topics = [t for t in self._resolve_topics(db) if t in msg_lower]
            if len(found_topics) < 2:
                found_topics = ["pendirian pt", "virtual office", "konsultasi pajak"]
            tool_res = execute_claude_tool(db, "compare_topics", {"keywords": found_topics})
            leaderboard = tool_res.get("leaderboard", [])
            top_t = leaderboard[0]["keyword"] if leaderboard else "N/A"
            reply = (
                f"Hasil riset perbandingan topik konten:\n"
                f"- Topik dengan antusiasme & likes tertinggi: **'{top_t}'**.\n"
                f"- Peringkat topik:\n" +
                "\n".join([f"  {r['rank']}. **{r['keyword']}** — rata-rata {r['avg_likes']:,} likes, {r['avg_views']:,} views" for r in leaderboard]) +
                f"\n\nRekomendasi: Fokuskan pilar konten utama pada topik peringkat teratas untuk memaksimalkan jangkauan organik."
            )
            return {
                "status": "success",
                "user_query": message,
                "tool_used": "compare_topics",
                "tools_used": ["compare_topics"],
                "tool_results": [{"tool": "compare_topics", "input": {"keywords": found_topics}, "output": tool_res}],
                "reply": reply,
            }

        # 2. Viral Content / Idea Inspiration Intent
        if any(w in msg_lower for w in ["viral", "tertinggi", "terbanyak", "contoh", "ide konten", "hook"]):
            kw = None
            for t in self._resolve_topics(db):
                if t in msg_lower:
                    kw = t
                    break
            if not kw:
                words = [w for w in re.findall(r"\b[a-zA-Z0-9_]+\b", msg_lower) if len(w) > 2 and w not in STOP_WORDS]
                kw = words[0] if words else "pendirian pt"

            tool_res = execute_claude_tool(db, "find_viral_content", {"keyword": kw, "limit": 3})
            posts = tool_res.get("viral_posts", [])
            if posts:
                post_bullets = []
                for p in posts:
                    views_txt = f"{p['views']:,} views" if p.get("views") is not None else "Photo post"
                    post_bullets.append(
                        f"- [{p['platform'].upper()}] @{p['username']}: \"{p['caption'][:100]}...\" (👍 {p['likes']:,} likes, 👁️ {views_txt})"
                    )
                bullet_str = "\n".join(post_bullets)
                reply = (
                    f"Berikut referensi konten paling viral untuk topik **'{kw}'**:\n\n"
                    f"{bullet_str}\n\n"
                    f"**Pola Keberhasilan**: Konten yang mendapatkan interaksi tinggi umumnya menggunakan hook masalah nyata (misal: 'Jangan sampai salah izin OSS', 'Biaya bikin PT vs CV') dan menyajikan solusi langkah demi langkah."
                )
            else:
                reply = f"Belum ditemukan postingan viral untuk topik '{kw}'. Jalankan scraper kata kunci terlebih dahulu."
            return {
                "status": "success",
                "user_query": message,
                "tool_used": "find_viral_content",
                "tools_used": ["find_viral_content"],
                "tool_results": [{"tool": "find_viral_content", "input": {"keyword": kw}, "output": tool_res}],
                "reply": reply,
            }

        # 3. Research Specific Topic Intent (Check known topics first)
        for t in self._resolve_topics(db):
            if t in msg_lower:
                tool_res = execute_claude_tool(db, "research_topic", {"keyword": t})
                total_p = tool_res.get("total_posts", 0)
                avg_l = tool_res.get("avg_likes", 0)
                max_l = tool_res.get("max_likes", 0)
                avg_v = tool_res.get("avg_views", 0)
                er = tool_res.get("engagement_rate", 0)
                reply = (
                    f"📊 **Hasil Riset Topik: '{t}'** di Media Sosial:\n"
                    f"- Total postingan termonitor: **{total_p} post**\n"
                    f"- Rata-rata likes per post: **{avg_l:,} likes**\n"
                    f"- Puncak likes tertinggi: **{max_l:,} likes**\n"
                    f"- Rata-rata views (TikTok/Reels): **{avg_v:,} views**\n"
                    f"- Engagement rate rata-rata: **{er}%**\n\n"
                    f"💡 **Insight Riset**: Topik '{t}' memiliki interaksi yang kuat di media sosial. Di TikTok & Reels, video edukasi 30-60 detik berfokus pada solusi praktis menghasilkan interaksi di atas rata-rata."
                )
                return {
                    "status": "success",
                    "user_query": message,
                    "tool_used": "research_topic",
                    "tools_used": ["research_topic"],
                    "tool_results": [{"tool": "research_topic", "input": {"keyword": t}, "output": tool_res}],
                    "reply": reply,
                }

        # 4. Fallback Account Summary
        matched_acc = None
        for a in accounts:
            if a.username.lower() in msg_lower:
                matched_acc = a
                break

        if matched_acc:
            tool_res = execute_claude_tool(db, "get_engagement_summary", {"username": matched_acc.username, "platform": matched_acc.platform})
            s = tool_res.get("summary", {})
            reply = (
                f"Ringkasan akun @{matched_acc.username} ({matched_acc.platform}):\n"
                f"- Total postingan: {s.get('total_posts', 0)}\n"
                f"- Rata-rata likes: {s.get('avg_likes', 0):,}\n"
                f"- Rata-rata views: {s.get('avg_views', 0):,}\n"
                f"- Engagement rate: {s.get('engagement_rate', 0)}%"
            )
            return {
                "status": "success",
                "user_query": message,
                "tool_used": "get_engagement_summary",
                "tools_used": ["get_engagement_summary"],
                "tool_results": [{"tool": "get_engagement_summary", "input": {"username": matched_acc.username, "platform": matched_acc.platform}, "output": tool_res}],
                "reply": reply,
            }

        # 5. Extract topic keywords skipping stop words
        words = [w for w in re.findall(r"\b[a-zA-Z0-9_]+\b", msg_lower) if len(w) > 2 and w not in STOP_WORDS]
        kw = words[0] if words else "pendirian PT"
        tool_res = execute_claude_tool(db, "research_topic", {"keyword": kw})
        reply = (
            f"📊 **Hasil Riset Kata Kunci: '{kw}'**:\n"
            f"- Total postingan: **{tool_res.get('total_posts', 0)} post**\n"
            f"- Rata-rata likes: **{tool_res.get('avg_likes', 0):,} likes**\n"
            f"- Puncak likes: **{tool_res.get('max_likes', 0):,} likes**\n"
            f"- Rata-rata views: **{tool_res.get('avg_views', 0):,} views**\n"
            f"- Engagement rate: **{tool_res.get('engagement_rate', 0)}%**\n\n"
            f"💡 **Rekomendasi Konten**: Gunakan kata kunci '{kw}' pada baris pertama caption dan hook video 3 detik awal untuk meningkatkan retensi penonton."
        )
        return {
            "status": "success",
            "user_query": message,
            "tool_used": "research_topic",
            "tools_used": ["research_topic"],
            "tool_results": [{"tool": "research_topic", "input": {"keyword": kw}, "output": tool_res}],
            "reply": reply,
        }
