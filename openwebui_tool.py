"""
title: EasyCorp Social Media Content & Topic Researcher
author: EasyCorp Engineering
description: Tool untuk riset topik konten, kata kunci viral, analisis tren Instagram & TikTok, dan pengambilan post terbaru per akun langsung dari Open WebUI.
version: 2.1.0
"""

import json
from typing import Optional
import httpx
from pydantic import BaseModel, Field


class Tools:
    class Valves(BaseModel):
        API_BASE_URL: str = Field(
            default="http://api:8000",
            description="URL backend API (default Docker internal: http://api:8000, atau http://localhost:8000)",
        )
        TIMEOUT_SECONDS: int = Field(
            default=20,
            description="Batas waktu request API baca-data (detik)",
        )
        SCRAPE_TIMEOUT_SECONDS: int = Field(
            default=150,
            description="Batas waktu request yang bisa memicu scraping akun baru via Bright Data (detik) — lebih lama karena scraping sinkron bisa butuh waktu.",
        )

    def __init__(self):
        self.valves = self.Valves()

    async def research_topic(self, keyword: str, platform: str = "") -> str:
        """
        Lakukan riset mendalam performa kata kunci atau topik konten tertentu di Instagram & TikTok.
        Mengembalikan total postingan, rata-rata likes, views, engagement rate, dan postingan paling viral.
        :param keyword: Kata kunci atau topik (misal: 'pendirian PT', 'virtual office', 'pajak UMKM', 'izin OSS')
        :param platform: Filter platform ('instagram' atau 'tiktok', opsional)
        :return: JSON statistik performa topik dan contoh postingan viral.
        """
        clean_kw = keyword.strip()
        url = f"{self.valves.API_BASE_URL}/topics/summary?keyword={clean_kw}"
        if platform:
            url += f"&platform={platform.lower().strip()}"
        try:
            async with httpx.AsyncClient(timeout=self.valves.TIMEOUT_SECONDS) as client:
                res = await client.get(url)
                if res.status_code == 200:
                    return json.dumps(res.json(), indent=2, ensure_ascii=False)
                return f"Error: API returned status code {res.status_code}"
        except Exception as e:
            return f"Gagal meriset topik: {str(e)}"

    async def find_viral_content(self, keyword: str, platform: str = "", limit: int = 5) -> str:
        """
        Cari referensi postingan paling viral (likes dan views tertinggi) untuk topik atau kata kunci tertentu.
        Sangat berguna untuk mencari inspirasi hook, caption, dan format konten marketing.
        :param keyword: Kata kunci pencarian (misal: 'pendirian PT', 'virtual office', 'pajak')
        :param platform: Filter platform ('instagram' atau 'tiktok', opsional)
        :param limit: Jumlah postingan viral yang diinginkan (default 5)
        :return: JSON daftar postingan viral dengan caption lengkap, likes, dan views.
        """
        params = {"keyword": keyword.strip(), "order_by": "likes", "limit": limit}
        if platform:
            params["platform"] = platform.lower().strip()

        url = f"{self.valves.API_BASE_URL}/posts"
        try:
            async with httpx.AsyncClient(timeout=self.valves.TIMEOUT_SECONDS) as client:
                res = await client.get(url, params=params)
                if res.status_code == 200:
                    data = res.json()
                    return json.dumps(data.get("data", []), indent=2, ensure_ascii=False)
                return f"Error: API returned status code {res.status_code}"
        except Exception as e:
            return f"Gagal mencari konten viral: {str(e)}"

    async def compare_topics(self, keywords_comma_separated: str) -> str:
        """
        Bandingkan performa engagement dan minat audiens antar beberapa topik/kata kunci konten.
        :param keywords_comma_separated: Daftar kata kunci dipisah koma (contoh: 'pendirian PT, virtual office, pajak')
        :return: JSON peringkat dan perbandingan performa topik.
        """
        keywords = [k.strip() for k in keywords_comma_separated.split(",") if k.strip()]
        if not keywords:
            return "Masukkan setidaknya satu kata kunci untuk dibandingkan."

        url = f"{self.valves.API_BASE_URL}/topics/compare?keywords={','.join(keywords)}"
        try:
            async with httpx.AsyncClient(timeout=self.valves.TIMEOUT_SECONDS) as client:
                res = await client.get(url)
                if res.status_code == 200:
                    return json.dumps(res.json(), indent=2, ensure_ascii=False)
                return f"Error: API returned status code {res.status_code}"
        except Exception as e:
            return f"Gagal membandingkan topik: {str(e)}"

    async def list_monitored_topics(self) -> str:
        """
        Dapatkan daftar semua topik dan kata kunci konten yang sedang diriset oleh sistem.
        :return: JSON daftar topik dan kategori.
        """
        url = f"{self.valves.API_BASE_URL}/topics"
        try:
            async with httpx.AsyncClient(timeout=self.valves.TIMEOUT_SECONDS) as client:
                res = await client.get(url)
                if res.status_code == 200:
                    return json.dumps(res.json().get("data", []), indent=2, ensure_ascii=False)
                return f"Error: API returned status code {res.status_code}"
        except Exception as e:
            return f"Gagal mengambil daftar topik: {str(e)}"

    async def get_account_posts(
        self, username: str, platform: str = "instagram", limit: int = 10, force_refresh: bool = False,
    ) -> str:
        """
        Ambil postingan sebuah akun SPESIFIK secara kronologis (dari yang terbaru) — bukan
        berdasarkan kata kunci/topik. Pakai fungsi ini ketika user minta "N post terakhir akun
        @username" atau "riwayat postingan @username", bukan riset topik/keyword umum. Otomatis
        memicu scraping via Bright Data kalau data akun belum ada atau sudah lebih dari 24 jam
        (mengikuti TOPIC_STALENESS_HOURS backend), lalu mengembalikan post yang benar-benar
        tersimpan di database — bukan angka karangan.
        :param username: Username akun tujuan (dengan/tanpa '@'), misal 'id.easylegal'
        :param platform: 'instagram' atau 'tiktok'
        :param limit: Jumlah post terbaru yang diinginkan (default 10, maksimal 100)
        :param force_refresh: True untuk memaksa scraping ulang meski data masih dalam masa cache
        :return: JSON status scraping dan daftar post terbaru akun tersebut, terurut dari yang paling baru, lengkap dengan caption, likes, comments, views, dan tanggal publikasi.
        """
        clean_username = username.strip().lstrip("@")
        clean_platform = (platform or "instagram").lower().strip()
        if clean_platform not in ("instagram", "tiktok"):
            clean_platform = "instagram"
        safe_limit = max(1, min(int(limit), 100))

        message = f"Cari {safe_limit} post {clean_platform} @{clean_username}"
        if force_refresh:
            message += " sekarang"

        chat_url = f"{self.valves.API_BASE_URL}/chat"
        try:
            async with httpx.AsyncClient(timeout=self.valves.SCRAPE_TIMEOUT_SECONDS) as client:
                res = await client.post(chat_url, json={"message": message})
                if res.status_code != 200:
                    return f"Error: API returned status code {res.status_code} saat memicu scraping akun."
                chat_data = res.json()
        except Exception as e:
            return f"Gagal memicu scraping akun @{clean_username}: {str(e)}"

        posts_url = f"{self.valves.API_BASE_URL}/posts"
        params = {
            "username": clean_username,
            "platform": clean_platform,
            "order_by": "posted_at",
            "limit": safe_limit,
        }
        try:
            async with httpx.AsyncClient(timeout=self.valves.TIMEOUT_SECONDS) as client:
                res = await client.get(posts_url, params=params)
                if res.status_code != 200:
                    return f"Error: API returned status code {res.status_code} saat mengambil post."
                posts_data = res.json().get("data", [])
        except Exception as e:
            return f"Gagal mengambil daftar post @{clean_username}: {str(e)}"

        receipts = chat_data.get("action_receipts") or []
        return json.dumps(
            {
                "scrape_status": receipts[0] if receipts else None,
                "post_count": len(posts_data),
                "posts": posts_data,
            },
            indent=2,
            ensure_ascii=False,
        )
