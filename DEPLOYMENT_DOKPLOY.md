# Panduan Deployment via Docker Compose di Dokploy

Panduan langkah demi langkah untuk deploy sistem **Social Media Scraper + Open WebUI Chat Panel** di Dokploy VPS menggunakan Docker Compose.

---

## 1. Buat Service Baru di Dokploy

1. Buka dashboard **Dokploy** di VPS Anda.
2. Buat Project baru (misalnya: `EasyCorp-SocialMedia`).
3. Di dalam project, klik **Create Service** dan pilih jenis: **Compose**.
4. Beri nama service (misal: `social-intelligence-stack`).

---

## 2. Hubungkan Kode / Repository

Di tab **General** pada service Compose di Dokploy:
- **Source**: Pilih **GitHub** (atau Git Repository Anda).
- **Branch**: Pilih branch yang aktif (misal `main`).
- **Compose Path**: `docker-compose.yml`

*(Jika deploy tanpa git repo, Anda juga bisa memilih tipe **Raw Docker Compose** lalu copy-paste isi file `docker-compose.yml`).*

---

## 3. Konfigurasi Environment Variables

Buka tab **Environment** pada service Compose di Dokploy, lalu masukkan variabel berikut:

```env
# 1. Claude API Key (Wajib agar AI bisa menganalisis data dengan tool-use)
ANTHROPIC_API_KEY=sk-ant-api03-xxxxxxx
CLAUDE_MODEL=claude-3-5-sonnet-20241022

# 2. Path Database
DATABASE_PATH=/app/data/social_media.db

# 3. Bright Data Scraper APIs (direkomendasikan)
# Mendukung multi-token (pisahkan dengan koma: token1,token2) untuk rotasi & failover otomatis
BRIGHT_DATA_API_TOKEN=isi-dengan-token-bright-data
# Wajib untuk pencarian topik Instagram; gunakan nama SERP API zone dari Bright Data (bisa koma: zone1,zone2)
BRIGHT_DATA_SERP_ZONE=nama-zone-serp
# 4. Kredensial fallback self-hosted (opsional; gunakan akun burner jika ada)
INSTAGRAM_USERNAME=
INSTAGRAM_PASSWORD=
TIKTOK_MS_TOKEN=

# 5. Batas Postingan per Akun tiap Run
MAX_POSTS_PER_SCRAPE=30
```

Aktifkan dataset **Instagram Posts**, **Instagram Reels**, **TikTok Posts**, dan **Threads Posts** di
Bright Data. Scrape profil dan topik TikTok/Threads memakai dataset tersebut langsung.
Pencarian topik Instagram memakai SERP API zone untuk menemukan URL post/reel,
kemudian mengambil detail kontennya lewat dataset Instagram.

**Tips Multi-Token**: Jika akun trial sering terkena limit antrian (concurrency limit / 429), Anda
dapat mendaftarkan akun Bright Data kedua dan memasukkan kedua token dipisahkan koma di
`BRIGHT_DATA_API_TOKEN=token1,token2`. Sistem akan otomatis mendistribusikan request secara
bergantian (round-robin) dan langsung berpindah ke token berikutnya jika satu akun mengalami timeout atau limit.
Klik **Save**.

---

## 4. Konfigurasi Domain & Akses Web (Traefik)

Di tab **Domains** pada Dokploy, Anda bisa menambahkan 2 domain terpisah (atau sub-domain):

### Domain 1: Chat Panel untuk Tim Marketing (Open WebUI)
- **Domain**: `chat-marketing.domainanda.com`
- **Service**: `open-webui`
- **Container Port**: `8080` (port internal Open WebUI)
- **Certificate**: Let's Encrypt (HTTPS otomatis aktif)

### Domain 2: Dashboard Internal & Backend API
- **Domain**: `social-api.domainanda.com`
- **Service**: `api`
- **Container Port**: `8000`
- **Certificate**: Let's Encrypt

---

## 5. Deploy Stack

1. Klik tombol **Deploy** di Dokploy.
2. Dokploy akan:
   - Membangun container `api` (FastAPI backend & scrapers).
   - Mengunduh image `open-webui` (`ghcr.io/open-webui/open-webui:main`).
   - Menjalankan healthcheck otomatis hingga seluruh service sehat (`healthy`).

---

## 6. Setup Scheduler Scraping Harian (Dokploy Scheduled Jobs)

Sesuai PRD Section 10, scraping harian dijalankan via fitur **Scheduled Jobs** di Dokploy:

1. Di dashboard Dokploy, buka menu **Scheduled Jobs** (atau tab Scheduled Jobs di service Anda).
2. Klik **Create Scheduled Job**:
   - **Name**: `Daily Social Media Scraper`
   - **Schedule (Cron)**: `0 2 * * *` *(dijalankan setiap hari pukul 02:00 pagi WIB)*
   - **Command**:
     ```bash
     docker exec easycorp_social_api python -m src.scrapers.runner
     ```
3. Klik **Save**.
4. Anda bisa klik **Run Now** untuk melakukan uji coba scraping pertama kali.

---

## 7. Cara Penggunaan untuk Tim Marketing

1. Buka URL Open WebUI di browser: `https://chat-marketing.domainanda.com` (atau `http://<ip-vps>:3000`).
2. Buat akun pertama (user pertama otomatis menjadi Admin Open WebUI).
3. Di dropdown model atas, model **`social-media-claude-agent`** otomatis terpilih.
4. Tim marketing bisa langsung bertanya dalam bahasa natural:
   - *"Berapa rata-rata likes dan engagement rate akun EasyLegal bulan ini?"*
   - *"Bandingkan performa akun easylegal_id vs kompetitor legalku_official"*
   - *"Cari postingan yang membahas tentang izin PT dan OSS"*
   - *"Konten seperti apa yang memiliki likes tertinggi di TikTok EasyLegal?"*
5. Open WebUI akan memanggil backend tool secara otomatis dan menyajikan jawaban analisis lengkap beserta angka faktual.
