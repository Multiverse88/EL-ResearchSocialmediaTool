"""
title: EasyCorp Social Media Intelligence Tool for Open WebUI
author: EasyCorp Engineering
description: Native Open WebUI Tool to query scraped Instagram & TikTok marketing metrics, calculate engagement summaries, and compare brand vs competitor accounts.
version: 1.0.0
"""

import json
from typing import Optional
import httpx
from pydantic import BaseModel, Field


class Tools:
    class Valves(BaseModel):
        API_BASE_URL: str = Field(
            default="http://api:8000",
            description="Base URL of the Social Media Research Backend API (inside Docker network: http://api:8000, or public URL)",
        )
        TIMEOUT_SECONDS: int = Field(
            default=20,
            description="Timeout for API requests",
        )

    def __init__(self):
        self.valves = self.Valves()

    async def get_monitored_accounts(self) -> str:
        """
        Dapatkan daftar semua akun media sosial yang sedang dimonitor oleh EasyCorp (Instagram & TikTok, brand sendiri & kompetitor).
        :return: JSON daftar akun dengan platform, username, dan status brand.
        """
        url = f"{self.valves.API_BASE_URL}/accounts"
        try:
            async with httpx.AsyncClient(timeout=self.valves.TIMEOUT_SECONDS) as client:
                res = await client.get(url)
                if res.status_code == 200:
                    return json.dumps(res.json().get("data", []), indent=2, ensure_ascii=False)
                return f"Error: API returned status code {res.status_code}"
        except Exception as e:
            return f"Gagal mengambil daftar akun: {str(e)}"

    async def get_engagement_summary(self, username: str, platform: str = "instagram") -> str:
        """
        Ambil ringkasan performa dan engagement suatu akun (total post, total/rata-rata likes, comments, views, dan engagement rate).
        :param username: Username akun tanpa @ (contoh: 'easylegal_id')
        :param platform: Platform media sosial ('instagram' atau 'tiktok')
        :return: JSON statistik ringkasan engagement akun.
        """
        clean_user = username.strip().lstrip("@")
        url = f"{self.valves.API_BASE_URL}/posts/summary?username={clean_user}&platform={platform.lower()}"
        try:
            async with httpx.AsyncClient(timeout=self.valves.TIMEOUT_SECONDS) as client:
                res = await client.get(url)
                if res.status_code == 200:
                    return json.dumps(res.json(), indent=2, ensure_ascii=False)
                elif res.status_code == 404:
                    return f"Akun @{clean_user} di platform {platform} belum terdaftar atau belum memiliki data hasil scraping."
                return f"Error: API returned status code {res.status_code}"
        except Exception as e:
            return f"Gagal mengambil summary engagement: {str(e)}"

    async def search_scraped_posts(
        self,
        keyword: Optional[str] = None,
        platform: Optional[str] = None,
        username: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 15,
    ) -> str:
        """
        Cari postingan media sosial berdasarkan kata kunci caption, username akun, platform, atau rentang tanggal.
        :param keyword: Kata kunci pencarian (mis. 'PT', 'OSS', 'pajak', 'merek')
        :param platform: Filter platform ('instagram' atau 'tiktok', opsional)
        :param username: Filter akun tertentu (opsional)
        :param date_from: Tanggal awal filter ISO format (contoh: '2026-01-01', opsional)
        :param date_to: Tanggal akhir filter ISO format (contoh: '2026-01-31', opsional)
        :param limit: Jumlah maksimal post yang dikembalikan (default 15)
        :return: JSON daftar postingan yang sesuai kriteria pencarian.
        """
        params = {"limit": limit}
        if keyword:
            params["keyword"] = keyword
        if platform:
            params["platform"] = platform.lower()
        if username:
            params["username"] = username.strip().lstrip("@")
        if date_from:
            params["from"] = date_from
        if date_to:
            params["to"] = date_to

        url = f"{self.valves.API_BASE_URL}/posts"
        try:
            async with httpx.AsyncClient(timeout=self.valves.TIMEOUT_SECONDS) as client:
                res = await client.get(url, params=params)
                if res.status_code == 200:
                    data = res.json()
                    posts = data.get("data", [])
                    return json.dumps({
                        "count": len(posts),
                        "posts": [
                            {
                                "platform": p.get("platform"),
                                "username": p.get("username"),
                                "caption": p.get("caption", "")[:120],
                                "likes": p.get("likes"),
                                "comments": p.get("comments"),
                                "views": p.get("views"),
                                "posted_at": p.get("posted_at"),
                            }
                            for p in posts
                        ]
                    }, indent=2, ensure_ascii=False)
                return f"Error: API returned status code {res.status_code}"
        except Exception as e:
            return f"Gagal mencari posts: {str(e)}"

    async def compare_accounts(self, usernames_comma_separated: str) -> str:
        """
        Bandingkan performa engagement antar beberapa akun (mis. brand sendiri vs kompetitor).
        :param usernames_comma_separated: Daftar username dipisah koma (contoh: 'easylegal_id, legalku_official, izinlegal_id')
        :return: JSON perbandingan statistik dan peringkat akun berdasarkan rata-rata likes & engagement rate.
        """
        usernames = [u.strip().lstrip("@") for u in usernames_comma_separated.split(",") if u.strip()]
        if not usernames:
            return "Harap masukkan setidaknya satu username untuk dibandingkan."

        summaries = []
        async with httpx.AsyncClient(timeout=self.valves.TIMEOUT_SECONDS) as client:
            for u in usernames:
                res = await client.get(f"{self.valves.API_BASE_URL}/posts/summary?username={u}")
                if res.status_code == 200:
                    summaries.append(res.json())

        if not summaries:
            return f"Tidak ditemukan data untuk akun: {', '.join(usernames)}"

        return json.dumps({"compared_count": len(summaries), "results": summaries}, indent=2, ensure_ascii=False)
