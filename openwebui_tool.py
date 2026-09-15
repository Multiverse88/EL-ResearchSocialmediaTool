"""
title: EasyCorp Social Media Content & Topic Researcher
author: EasyCorp Engineering
description: Tool untuk riset topik konten, kata kunci viral, dan analisis tren Instagram & TikTok langsung dari Open WebUI.
version: 2.0.0
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
            description="Batas waktu request API (detik)",
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
