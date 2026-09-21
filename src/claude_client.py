from __future__ import annotations

import json
import logging
import os
import re
import threading
from queue import Queue
from typing import Any, Callable, Dict, List, Optional, Tuple

import anthropic
import httpx
from .db import Database
from .tools import CLAUDE_TOOLS_SPEC, execute_claude_tool
from .chat_actions import is_competitor_analysis_intent, resolve_account_reference
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
7. JANGAN PERNAH mengklaim suatu aksi backend terjadi (scraping berhasil, jumlah postingan baru ditarik, sistem berhasil mengambil data, dll) kecuali itu eksplisit tertulis di blok [AKSI YANG BARU DIJALANKAN OLEH SISTEM] atau [DATA FAKTUAL ...] di bawah. Kalau blok itu tidak ada atau tidak menyebut aksi tersebut, berarti aksi itu TIDAK terjadi — katakan itu terus terang, jangan mengarang narasi keberhasilan/kegagalan yang tidak didukung data yang diberikan.
8. User sedang mengobrol dengan Anda di halaman chat terpisah (bukan di halaman dashboard grafik) — sidebar chat sudah punya tombol "Kembali ke Dashboard" untuk kembali melihat visualisasi. Kalau jawaban Anda sangat berkaitan dengan tren visual/grafik, cukup sebutkan singkat sekali "lihat juga grafiknya di dashboard" tanpa menempelkan URL lengkap berulang di setiap jawaban.
[FRAMEWORK STRATEGI KONTEN — terapkan aktif saat memberi rekomendasi, bukan cuma teori]
Sumber: Marketing Skills for AI Agents (coreyhaines31/marketingskills, skill "social").

A. Hook Formula — pakai untuk menyusun kalimat pembuka caption/Reels/TikTok:
   - Curiosity: "Ternyata [asumsi umum] itu salah." / "[Hasil] — padahal cuma butuh [waktu singkat]."
   - Story: "Minggu lalu klien kami [kejadian tak terduga]..." / "Dulu [kondisi awal], sekarang [kondisi sekarang]."
   - Value: "Cara [hasil diinginkan] tanpa [rasa sakit umum]:" / "[Angka] hal yang bikin [hasil]:"
   - Contrarian: "Kebanyakan orang salah soal [topik]. Ini alasannya:" / "Berhenti [kesalahan umum]. Lakukan ini:"

B. Content Pillars — kelompokkan rekomendasi konten ke pilar, jangan random:
   Insight industri (regulasi/perpajakan terbaru) · Edukasi (how-to, framework legal/pajak) ·
   Behind-the-scenes (proses kerja EasyLegal/EasyTax/EasyOffice) · Studi kasus klien ·
   Promosi layanan (porsi kecil, jangan dominan).

C. Platform notes (Instagram & TikTok, sesuai cakupan data sistem ini):
   - Instagram: Reels untuk jangkauan baru, carousel untuk edukasi mendalam, idealnya 1-2 post/hari.
   - TikTok: video pendek native (bukan re-upload Reels polos), hook 2 detik pertama krusial.

D. Metrik yang benar-benar berarti (gunakan istilah ini saat menjelaskan performa):
   Awareness: reach, pertumbuhan follower. Engagement: engagement rate, comments (lebih bernilai dari likes), shares/saves.
   Ingatkan bila reach/impression/saves tidak tersedia di data sistem (lihat batasan data), jangan mengarang.

Saat user minta "ide konten" atau "rekomendasi", gunakan data faktual scraping (post dengan performa terbaik) sebagai bukti, lalu petakan ke satu formula hook + satu pilar konten yang relevan — bukan saran generik tanpa dasar data.
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
    "halo", "hai", "tes", "test", "ya", "kan", "nih",
    "coba", "cek", "lihat", "tampilkan", "kasih", "kirim", "kirimkan",
    "berikan", "update", "sekarang", "dulu", "nanti", "tadi", "oke",
    "ok", "baik", "silakan", "tarik", "ambil", "gitu", "gini", "yuk",
    "ayo", "please", "plis", "coy", "bro", "gan", "min", "kak",
}


class ClaudeChatHandler:
    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None, base_url: Optional[str] = None):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self.model = model or os.getenv("CLAUDE_MODEL", "Thinking")
        self.base_url = base_url or os.getenv("ANTHROPIC_BASE_URL") or os.getenv("OPENAI_API_BASE_URL")

        # Explicit provider routing: CHAT_PROVIDER_MODE ("anthropic" | "openai_compatible") wins
        # when set. Unset/invalid falls back to inferring from the key prefix and base_url, which
        # preserves zero-config behavior for existing deployments that never set the env var.
        configured_mode = (os.getenv("CHAT_PROVIDER_MODE") or "").strip().lower()
        if configured_mode in ("anthropic", "openai_compatible"):
            self.provider_mode = configured_mode
        else:
            is_openai_compatible = bool(self.api_key) and (
                not self.api_key.startswith("sk-ant-")
                or (self.base_url and "anthropic.com" not in self.base_url)
            )
            self.provider_mode = "openai_compatible" if is_openai_compatible else "anthropic"

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

        Before any research/answer path runs, translates the message into typed chat
        actions (scrape a profile, start/stop monitoring, replace a monitored account,
        compare profiles) via `chat_actions.ChatActionOrchestrator` and executes them
        against Bright Data/the fallback scrapers, so commands like "ganti akun EasyLegal jadi
        @id.easylegal" actually mutate monitoring and scrape — not just answer as if
        they were a topic-research question.
        """
        if not message or not message.strip():
            return {"status": "error", "message": "Pesan chat tidak boleh kosong"}

        history = conversation_history or []
        is_openai_router = self.provider_mode == "openai_compatible"
        action_result = self._run_chat_actions(db, message, history, is_openai_router)
        if action_result.clarification:
            return {
                "status": "success",
                "user_query": message,
                "tool_used": None,
                "tools_used": [],
                "tool_results": [],
                "action_receipts": [],
                "reply": action_result.clarification,
            }

        if is_competitor_analysis_intent(message):
            result = self._build_competitor_analysis_fallback_result(db, message, history)
            return self._merge_action_context(result, action_result, already_grounded=True)

        if not self.api_key:
            logger.info("No AI API key configured. Running local intent fallback handler.")
            return self._merge_action_context(self._local_fallback_handler(db, message), action_result)

        # Route 1: 9router or OpenAI-compatible router
        if is_openai_router:
            try:
                result = self._call_openai_router(db, message, history, action_result=action_result)
                return self._merge_action_context(result, action_result, already_grounded=True)
            except Exception as exc:
                logger.error(f"Error calling 9router/OpenAI gateway: {exc}. Falling back to local handler.")
                fallback = self._local_fallback_handler(db, message)
                fallback["warning"] = f"AI Router notice: {str(exc)} (Menampilkan hasil dari query database internal)."
                return self._merge_action_context(fallback, action_result)

        # Route 2: Native Anthropic Claude API
        if self.client:
            try:
                result = self._claude_tool_use_loop(db, message, history, action_result=action_result)
                return self._merge_action_context(result, action_result, already_grounded=True)
            except Exception as exc:
                logger.error(f"Error calling Claude API: {exc}. Falling back to local handler.")
                fallback = self._local_fallback_handler(db, message)
                fallback["warning"] = f"Claude API notice: {str(exc)} (Menampilkan hasil dari query database internal)."
                return self._merge_action_context(fallback, action_result)

        return self._merge_action_context(self._local_fallback_handler(db, message), action_result)
    def _run_chat_actions(
        self,
        db: Database,
        message: str,
        history: List[Dict[str, Any]],
        is_openai_router: bool,
        progress_callback: Optional[Callable[[str], None]] = None,
    ):
        """Runs the deterministic parser first, then (only for messages that show some
        sign of action intent it didn't already resolve) consults the AI planner via the
        router. Returns an `ActionExecutionResult` — empty when nothing action-like was
        found, so ordinary research questions are entirely unaffected.

        Action execution (provider calls, DB mutations) is best-effort: any failure here
        must degrade to "no action taken", never crash the primary chat/research path.
        A bug in one action executor should not take down basic research questions.
        """
        from .chat_actions import ChatActionOrchestrator, ActionExecutionResult
        try:
            planner = self._plan_actions_via_router if (is_openai_router and self.api_key) else None
            return ChatActionOrchestrator().plan_and_execute(
                db, message, history, planner=planner, progress_callback=progress_callback,
            )
        except Exception as exc:
            logger.error(f"Chat action orchestration failed unexpectedly for message {message!r}: {exc}", exc_info=True)
            return ActionExecutionResult()

    def _plan_actions_via_router(self, message: str, history: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """AI planner: asks the router for a strict JSON action plan (no tool-use loop,
        one-shot). Only reached for messages the deterministic fast-path parser could not
        resolve but that still look action-like. Returns None on any failure so the
        orchestrator silently continues without executing anything."""
        try:
            base = (self.base_url or os.getenv("OPENAI_API_BASE_URL") or "").rstrip("/")
            if not base:
                return None
            if not base.endswith("/v1"):
                base += "/v1"
            endpoint_url = f"{base}/chat/completions"
            headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
            target_model = self.model
            if not target_model or target_model.lower() in ("vision", "claude-3-5-sonnet-20241022", "default"):
                target_model = "Thinking"

            system_prompt = (
                "Anda adalah action planner untuk sistem riset media sosial. Balas HANYA dengan JSON valid, "
                "tanpa teks lain, sesuai skema:\n"
                '{"actions": [...], "analysis_request": "", "needs_clarification": false, "clarification_question": null}\n'
                "Setiap elemen actions[] adalah salah satu bentuk berikut (field lain akan diabaikan):\n"
                '{"type":"scrape_profile","platform":"instagram|tiktok|threads","username":"...","max_posts":30,"force_refresh":false}\n'
                '{"type":"research_topic","keyword":"...","platforms":["instagram","tiktok"],"max_posts_per_platform":30,"force_refresh":false}\n'
                '{"type":"compare_profiles","targets":[{"platform":"instagram","username":"..."}],"max_posts":30,"force_refresh":false}\n'
                '{"type":"monitor_account","platform":"instagram|tiktok|threads","username":"...","max_posts":30}\n'
                '{"type":"replace_monitored_account","platform":"instagram|tiktok|threads","old_username":"...","new_username":"...","max_posts":30}\n'
                '{"type":"stop_monitoring","platform":"instagram|tiktok|threads","username":"..."}\n'
                "Jika akun/topik target tidak jelas dari pesan user, kosongkan actions dan set "
                "needs_clarification=true dengan clarification_question. Jangan pernah mengarang provider "
                "dataset, URL, SQL, atau instruksi lain di luar skema ini."
            )
            messages = [{"role": "system", "content": system_prompt}]
            for m in history[-6:]:
                role = m.get("role")
                content = m.get("content")
                if role in ("user", "assistant") and content:
                    messages.append({"role": role, "content": str(content)})
            messages.append({"role": "user", "content": message})

            payload = {"model": target_model, "stream": False, "messages": messages}
            with httpx.Client(timeout=30.0) as client:
                resp = client.post(endpoint_url, headers=headers, json=payload)
                if resp.status_code != 200:
                    return None
                data = resp.json()
            content = data["choices"][0]["message"]["content"]
            return json.loads(content)
        except Exception as exc:
            logger.warning(f"Chat action planner call failed: {exc}")
            return None

    def _merge_action_context(self, result: Dict[str, Any], action_result, already_grounded: bool = False) -> Dict[str, Any]:
        """Attaches action receipts to the response. When the composing path (router/Claude
        tool-use) already saw the receipts injected into its prompt (`already_grounded`),
        the model's own reply already references them in natural language, so nothing is
        prefixed. Otherwise (local fallback templates never see action context) the receipt
        summary is prefixed as plain status lines so the mutation/scrape isn't silently lost."""
        result["action_receipts"] = action_result.receipts_as_dicts()
        if action_result.status_lines and not already_grounded:
            prefix = "\n".join(f"- {line}" for line in action_result.status_lines)
            existing_reply = result.get("reply", "")
            result["reply"] = f"{prefix}\n\n{existing_reply}" if existing_reply else prefix
        return result

    @staticmethod
    def _engagement_label_value(data: Dict[str, Any]) -> Tuple[str, str]:
        """Honest engagement label+value for display: a true percentage (label
        "Engagement rate") when `engagement_rate` was computed from a views denominator,
        otherwise the `engagements_per_post` fallback — a real per-post interaction count,
        labeled accordingly and NEVER suffixed with "%" since it is not a rate."""
        rate = data.get("engagement_rate")
        if rate is not None:
            return "Engagement rate", f"{rate}%"
        per_post = data.get("engagements_per_post", 0)
        return "Rata-rata interaksi per post (views tidak tersedia)", f"{per_post}"

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

        is_openai_router = self.provider_mode == "openai_compatible"
        from .chat_actions import has_action_intent
        if has_action_intent(message):
            events: Queue[Tuple[str, Any]] = Queue()

            def run_actions() -> None:
                events.put(("progress", "Menyiapkan proses scraping dan memeriksa data akun…"))
                result = self._run_chat_actions(
                    db, message, history, is_openai_router,
                    progress_callback=lambda text: events.put(("progress", text)),
                )
                events.put(("result", result))

            worker = threading.Thread(target=run_actions, name="chat-scrape-action", daemon=True)
            worker.start()
            while True:
                event_type, payload = events.get()
                if event_type == "progress":
                    yield {"type": "reasoning", "text": f"🔄 {payload}\n"}
                    continue
                action_result = payload
                worker.join(timeout=0.1)
                break
        else:
            action_result = self._run_chat_actions(db, message, history, is_openai_router)

        if action_result.clarification:
            yield {"type": "content", "text": action_result.clarification}
            return
        if action_result.status_lines:
            status_text = "\n".join(f"🔧 {line}" for line in action_result.status_lines) + "\n\n"
            yield {"type": "reasoning", "text": status_text}

        if not self.api_key:
            fallback = self._merge_action_context(self._local_fallback_handler(db, message), action_result)
            yield {"type": "content", "text": fallback.get("reply", "")}
            return

        if is_openai_router:
            yield from self.stream_router_chat(db, message, history, action_result=action_result)
            return

        if self.client:
            try:
                result = self._claude_tool_use_loop(db, message, history, action_result=action_result)
                result = self._merge_action_context(result, action_result, already_grounded=True)
                yield {"type": "content", "text": result.get("reply", "")}
            except Exception as exc:
                logger.error(f"Error calling Claude API: {exc}. Falling back to local handler.")
                fallback = self._merge_action_context(self._local_fallback_handler(db, message), action_result)
                yield {"type": "content", "text": fallback.get("reply", "")}
            return

        fallback = self._merge_action_context(self._local_fallback_handler(db, message), action_result)
        yield {"type": "content", "text": fallback.get("reply", "")}

    def _resolve_matched_topic(
        self,
        db: Database,
        user_message: str,
        history: List[Dict[str, Any]],
    ) -> Tuple[str, bool]:
        """Matches the user's message (or recent history, for follow-ups) to a known/likely
        topic keyword. Returns `(topic, is_confident)`: `is_confident` is True only when the
        topic came from an already-known topic (registered in `topics` or the static seed
        list), or from the AI's own reading of the prompt confirming genuine topic-research
        intent — never from blindly guessing an arbitrary leftover word out of the sentence.
        Callers must gate live-scrape-on-chat (which registers a new topic and spends
        Bright Data quota) on `is_confident`, so filler/test prompts like "coba" never get
        treated as a real research topic.
        """
        msg_lower = user_message.lower()
        matched_topic = None
        for t in self._resolve_topics(db):
            if t in msg_lower:
                matched_topic = t
                break
        # Look back in history if this is a follow-up query like "gimana list nya" or "di akun mana saja"
        if not matched_topic and history:
            for past_msg in reversed(history):
                past_content = str(past_msg.get("content", "")).lower()
                for t in self._resolve_topics(db):
                    if t in past_content:
                        matched_topic = t
                        break
                if matched_topic:
                    break

        if matched_topic:
            return matched_topic, True

        # No known/seeded topic substring matched the message. Before ever treating this
        # as a topic worth spending Bright Data quota on, have the AI actually read and
        # understand the prompt first: is this genuine topic-research intent, and if so
        # what's the real topic? Only a positive, understood answer counts as confident —
        # an unreachable/unconfigured AI or an unclear/filler prompt never does.
        ai_topic = self._classify_topic_intent(user_message, history)
        if ai_topic:
            return ai_topic, True

        words = [w for w in re.findall(r"\b[a-zA-Z0-9_]+\b", msg_lower) if len(w) > 2 and w not in STOP_WORDS]
        return (words[0] if words else "pendirian PT"), False

    def _classify_topic_intent(self, message: str, history: List[Dict[str, Any]]) -> Optional[str]:
        """Asks the AI to read the prompt and decide whether it expresses genuine intent to
        research a social media content topic/keyword (vs. a filler/test message, greeting,
        or unrelated question). Returns the concise topic keyword on a confident positive
        read, else None. Never raises — an unreachable router, malformed response, or
        unclear prompt all resolve to None so the caller falls back to the non-scraping
        path instead of guessing.
        """
        try:
            base = (self.base_url or os.getenv("OPENAI_API_BASE_URL") or "").rstrip("/")
            if not self.api_key or not base:
                return None
            if not base.endswith("/v1"):
                base += "/v1"
            endpoint_url = f"{base}/chat/completions"
            headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
            target_model = self.model
            if not target_model or target_model.lower() in ("vision", "claude-3-5-sonnet-20241022", "default"):
                target_model = "Thinking"

            system_prompt = (
                "Baca pesan user dan tentukan apakah pesan ini benar-benar berisi permintaan riset "
                "topik/kata kunci konten media sosial (Instagram/TikTok) — misalnya soal jasa hukum, "
                "pajak, perizinan, atau topik konten lain yang ingin dianalisis. Pesan basa-basi, sapaan, "
                "kalimat uji coba/testing ('coba', 'tes', 'cek dulu'), atau pertanyaan yang tidak jelas "
                "maksud topiknya BUKAN permintaan riset topik. Balas HANYA JSON valid tanpa teks lain:\n"
                '{"is_topic_research": true|false, "topic": "kata kunci topik singkat atau null"}\n'
                'Isi "topic" hanya jika is_topic_research true, dengan kata kunci topik yang ringkas '
                "(bukan kalimat penuh, bukan filler word).\n"
                "Riwayat percakapan boleh dipakai sebagai konteks, tapi keputusan HARUS berdasar pesan "
                "TERAKHIR user."
            )
            messages = [{"role": "system", "content": system_prompt}]
            for m in history[-6:]:
                role = m.get("role")
                content = m.get("content")
                if role in ("user", "assistant") and content:
                    messages.append({"role": role, "content": str(content)})
            messages.append({"role": "user", "content": message})

            payload = {"model": target_model, "stream": False, "messages": messages}
            with httpx.Client(timeout=15.0) as client:
                resp = client.post(endpoint_url, headers=headers, json=payload)
                if resp.status_code != 200:
                    return None
                data = resp.json()
            content = data["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            if not isinstance(parsed, dict) or not parsed.get("is_topic_research"):
                return None
            topic = str(parsed.get("topic") or "").strip().lower()
            return topic or None
        except Exception as exc:
            logger.warning(f"Topic intent classification call failed: {exc}")
            return None

    def _resolve_conversation_account(
        self,
        db: Database,
        user_message: str,
        history: List[Dict[str, Any]],
    ) -> Optional[Tuple[str, str]]:
        """Returns the most recently referenced stored account for account follow-ups."""
        current = resolve_account_reference(db, user_message)
        if current is not None:
            return current
        for past_message in reversed(history[-8:]):
            account_ref = resolve_account_reference(db, str(past_message.get("content", "")))
            if account_ref is not None:
                return account_ref
        return None

    def _ensure_topic_freshness(self, db: Database, matched_topic: str) -> Optional[str]:
        """
        Live-scrapes the topic before answering if we have no data yet, or the newest data is
        older than TOPIC_STALENESS_HOURS (default 6h). Runs synchronously (blocks the chat
        response) — this trades response latency for data freshness, and consumes scraper
        quota (Bright Data credit, or risks free-tier IP/account rate limiting) on every genuinely
        new or stale topic a user asks about. Disable via ENABLE_LIVE_SCRAPE_ON_CHAT=false.
        Returns a short human-readable status string if a scrape ran, else None.
        """
        if os.getenv("ENABLE_LIVE_SCRAPE_ON_CHAT", "true").lower() in ("false", "0", "no"):
            return None

        try:
            staleness_hours = float(os.getenv("TOPIC_STALENESS_HOURS", "24"))
        except ValueError:
            staleness_hours = 24.0

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
            from datetime import datetime, timezone
            from .scrapers.keyword_scraper import scrape_topic_content
            max_posts = int(os.getenv("MAX_POSTS_PER_SCRAPE", "30"))
            logger.info(f"Live scrape-on-chat: fetching fresh data for topic '{matched_topic}'")
            since = last_scraped or None
            result = scrape_topic_content(
                db,
                matched_topic,
                max_posts_per_platform=max_posts,
                since=since,
            )
            added = result.get("total_posts_added", 0)
            return (
                f"🔍 Mengambil data terbaru untuk topik \"{matched_topic}\" dari Instagram & TikTok "
                f"({added} postingan baru ditemukan)...\n\n"
            )
        except Exception as exc:
            logger.warning(f"Live scrape-on-chat failed for topic '{matched_topic}': {exc}")
            return None

    def _build_account_context_text(self, db: Database, platform: str, username: str) -> Tuple[str, Dict[str, Any]]:
        """Builds factual context grounded on a SPECIFIC account's full post history —
        no keyword/topic filtering. Used when chat_actions resolved a specific profile
        (scrape_profile/monitor_account/replace_monitored_account), so a message like
        "riset soal akun instagram id.easylegal" answers from all of that account's
        scraped posts instead of being misrouted through topic-keyword matching (which
        previously extracted a stray word like "soal" from the sentence and silently
        filtered the account's own posts down to whichever ones happened to contain it).
        Returns (context_text, summary_dict) — summary_dict is {} when the account has
        no data yet.
        """
        acc = db.get_account_by_username(platform, username)
        if not acc:
            text = f"[DATA FAKTUAL AKUN]:\nAkun @{username} [{platform.upper()}] belum memiliki data tersimpan di sistem.\n"
            return text, {}

        summary = db.get_account_summary(acc.id) or {}
        last_scraped_at = db.get_account_freshness(acc.id)
        top_posts = db.query_posts(account_id=acc.id, order_by="likes", limit=8)
        recent_posts = db.query_posts(account_id=acc.id, order_by="posted_at", limit=5)
        follower_line = (
            f"Follower Count: {acc.follower_count:,}\n" if acc.follower_count is not None
            else "Follower Count: tidak tersedia (belum tertangkap saat scraping terakhir)\n"
        )
        text = f"""
[DATA FAKTUAL HASIL SCRAPING MEDIA SOSIAL — PROFIL AKUN @{acc.username}]:
Platform: {acc.platform.upper()}
{follower_line}Total Postingan Tersimpan (SEMUA post akun ini, TANPA filter kata kunci apa pun): {summary.get('total_posts', 0)} post
Waktu Sinkronisasi Data Terakhir: {last_scraped_at or 'belum tersedia'}
Tanggal Post Terbaru Tersimpan: {summary.get('latest_post') or 'belum tersedia'}
Rata-Rata Likes per Post: {summary.get('avg_likes', 0):,} likes
Rata-Rata Comments per Post: {summary.get('avg_comments', 0):,} comments
Rata-Rata Views: {summary.get('avg_views', 0):,} views
Postingan dengan Likes Tertinggi:
"""
        if top_posts:
            for idx, p in enumerate(top_posts, 1):
                content_label = (p.get("content_type") or "post").upper()
                v_txt = f"{p['views']:,} views" if p.get("views") is not None else "views tidak tersedia"
                link_txt = f", Link: {p['post_url']}" if p.get("post_url") else ""
                text += f"{idx}. [{content_label}] \"{p['caption'][:140]}...\" (Likes: {p['likes']:,}, Views: {v_txt}, Diposting: {p['posted_at']}{link_txt})\n"
        else:
            text += "(Belum ada postingan tersimpan untuk akun ini.)\n"

        text += "\nPostingan Terbaru:\n"
        if recent_posts:
            for idx, p in enumerate(recent_posts, 1):
                content_label = (p.get("content_type") or "post").upper()
                v_txt = f"{p['views']:,} views" if p.get("views") is not None else "views tidak tersedia"
                link_txt = f", Link: {p['post_url']}" if p.get("post_url") else ""
                text += f"{idx}. [{content_label}] \"{p['caption'][:140]}...\" (Likes: {p['likes']:,}, Views: {v_txt}, Diposting: {p['posted_at']}{link_txt})\n"
        else:
            text += "(Belum ada postingan tersimpan untuk akun ini.)\n"

        return text, summary

    def _build_multi_account_context_text(
        self, db: Database, account_refs: List[Tuple[str, str]],
    ) -> Tuple[str, str]:
        """Builds concatenated factual context for 2+ SPECIFIC accounts — used for
        compare_profiles, so a "bandingkan @a vs @b" request is grounded on each
        account's own full post history instead of just the pass/fail scrape receipt
        (which has no likes/comments/views data at all). Returns (context_text,
        subject_label)."""
        parts = []
        usernames = []
        for platform, username in account_refs:
            text, _ = self._build_account_context_text(db, platform, username)
            parts.append(text)
            usernames.append(f"@{username}")
        context_text = "\n".join(parts)
        subject_label = "perbandingan akun " + " vs ".join(usernames)
        return context_text, subject_label

    def _build_competitor_analysis_context_text(
        self,
        db: Database,
        user_message: str,
        history: List[Dict[str, Any]],
    ) -> Tuple[str, str, Dict[str, Any]]:
        """Builds factual competitor comparison evidence and explicit ATM instructions."""
        msg_lower = user_message.lower()
        metric = "engagement"
        if re.search(r"\b(views?|tayangan|jangkauan)\b", msg_lower):
            metric = "views"
        elif re.search(r"\blikes?\b", msg_lower):
            metric = "likes"
        elif re.search(r"\bkomentar\b", msg_lower):
            metric = "comments"

        platform = None
        if "instagram" in msg_lower or re.search(r"\big\b", msg_lower):
            platform = "instagram"
        elif "tiktok" in msg_lower:
            platform = "tiktok"
        elif "threads" in msg_lower:
            platform = "threads"

        days = 3650
        if "hari ini" in msg_lower:
            days = 1
        elif "kemarin" in msg_lower:
            days = 2
        elif re.search(r"\bminggu\b", msg_lower):
            days = 7
        elif re.search(r"\bbulan\b", msg_lower):
            days = 30

        baseline_username = None
        account_ref = self._resolve_conversation_account(db, user_message, history)
        if account_ref:
            baseline_username = account_ref[1]
        else:
            brand_aliases = {
                "easylegal": "id.easylegal",
                "easytax": "id.easytax",
                "easyoffice": "id.easyoffice",
            }
            for brand, username in brand_aliases.items():
                if brand in msg_lower:
                    baseline_username = username
                    break

        from .analytics import get_competitor_analysis_dataset
        data = get_competitor_analysis_dataset(
            db,
            baseline_username=baseline_username,
            platform=platform,
            metric=metric,
            days=days,
            limit=12,
        )
        baseline = data["baseline"]
        summary = baseline["summary"]
        subject_label = (
            f"komparasi kompetitor terhadap @{baseline_username}"
            if baseline_username
            else "komparasi kompetitor terhadap seluruh brand EasyCorp"
        )
        text = f"""
[DATA FAKTUAL KOMPARASI KOMPETITOR + ATM]:
Baseline: @{baseline['username']}
Platform: {data['platform']}
Periode: {data['period_days']} hari terakhir
Metrik ranking yang diminta: {data['metric']}

Ringkasan Baseline:
- Total post: {summary['post_count']}
- Total views: {summary['total_views']}
- Total likes: {summary['total_likes']}
- Total komentar: {summary['total_comments']}
- Post terbaru: {summary['latest_post'] or 'tidak tersedia'}

Peringkat Akun Kompetitor:
"""
        if data["competitor_accounts"]:
            for idx, account in enumerate(data["competitor_accounts"], 1):
                text += (
                    f"{idx}. @{account['username']} [{account['platform']}]: "
                    f"{account['post_count']} post, {account['total_views']} views, "
                    f"{account['total_likes']} likes, {account['total_comments']} komentar\n"
                )
        else:
            text += "(Tidak ada akun kompetitor yang cocok dengan filter.)\n"

        text += "\nPostingan Kompetitor Terbaik Berdasarkan Metrik Permintaan:\n"
        if data["top_competitor_posts"]:
            for idx, post in enumerate(data["top_competitor_posts"], 1):
                views = post["views"] if post["views"] is not None else "tidak tersedia"
                link = post["post_url"] or "tidak tersedia"
                text += (
                    f"{idx}. @{post['username']} [{post['platform']}], diposting {post['posted_at']}: "
                    f"\"{post['caption'][:240]}\" | Views: {views}, Likes: {post['likes']}, "
                    f"Komentar: {post['comments']}, Link: {link}\n"
                )
        else:
            text += "(Tidak ada postingan kompetitor yang cocok dengan filter.)\n"

        text += "\nPostingan Baseline Terbaik untuk Pembanding:\n"
        if baseline["top_posts"]:
            for idx, post in enumerate(baseline["top_posts"], 1):
                views = post["views"] if post["views"] is not None else "tidak tersedia"
                link = post["post_url"] or "tidak tersedia"
                text += (
                    f"{idx}. @{post['username']} [{post['platform']}], diposting {post['posted_at']}: "
                    f"\"{post['caption'][:200]}\" | Views: {views}, Likes: {post['likes']}, "
                    f"Komentar: {post['comments']}, Link: {link}\n"
                )
        else:
            text += "(Tidak ada postingan baseline yang cocok dengan filter.)\n"

        text += """
[INSTRUKSI JAWABAN KOMPARASI + ATM]:
- Mulai dengan komparasi angka baseline vs kompetitor dari data di atas.
- Pilih postingan kompetitor yang paling relevan berdasarkan metrik permintaan user.
- Buat bagian AMATI: pola hook, format, topik, struktur caption, dan CTA yang terlihat dari bukti.
- Buat bagian TIRU: prinsip yang layak diadopsi, bukan menyalin caption atau identitas kompetitor.
- Buat bagian MODIFIKASI: minimal 3 konsep yang disesuaikan untuk baseline, masing-masing dengan hook baru.
- Sertakan akun, angka, tanggal, dan link yang tersedia. Jangan mengarang link atau metrik kosong.
- Jika daftar kompetitor di atas berisi data, jangan pernah mengatakan data kompetitor belum tersedia.
"""
        return text, subject_label, data

    def _build_competitor_analysis_fallback_result(
        self,
        db: Database,
        user_message: str,
        history: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Returns a factual ATM answer when the configured AI router is unavailable."""
        _, _, data = self._build_competitor_analysis_context_text(db, user_message, history)
        baseline = data["baseline"]
        summary = baseline["summary"]
        metric = data["metric"]
        metric_labels = {
            "views": "views",
            "likes": "likes",
            "comments": "komentar",
            "engagement": "likes + komentar",
        }

        comparison_lines = [
            f"- Baseline **@{baseline['username']}**: {summary['post_count']} post, "
            f"{summary['total_views']:,} views, {summary['total_likes']:,} likes, "
            f"{summary['total_comments']:,} komentar."
        ]
        for account in data["competitor_accounts"][:5]:
            comparison_lines.append(
                f"- **@{account['username']}** ({account['platform']}): "
                f"{account['post_count']} post, {account['total_views']:,} views, "
                f"{account['total_likes']:,} likes, {account['total_comments']:,} komentar."
            )

        posts = data["top_competitor_posts"][:3]
        if not posts:
            reply = (
                "**KOMPARASI**\n"
                + "\n".join(comparison_lines)
                + "\n\nBelum ada postingan kompetitor yang cocok dengan platform dan periode "
                  "permintaan. Analisis ATM tidak dibuat agar caption, metrik, atau tautan tidak "
                  "direkayasa."
            )
        else:
            metric_note = ""
            if metric == "views" and not any(post["views"] is not None for post in posts):
                metric_note = (
                    "\n- Data views kompetitor belum tersedia. Urutan contoh di bawah memakai "
                    "likes + komentar sebagai pembeda sekunder, bukan bukti views terbesar."
                )

            observed_lines = []
            for index, post in enumerate(posts, 1):
                views = f"{post['views']:,}" if post["views"] is not None else "tidak tersedia"
                link = f"\n  Link: {post['post_url']}" if post["post_url"] else "\n  Link: tidak tersedia"
                caption = post["caption"].strip() or "(caption kosong)"
                observed_lines.append(
                    f"{index}. **@{post['username']}** — \"{caption[:220]}\"\n"
                    f"   {post['posted_at']} · views {views} · {post['likes']:,} likes · "
                    f"{post['comments']:,} komentar{link}"
                )

            focus_by_brand = {
                "id.easylegal": "legalitas dan kepatuhan bisnis",
                "id.easytax": "pajak dan kepatuhan fiskal",
                "id.easyoffice": "ruang kerja dan operasional bisnis",
            }
            focus = focus_by_brand.get(baseline["username"], "solusi bisnis EasyCorp")
            reply = (
                f"**KOMPARASI** — diurutkan berdasarkan {metric_labels[metric]}\n"
                + "\n".join(comparison_lines)
                + metric_note
                + "\n\n**AMATI**\n"
                + "\n".join(observed_lines)
                + "\n\n**TIRU**\n"
                  "- Adopsi prinsip hook masalah sebelum solusi; jangan menyalin caption atau "
                  "identitas visual kompetitor.\n"
                  "- Pertahankan satu masalah utama per post, bukti yang mudah dipindai, lalu CTA "
                  "yang spesifik.\n"
                  "- Uji format dan struktur dari contoh teratas terhadap baseline; angka di atas "
                  "adalah hasil tersimpan, bukan jaminan performa berikutnya.\n\n"
                  f"**MODIFIKASI untuk @{baseline['username']}**\n"
                  f"1. Hook: “Sebelum mengurus {focus}, cek 3 risiko ini.” Format: carousel "
                  "checklist; CTA: simpan sebagai panduan.\n"
                  f"2. Hook: “Kesalahan terkait {focus} yang terlihat sepele tetapi paling sering "
                  "menghambat bisnis.” Format: video singkat masalah → dampak → solusi.\n"
                  f"3. Hook: “Sudah yakin {focus} untuk bisnis Anda aman?” Format: audit mandiri "
                  "3 pertanyaan; CTA: komentari bagian yang paling membingungkan."
            )

        return {
            "status": "success",
            "user_query": user_message,
            "tool_used": "competitor_analysis",
            "tools_used": ["competitor_analysis"],
            "tool_results": [{
                "tool": "competitor_analysis",
                "input": {
                    "baseline_username": baseline["username"],
                    "platform": data["platform"],
                    "metric": metric,
                    "period_days": data["period_days"],
                },
                "output": data,
            }],
            "reply": reply,
        }

    def _build_router_context(
        self,
        db: Database,
        user_message: str,
        history: List[Dict[str, Any]],
        matched_topic: Optional[str] = None,
        action_context_text: str = "",
        account_ref: Optional[Tuple[str, str]] = None,
        account_refs: Optional[List[Tuple[str, str]]] = None,
    ):
        """
        Resolves topic (unless already provided by the caller), live-scrapes it if stale,
        pulls factual DB context, and builds the router request payload.

        When `account_refs` has 2+ entries (a chat_actions compare_profiles action),
        context is grounded on EACH account's full post history concatenated together —
        without this, a compare request only sees pass/fail scrape receipts with no
        actual likes/comments/views to compare.

        When `account_ref` is set (a chat_actions profile-level action resolved a
        single specific account), context is grounded on that account's full post history
        instead — topic-keyword resolution never runs, so an unrelated word extracted
        from the sentence can't silently filter the account's own data.
        """
        if is_competitor_analysis_intent(user_message):
            context_text, subject_label, subject_data = self._build_competitor_analysis_context_text(
                db, user_message, history,
            )
            matched_topic = None
        elif account_refs and len(account_refs) >= 2:
            context_text, subject_label = self._build_multi_account_context_text(db, account_refs)
            subject_data = {
                "accounts": [{"platform": p, "username": u} for p, u in account_refs],
            }
        else:
            if account_ref is None:
                # Explicit topics in the new message take precedence. Otherwise preserve the
                # most recent account subject for follow-ups such as "apa aja konten terbaru
                # nya" instead of extracting a filler word ("aja") as a new keyword.
                has_explicit_topic = any(topic in user_message.lower() for topic in self._resolve_topics(db))
                if not has_explicit_topic:
                    account_ref = self._resolve_conversation_account(db, user_message, history)
            if account_ref is not None:
                platform, username = account_ref
                context_text, account_summary = self._build_account_context_text(db, platform, username)
                subject_data = {"platform": platform, "username": username, **account_summary}
                subject_label = f"akun @{username}"
            else:
                if matched_topic is None:
                    matched_topic, topic_is_confident = self._resolve_matched_topic(db, user_message, history)
                    if topic_is_confident:
                        self._ensure_topic_freshness(db, matched_topic)

                # Pull real data from database for this topic to inject as factual context
                topic_data = db.get_topic_summary(matched_topic)
                viral_posts = db.query_posts(topic=matched_topic, order_by="likes", limit=8)
                ig_summary = db.get_topic_summary(matched_topic, platform="instagram")
                tt_summary = db.get_topic_summary(matched_topic, platform="tiktok")
                account_breakdown = db.get_topic_account_breakdown(matched_topic, limit=8)
                er_label, er_value = self._engagement_label_value(topic_data)

                context_text = f"""
[DATA FAKTUAL HASIL SCRAPING MEDIA SOSIAL]:
Topik / Kata Kunci: '{matched_topic}'
Total Postingan Termonitor (semua platform): {topic_data.get('total_posts', 0)} post
Rata-Rata Likes per Post: {topic_data.get('avg_likes', 0):,} likes
Puncak Likes Tertinggi: {topic_data.get('max_likes', 0):,} likes
Rata-Rata Views (Video TikTok/Reels): {topic_data.get('avg_views', 0):,} views
{er_label}: {er_value}

Breakdown per Platform:
- Instagram: {ig_summary.get('total_posts', 0)} post, rata-rata {ig_summary.get('avg_likes', 0):,} likes
- TikTok: {tt_summary.get('total_posts', 0)} post, rata-rata {tt_summary.get('avg_likes', 0):,} likes, rata-rata {tt_summary.get('avg_views', 0):,} views

Daftar Postingan Viral Terkait (Gunakan data akun dan metrik berikut jika user bertanya akun mana atau minta daftar postingan):
"""
                for idx, p in enumerate(viral_posts, 1):
                    content_label = (p.get("content_type") or "post").upper()
                    v_txt = f"{p['views']:,} views" if p.get("views") is not None else "views tidak tersedia"
                    link_txt = f", Link: {p['post_url']}" if p.get("post_url") else ""
                    context_text += f"{idx}. Akun @{p['username']} [{p['platform'].upper()} · {content_label}]: \"{p['caption'][:120]}...\" (Likes: {p['likes']:,}, Views: {v_txt}{link_txt})\n"

                context_text += "\nAkun Paling Aktif Membahas Topik Ini (jumlah post & rata-rata likes yang tertangkap scraping):\n"
                if account_breakdown:
                    for idx, a in enumerate(account_breakdown, 1):
                        context_text += (
                            f"{idx}. @{a['username']} [{a['platform'].upper()}]: {a['post_count']} post, "
                            f"rata-rata {a['avg_likes']:,} likes, likes tertinggi {a['max_likes']:,}\n"
                        )
                else:
                    context_text += "(Belum ada akun yang tertangkap scraping untuk topik ini.)\n"

                subject_data = topic_data
                subject_label = f"topik '{matched_topic}'"

        if action_context_text:
            context_text += f"\n{action_context_text}"

        context_text += """
[KETERBATASAN DATA SAAT INI]:
- Reach/impression spesifik (data private Insights milik pemilik akun) TIDAK tersedia — hanya bisa didapat lewat Meta/TikTok Business API resmi milik pemilik akun, bukan lewat scraping publik manapun.
- Follower count dan URL/permalink post TERSEDIA untuk data yang di-scrape ulang setelah update sistem ini; data lama yang sudah tersimpan sebelum update mungkin masih kosong pada kedua field tersebut sampai akun tersebut di-scrape ulang.
Jangan mengarang angka untuk hal-hal di atas jika ditanya user.
"""

        system_instruction = (
            f"{DEFAULT_SYSTEM_PROMPT}\n\n"
            f"SUBJEK AKTIF PERCAKAPAN: {subject_label}. Pertahankan subjek ini untuk pertanyaan lanjutan "
            f"dan jangan mengubah kata pengisi seperti 'aja', 'nya', 'terbaru', atau 'gimana' menjadi topik baru.\n\n"
            f"Berikut data hasil scraping terkini yang relevan dengan {subject_label}:\n"
            f"{context_text}\n"
            f"Gunakan SEMUA data faktual di atas untuk menjawab pertanyaan tim marketing secara mendalam dan lengkap — "
            f"jangan mempersempit jawaban ke sebagian kecil data kecuali user secara eksplisit meminta kata kunci/topik tertentu. "
            f"Bedakan waktu sinkronisasi data dari tanggal posting: sinkronisasi yang berhasil tidak berarti akun menerbitkan post baru pada hari itu. "
            f"Jika tidak ada post dalam rentang tanggal yang diminta, katakan hanya bahwa tidak ada post pada rentang tersebut, "
            f"lalu sebutkan tanggal post terbaru dan waktu sinkronisasi terakhir; jangan menyatakan akun belum terdaftar atau belum di-scrape bila konteks menunjukkan data tersimpan. "
            f"Sertakan insight atau ide taktis."
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

        return endpoint_url, headers, target_model, messages, matched_topic, subject_data

    def _call_openai_router(
        self,
        db: Database,
        user_message: str,
        history: List[Dict[str, Any]],
        action_result=None,
    ) -> Dict[str, Any]:
        """Calls 9router / OpenAI-compatible endpoint with enriched database context (buffered, non-streaming)."""
        matched_topic = action_result.matched_topic if action_result else None
        account_ref = action_result.matched_account if action_result else None
        account_refs = action_result.matched_accounts if action_result else None
        action_context_text = action_result.context_text if action_result else ""
        endpoint_url, headers, target_model, messages, matched_topic, topic_data = self._build_router_context(
            db, user_message, history, matched_topic=matched_topic,
            action_context_text=action_context_text, account_ref=account_ref, account_refs=account_refs,
        )
        payload = {
            "model": target_model,
            "stream": True,
            "messages": messages,
            "reasoning_effort": "high",
        }

        full_content = ""
        reasoning = ""
        usage_data: Optional[Dict[str, Any]] = None
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
                            # OpenAI-compatible gateways that report usage attach it to a
                            # chunk (typically the final one, sometimes with empty choices) —
                            # capture it if present rather than fabricating an estimate.
                            if isinstance(chunk.get("usage"), dict):
                                usage_data = chunk["usage"]
                        except Exception:
                            pass

        # Some router responses leak an empty/stray <think>...</think> marker into the
        # content delta instead of routing it through reasoning_content — strip it so it
        # never surfaces as literal text in the chat UI.
        visible_content = re.sub(r"<think>.*?</think>", "", full_content, flags=re.DOTALL).strip()
        final_reply = visible_content or full_content.strip() or reasoning.strip()
        if not final_reply:
            return self._local_fallback_handler(db, user_message)
        is_competitor_analysis = (
            isinstance(topic_data, dict)
            and topic_data.get("analysis_type") == "competitor_atm"
        )
        tool_name = "competitor_analysis" if is_competitor_analysis else "research_topic"
        return {
            "status": "success",
            "user_query": user_message,
            "model": target_model,
            "tool_used": tool_name,
            "tool_results": [{
                "tool": tool_name,
                "topic": matched_topic,
                "data": topic_data,
            }],
            "reply": final_reply,
            "usage": usage_data,
        }

    def stream_router_chat(self, db: Database, user_message: str, history: List[Dict[str, Any]], action_result=None):
        """
        Generator that relays live SSE chunks from 9router as they arrive, so the client
        (the dashboard's embedded "Tanya AI" panel, or any OpenAI-compatible client)
        can render the typing/thinking animation in real time.
        Yields dicts: {"type": "reasoning"|"content", "text": str} or {"type": "error", "text": str}.
        """
        if is_competitor_analysis_intent(user_message):
            result = self._build_competitor_analysis_fallback_result(db, user_message, history)
            yield {"type": "content", "text": result["reply"]}
            return

        matched_topic = action_result.matched_topic if action_result else None
        account_ref = action_result.matched_account if action_result else None
        account_refs = action_result.matched_accounts if action_result else None
        action_context_text = action_result.context_text if action_result else ""
        if matched_topic is None and account_ref is None and not account_refs:
            matched_topic, topic_is_confident = self._resolve_matched_topic(db, user_message, history)
            freshness_status = self._ensure_topic_freshness(db, matched_topic) if topic_is_confident else None
            if freshness_status:
                yield {"type": "reasoning", "text": freshness_status}

        endpoint_url, headers, target_model, messages, matched_topic, topic_data = self._build_router_context(
            db, user_message, history, matched_topic=matched_topic,
            action_context_text=action_context_text, account_ref=account_ref, account_refs=account_refs,
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


    @staticmethod
    def _sum_anthropic_usage(*responses) -> Dict[str, int]:
        """Real token usage from one or more Anthropic Messages API responses, summed
        across turns (e.g. the tool-use loop's initial + final call), in OpenAI-compatible
        shape. Never estimated — every value comes straight from the provider response."""
        prompt_tokens = sum(getattr(r.usage, "input_tokens", 0) or 0 for r in responses)
        completion_tokens = sum(getattr(r.usage, "output_tokens", 0) or 0 for r in responses)
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }

    def _claude_tool_use_loop(
        self,
        db: Database,
        user_message: str,
        history: List[Dict[str, Any]],
        action_result=None,
    ) -> Dict[str, Any]:
        """Multi-turn tool-use loop with Claude API."""
        # Competitor-analysis questions get the same deterministic, grounded template as
        # the router (`_call_openai_router`) and streaming router (`stream_router_chat`)
        # paths, so the answer quality/format doesn't depend on which provider is active.
        if is_competitor_analysis_intent(user_message):
            return self._build_competitor_analysis_fallback_result(db, user_message, history)

        messages = list(history)
        messages.append({"role": "user", "content": user_message})

        tools_used = []
        tool_results_data = []

        system_prompt = DEFAULT_SYSTEM_PROMPT
        extra_context = ""
        if action_result:
            if action_result.matched_accounts and len(action_result.matched_accounts) >= 2:
                multi_text, _ = self._build_multi_account_context_text(db, action_result.matched_accounts)
                extra_context += f"\n\n{multi_text}"
            if action_result.context_text:
                extra_context += f"\n\n{action_result.context_text}"
        if extra_context:
            system_prompt = f"{DEFAULT_SYSTEM_PROMPT}{extra_context}"

        request_kwargs = {
            "model": self.model,
            "max_tokens": 1500,
            "system": system_prompt,
            "messages": messages,
            "tools": CLAUDE_TOOLS_SPEC,
        }
        response = self.client.messages.create(**request_kwargs)

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
                system=system_prompt,
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
                "usage": self._sum_anthropic_usage(response, final_response),
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
                "usage": self._sum_anthropic_usage(response),
            }

    def _local_fallback_handler(self, db: Database, message: str) -> Dict[str, Any]:
        """Local topic and keyword intent handler when AI router is not reachable."""
        msg_lower = message.lower()
        accounts = db.list_accounts()

        # 1. Compare Topics Intent
        if is_competitor_analysis_intent(message):
            return self._build_competitor_analysis_fallback_result(db, message, [])

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
                    content_label = (p.get("content_type") or "post").upper()
                    views_txt = f"{p['views']:,} views" if p.get("views") is not None else "views tidak tersedia"
                    post_bullets.append(
                        f"- [{p['platform'].upper()} · {content_label}] @{p['username']}: \"{p['caption'][:100]}...\" (👍 {p['likes']:,} likes, 👁️ {views_txt})"
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
                er_label, er_value = self._engagement_label_value(tool_res)
                reply = (
                    f"📊 **Hasil Riset Topik: '{t}'** di Media Sosial:\n"
                    f"- Total postingan termonitor: **{total_p} post**\n"
                    f"- Rata-rata likes per post: **{avg_l:,} likes**\n"
                    f"- Puncak likes tertinggi: **{max_l:,} likes**\n"
                    f"- Rata-rata views (TikTok/Reels): **{avg_v:,} views**\n"
                    f"- {er_label}: **{er_value}**\n\n"
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
            er_label, er_value = self._engagement_label_value(s)
            reply = (
                f"Ringkasan akun @{matched_acc.username} ({matched_acc.platform}):\n"
                f"- Total postingan: {s.get('total_posts', 0)}\n"
                f"- Rata-rata likes: {s.get('avg_likes', 0):,}\n"
                f"- Rata-rata views: {s.get('avg_views', 0):,}\n"
                f"- {er_label}: {er_value}"
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
        er_label, er_value = self._engagement_label_value(tool_res)
        reply = (
            f"📊 **Hasil Riset Kata Kunci: '{kw}'**:\n"
            f"- Total postingan: **{tool_res.get('total_posts', 0)} post**\n"
            f"- Rata-rata likes: **{tool_res.get('avg_likes', 0):,} likes**\n"
            f"- Puncak likes: **{tool_res.get('max_likes', 0):,} likes**\n"
            f"- Rata-rata views: **{tool_res.get('avg_views', 0):,} views**\n"
            f"- {er_label}: **{er_value}**\n\n"
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
