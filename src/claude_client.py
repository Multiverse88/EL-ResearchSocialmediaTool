from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

import anthropic
from .db import Database
from .tools import CLAUDE_TOOLS_SPEC, execute_claude_tool

logger = logging.getLogger("backend.claude")

DEFAULT_SYSTEM_PROMPT = """
Anda adalah AI Social Media Content & Topic Researcher untuk EasyCorp (EasyLegal, EasyTax, EasyOffice).
Fokus utama Anda adalah melakukan riset topik dan kata kunci konten media sosial (Instagram & TikTok), menganalisis tren performa, menemukan konten viral, dan merekomendasikan ide konten berkinerja tinggi bagi tim marketing.

Panduan:
1. SELALU panggil tools yang tersedia untuk mengambil data nyata sebelum menjawab:
   - `research_topic`: Gunakan untuk melihat statistik total konten, likes rata-rata, views, dan engagement rate dari suatu kata kunci/topik.
   - `find_viral_content`: Gunakan untuk melihat contoh konten viral dengan interaksi tertinggi sebagai bahan inspirasi hook & caption.
   - `compare_topics`: Gunakan untuk membandingkan potensi minat audiens antar beberapa kata kunci (misal 'pendirian PT' vs 'virtual office').
   - `search_scraped_posts`: Gunakan untuk memfilter postingan spesifik.
2. Jelaskan metrik utama secara objektif dan berbasis angka riil.
3. Berikan rekomendasi taktis (misal: "Topik ini memiliki likes rata-rata X, format video di TikTok memiliki views 4x lebih tinggi, disarankan membuat konten dengan angle tips praktis").
4. Jawab dalam Bahasa Indonesia yang profesional, ramah, dan solutif untuk tim marketing.
"""

KNOWN_TOPICS = [
    "pendirian pt", "izin usaha oss", "konsultasi pajak", "merek dagang hki",
    "kontrak kerja", "perjanjian bisnis", "perizinan", "laporan spt tahunan",
    "sewa virtual office", "npwp badan usaha", "perubahan akta", "legalitas umkm",
    "biaya pembuatan pt", "syarat izin edar bpom", "rekening bank perusahaan",
    "pt pma", "virtual office", "pajak", "hki", "merek", "oss",
]


class ClaudeChatHandler:
    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None, base_url: Optional[str] = None):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self.model = model or os.getenv("CLAUDE_MODEL", "claude-3-5-sonnet-20241022")
        self.base_url = base_url or os.getenv("ANTHROPIC_BASE_URL")
        self.client = (
            anthropic.Anthropic(api_key=self.api_key, base_url=self.base_url)
            if self.api_key
            else None
        )

    def process_chat(
        self,
        db: Database,
        message: str,
        conversation_history: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Processes a marketing topic research query.
        Uses Claude API with tool use if ANTHROPIC_API_KEY is available,
        otherwise uses local intent matching fallback.
        """
        if not message or not message.strip():
            return {"status": "error", "message": "Pesan chat tidak boleh kosong"}

        if not self.client:
            logger.info("ANTHROPIC_API_KEY not set. Running local intent fallback handler.")
            return self._local_fallback_handler(db, message)

        try:
            return self._claude_tool_use_loop(db, message, conversation_history or [])
        except Exception as exc:
            logger.error(f"Error calling Claude API: {exc}. Falling back to local handler.")
            fallback = self._local_fallback_handler(db, message)
            fallback["warning"] = f"Claude API error ({str(exc)}). Menampilkan hasil dari mesin query internal."
            return fallback

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

        # Step 1: Initial call to Claude with tool definitions
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

            # Step 2: Final response from Claude synthesizing tool output
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
        """Local topic and keyword intent handler when Claude API key is not present."""
        msg_lower = message.lower()
        accounts = db.list_accounts()

        # 1. Compare Topics Intent
        if any(w in msg_lower for w in ["bandingkan topik", "bandingkan kata kunci", "compare topik", "vs", "versus"]):
            found_topics = [t for t in KNOWN_TOPICS if t in msg_lower]
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
            for t in KNOWN_TOPICS:
                if t in msg_lower:
                    kw = t
                    break
            if not kw:
                kw = "pendirian pt"
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

        # 3. Research Specific Topic Intent
        for t in KNOWN_TOPICS:
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
                    f"💡 **Insight Riset**: Topik '{t}' memiliki interaksi yang stabil. Di TikTok, format video penjelasan singkat dengan visual akta/dokumen menghasilkan views di atas rata-rata."
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

        # 5. Default keyword search
        stop_words = {"riset", "cari", "topik", "konten", "apa", "yang", "tentang", "bagaimana", "media", "sosial"}
        words = [w for w in msg_lower.split() if len(w) > 2 and w not in stop_words]
        kw = words[0] if words else "pendirian PT"
        tool_res = execute_claude_tool(db, "research_topic", {"keyword": kw})
        reply = (
            f"Hasil riset kata kunci **'{kw}'**:\n"
            f"- Total postingan: {tool_res.get('total_posts', 0)} post\n"
            f"- Rata-rata likes: {tool_res.get('avg_likes', 0):,}\n"
            f"- Rata-rata views: {tool_res.get('avg_views', 0):,}\n"
            f"- Engagement rate: {tool_res.get('engagement_rate', 0)}%"
        )
        return {
            "status": "success",
            "user_query": message,
            "tool_used": "research_topic",
            "tools_used": ["research_topic"],
            "tool_results": [{"tool": "research_topic", "input": {"keyword": kw}, "output": tool_res}],
            "reply": reply,
        }
