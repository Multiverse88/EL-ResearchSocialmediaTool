# EasyCorp Social Media Intelligence & Open WebUI AI Chat Panel

Sistem terpadu untuk scraping data publik Instagram & TikTok secara terjadwal, menyimpannya di database, dan mengekspos data tersebut lewat chat panel berbasis AI ([Open WebUI](https://openwebui.com/) + Claude API Tool Use) di dashboard internal.

Memungkinkan tim marketing untuk bertanya dalam bahasa natural (misal: *"Berapa engagement rata-rata akun EasyLegal bulan ini?"* atau *"Bandingkan performa akun EasyLegal vs kompetitor"*) tanpa perlu membuka spreadsheet atau query database manual.

---

## 🚀 Fitur Utama

- **Scraping Terjadwal Multi-Platform** (3 tingkat, otomatis pilih yang tersedia):
  1. **Apify** (jika `APIFY_API_TOKEN` diisi) — paling reliable, pakai proxy residential milik Apify. Gratis ±1.850 post/bulan dari kredit $5 bawaan akun Apify (tanpa kartu kredit).
  2. **Gratis self-hosted**: Instagram via Instaloader (perlu login akun burner untuk hindari rate-limit), TikTok via `TikTokApi` + headless Chromium (Playwright) — tanpa biaya, tanpa batas volume, tapi lebih rentan diblokir/berubah struktur halaman.
  3. **Fallback terakhir**: TikTok raw HTML parsing kalau Playwright/Chromium tidak tersedia.
  - **Scheduler**: Runner siap dipanggil oleh Dokploy Scheduled Jobs (`0 2 * * *`).
- **AI Chat Panel Berbasis Web ([Open WebUI](https://openwebui.com/))**:
  - 100% web-based, diakses langsung via browser tanpa install aplikasi desktop.
  - Didukung oleh model Claude API (`claude-3-5-sonnet`) dengan integrasi native **Tool Use**.
  - Auto-discovery model via endpoint `/v1/models` dan `/v1/chat/completions`.
- **Claude API Tools**:
  - `search_scraped_posts`: Pencarian post berdasarkan kata kunci, tanggal, platform, username.
  - `get_engagement_summary`: Perhitungan likes, comments, views rata-rata & engagement rate.
  - `compare_accounts`: Leaderboard dan perbandingan performa antar akun (brand vs kompetitor).
- **Internal Web Dashboard**:
  - Tampilan web siap pakai untuk kelola akun yang dimonitor, filter data postingan, dan trigger scraping manual.
- **Dokploy Ready (Docker Compose)**:
  - 1 file `docker-compose.yml` yang langsung menjalankan backend API, scrapers, dan Open WebUI dengan persistent volumes.

---

## 🏗️ Arsitektur

```
[Apify / TikTokApi(Playwright) / Instaloader] --(Dokploy Scheduled Jobs)--> [Database (SQLite / Postgres)]
                                                                      │
                                                                      ▼
                                                      [Backend API - FastAPI]
                                                        - REST Endpoints (/accounts, /posts)
                                                        - Claude Tool-Use Handler
                                                        - OpenAI-Compatible Adapter (/v1)
                                                                      │
                                                                      ▼
                                                    [Open WebUI - Web Chat Panel]
                                                    (Akses via browser di Port 3000)
```

---

## 📦 Panduan Deployment Dokploy (via Docker Compose)

Panduan detail tersedia di [DEPLOYMENT_DOKPLOY.md](./DEPLOYMENT_DOKPLOY.md).

### 1. Buat Service di Dokploy
- Buat Project baru di Dokploy.
- Klik **Create Service** dan pilih tipe **Compose**.
- Hubungkan repository ini (`https://github.com/Multiverse88/EL-ResearchSocialmediaTool.git`).

### 2. Konfigurasi Environment Variables (Tab Environment di Dokploy)
```env
ANTHROPIC_API_KEY=sk-ant-api03-xxxxxxx
CLAUDE_MODEL=claude-3-5-sonnet-20241022
DATABASE_PATH=/app/data/social_media.db
MAX_POSTS_PER_SCRAPE=30
```

### 3. Setting Routing / Domain (Tab Domains)
- **Open WebUI (Chat Panel)**: Arahkan ke service `open-webui` port `8080`.
- **Dashboard & API**: Arahkan ke service `api` port `8000`.

### 4. Setup Cron Harian (Tab Scheduled Jobs)
- **Schedule**: `0 2 * * *` (setiap hari jam 02:00 pagi)
- **Command**:
  ```bash
  docker exec easycorp_social_api python -m src.scrapers.runner
  ```

---

## 💻 Local Development

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Jalankan Server FastAPI
```bash
uvicorn src.server:app --host 0.0.0.0 --port 8000 --reload
```
Akses dashboard di `http://localhost:8000` dan Swagger API docs di `http://localhost:8000/docs`.

### 3. Jalankan Scraper Manual
```bash
# Scrape semua akun terdaftar
python -m src.scrapers.runner

# Scrape akun spesifik
python -m src.scrapers.runner --platform instagram --username easylegal_id --limit 20
```

### 4. Jalankan Unit & Integration Tests
```bash
python3 -m unittest discover -s tests -p "test_*.py"
```

---

## 🔌 Dokumentasi REST API

| Method | Endpoint | Keterangan |
|---|---|---|
| `GET` | `/accounts` | Daftar akun yang dimonitor |
| `POST` | `/accounts` | Daftarkan akun baru (`platform`, `username`, `is_own_brand`) |
| `GET` | `/posts` | Query postingan (`keyword`, `platform`, `username`, `from`, `to`, `limit`, `offset`) |
| `GET` | `/posts/summary` | Ringkasan engagement per akun (`account_id` atau `username`) |
| `POST` | `/chat` | Endpoint utama chat dengan Claude tool-use |
| `GET` | `/v1/models` | OpenAI-compatible discovery untuk Open WebUI |
| `POST` | `/v1/chat/completions` | OpenAI-compatible chat untuk Open WebUI |
| `POST` | `/scrape/run` | Trigger proses scraping di background |
| `GET` | `/scrape/logs` | Riwayat log status scraping |

---

## 📄 Lisensi

Internal EasyCorp proprietary tool.
