# EasyCorp Social Media Intelligence & Analytics Dashboard

Sistem terpadu untuk scraping data publik Instagram, TikTok & Threads secara terjadwal, menyimpannya di database, dan mengekspos data tersebut lewat dashboard analitik internal (`/analytics`) beserta halaman chat AI khusus (`/chat`, "Tanya AI", didukung Claude API Tool Use) — satu aplikasi, satu domain.

Memungkinkan tim marketing untuk bertanya dalam bahasa natural (misal: *"Berapa engagement rata-rata akun EasyLegal bulan ini?"* atau *"Bandingkan performa akun EasyLegal vs kompetitor"*) tanpa perlu membuka spreadsheet, query database manual, atau berpindah ke aplikasi lain.

---

## 🚀 Fitur Utama

- **Scraping Terjadwal Multi-Platform** (Instagram, TikTok, Threads):
  1. **Bright Data Scraper APIs** (jika `BRIGHT_DATA_API_TOKEN` diisi) — Instagram Posts/Reels, TikTok Posts, dan Threads Posts memakai dataset terkelola Bright Data. Pencarian topik Instagram memakai SERP API (`BRIGHT_DATA_SERP_ZONE`) untuk menemukan URL post/reel lalu mengambil detailnya lewat dataset.
  2. **Fallback self-hosted** (Instagram & TikTok saja — Threads tidak punya fallback gratis): Instagram via Instaloader (perlu login akun burner untuk mengurangi rate-limit), TikTok via `TikTokApi` + headless Chromium (Playwright).
  3. **Fallback terakhir**: TikTok raw HTML parsing kalau Playwright/Chromium tidak tersedia.
  - **Scheduler**: Runner siap dipanggil oleh Dokploy Scheduled Jobs (`0 2 * * *`).
- **Halaman "Tanya AI" Khusus (`/chat`)**:
  - Halaman chat mandiri bergaya ChatGPT (sidebar riwayat percakapan, bubble pesan, komposer) yang tetap satu aplikasi/domain dengan dashboard — tinggal klik "Kembali ke Dashboard" untuk balik ke grafik.
  - Didukung oleh model Claude API (`claude-3-5-sonnet`) dengan integrasi native **Tool Use**.
  - Endpoint `/v1/models` dan `/v1/chat/completions` tetap tersedia sebagai API OpenAI-compatible generik untuk klien eksternal lain bila dibutuhkan.
- **Live Scrape-on-Chat**: kalau topik yang ditanya belum pernah di-scrape atau datanya sudah lebih tua dari `TOPIC_STALENESS_HOURS` (default 24 jam), sistem otomatis scraping dulu sebelum AI menjawab — jawaban selalu berbasis data terkini, bukan cuma hasil scraping terjadwal semalam. Bisa dimatikan via `ENABLE_LIVE_SCRAPE_ON_CHAT=false`.
- **Chat-to-Scraper Action**: chat bisa langsung dipakai untuk mengelola monitoring dan menjalankan scrape via Bright Data — bukan cuma bertanya. Contoh perintah: *"Cari 20 post terbaru @kompetitor_a"*, *"Mulai monitor @id.easylegal"*, *"Ganti akun EasyLegal jadi @id.easylegal"*, *"Berhenti monitor @legalku"*, *"Bandingkan @id.easylegal dengan @legalku"*. Pesan diterjemahkan lewat parser deterministik untuk perintah eksplisit, dengan AI planner (JSON terstruktur via router) sebagai fallback untuk kalimat yang lebih natural. Lihat `src/chat_actions.py`.
- **Claude API Tools**:
  - `search_scraped_posts`: Pencarian post berdasarkan kata kunci, tanggal, platform, username.
  - `get_engagement_summary`: Perhitungan likes, comments, views rata-rata & engagement rate.
  - `compare_accounts`: Leaderboard dan perbandingan performa antar akun (brand vs kompetitor).
- **Internal Web Dashboard**:
  - Tampilan web siap pakai untuk kelola akun yang dimonitor, filter data postingan, dan trigger scraping manual.
- **Dokploy Ready (Docker Compose)**:
  - 1 file `docker-compose.yml` yang menjalankan backend API + dashboard + chat AI dalam satu container, dengan persistent volume.

---

## 🏗️ Arsitektur

```
[Bright Data / TikTokApi(Playwright) / Instaloader] --(Scheduled Jobs)--> [Database (SQLite / Postgres)]
                                                                      │
                                                                      ▼
                                                      [Backend API - FastAPI]
                                                        - REST Endpoints (/accounts, /posts)
                                                        - Claude Tool-Use Handler
                                                        - OpenAI-Compatible Adapter (/v1)
                                                        - Analytics Dashboard (/analytics) + Tanya AI Chat Page (/chat)
                                                                      │
                                                                      ▼
                                                    [Browser - Dashboard + Chat, 1 Domain]
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
BRIGHT_DATA_API_TOKEN=isi-dengan-token-bright-data
BRIGHT_DATA_SERP_ZONE=nama-zone-serp
CHAT_ACTION_API_KEY=isi-dengan-secret-acak
```
`CHAT_ACTION_API_KEY` membatasi siapa yang boleh memicu Chat-to-Scraper Action (lihat di atas) lewat `/chat` dan `/v1/chat/completions`. Halaman `/chat` membaca nilai ini secara otomatis (di-render server-side ke dalam halaman) sehingga langsung terautentikasi tanpa konfigurasi tambahan.

### 3. Setting Routing / Domain (Tab Domains)
- **Dashboard, Chat AI & API**: Arahkan ke service `api` port `8000` (satu domain untuk semuanya, misal `sosmed.easycorp.id`).

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
| `GET`/`POST` | `/chat` | `GET` menyajikan halaman chat "Tanya AI"; `POST` adalah endpoint chat dengan Claude tool-use |
| `GET` | `/v1/models` | OpenAI-compatible model discovery (generik, untuk klien eksternal) |
| `POST` | `/v1/chat/completions` | OpenAI-compatible chat completion (generik, untuk klien eksternal) |
| `POST` | `/scrape/run` | Trigger proses scraping di background |
| `GET` | `/scrape/logs` | Riwayat log status scraping |

---

## 📄 Lisensi

Internal EasyCorp proprietary tool.
