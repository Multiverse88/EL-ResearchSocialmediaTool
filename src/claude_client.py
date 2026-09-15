from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

import anthropic
from .db import Database
from .tools import CLAUDE_TOOLS_SPEC, execute_claude_tool

logger = logging.getLogger("backend.claude")

DEFAULT_SYSTEM_PROMPT = """
Anda adalah AI Social Media & Marketing Intelligence Assistant untuk EasyCorp (EasyLegal, EasyTax, EasyOffice).
Tugas Anda adalah menjawab pertanyaan tim marketing mengenai data dan performa akun sosial media (Instagram dan TikTok) brand sendiri maupun kompetitor berdasarkan data yang telah discrape ke dalam database.

Panduan:
1. SELALU panggil tools yang tersedia (search_scraped_posts, get_engagement_summary, compare_accounts) untuk mengambil data nyata sebelum menjawab. Jangan menebak angka atau performa.
2. Jelaskan metrik utama seperti total posts, rata-rata likes, comments, views, dan engagement rate secara objektif dan berbasis fakta.
3. Jawab dalam Bahasa Indonesia yang profesional, ramah, dan solutif untuk tim marketing.
4. Sertakan rekomendasi taktis jika relevan (misalnya konten dengan likes tinggi membahas topik apa).
"""


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
        Processes a marketing query.
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

        # Check if Claude requested tool execution
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
                    "content": json.dumps(tool_output),
                })

            # Append assistant's response (containing tool call blocks) and tool results
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_result_contents})

            # Step 2: Final response from Claude synthesizing the tool results
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
            # Direct text response without tool call
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
        """Local intent parser and tool execution when Claude API key is not present."""
        msg_lower = message.lower()
        accounts = db.list_accounts()

        # Intent: Compare Accounts
        if any(w in msg_lower for w in ["banding", "vs", "versus", "compare", "kompetitor"]):
            matched = [a.username for a in accounts if a.username.lower() in msg_lower]
            if len(matched) < 2 and len(accounts) >= 2:
                matched = [a.username for a in accounts[:3]]
            tool_res = execute_claude_tool(db, "compare_accounts", {"usernames": matched})
            
            top_ranked = tool_res.get("leaderboard_by_avg_likes", [])
            leader_txt = f"@{top_ranked[0]['username']}" if top_ranked else "belum ada data"
            reply = (
                f"Berdasarkan analisis perbandingan akun {', '.join(['@'+u for u in matched])}:\n"
                f"- Akun dengan rata-rata likes tertinggi adalah {leader_txt}.\n"
                f"- Rincian performa lengkap dapat dilihat pada breakdown data di bawah."
            )
            return {
                "status": "success",
                "user_query": message,
                "tool_used": "compare_accounts",
                "tools_used": ["compare_accounts"],
                "tool_results": [{"tool": "compare_accounts", "input": {"usernames": matched}, "output": tool_res}],
                "reply": reply,
            }

        # Intent: Engagement Summary
        if any(w in msg_lower for w in ["engagement", "rata-rata", "average", "summary", "performa", "likes", "komentar"]):
            target_acc = None
            for a in accounts:
                if a.username.lower() in msg_lower:
                    target_acc = a
                    break
            if not target_acc and accounts:
                target_acc = accounts[0]

            if target_acc:
                tool_res = execute_claude_tool(
                    db,
                    "get_engagement_summary",
                    {"username": target_acc.username, "platform": target_acc.platform},
                )
                sum_d = tool_res.get("summary", {})
                reply = (
                    f"Ringkasan performa akun @{target_acc.username} ({target_acc.platform}):\n"
                    f"- Total postingan: {sum_d.get('total_posts', 0)} post\n"
                    f"- Rata-rata likes: {sum_d.get('avg_likes', 0):,}\n"
                    f"- Rata-rata komentar: {sum_d.get('avg_comments', 0):,}\n"
                    f"- Rata-rata views: {sum_d.get('avg_views', 0):,}\n"
                    f"- Engagement rate: {sum_d.get('engagement_rate', 0)}%"
                )
                return {
                    "status": "success",
                    "user_query": message,
                    "tool_used": "get_engagement_summary",
                    "tools_used": ["get_engagement_summary"],
                    "tool_results": [{"tool": "get_engagement_summary", "input": {"username": target_acc.username, "platform": target_acc.platform}, "output": tool_res}],
                    "reply": reply,
                }

        # Intent: Search Posts
        stop_words = {"cari", "post", "posting", "tentang", "yang", "akun", "bulan", "ini", "apakah", "ada", "konten"}
        words = [w for w in msg_lower.split() if len(w) > 2 and w not in stop_words]
        keyword = words[0] if words else None
        
        tool_res = execute_claude_tool(db, "search_scraped_posts", {"keyword": keyword, "limit": 10})
        count = tool_res.get("count", 0)
        kw_txt = f"kata kunci '{keyword}'" if keyword else "semua kriteria"
        reply = f"Ditemukan {count} postingan dengan {kw_txt}. Silakan lihat daftar postingan di bawah untuk detail interaksi dan caption."
        
        return {
            "status": "success",
            "user_query": message,
            "tool_used": "search_scraped_posts",
            "tools_used": ["search_scraped_posts"],
            "tool_results": [{"tool": "search_scraped_posts", "input": {"keyword": keyword}, "output": tool_res}],
            "reply": reply,
        }
