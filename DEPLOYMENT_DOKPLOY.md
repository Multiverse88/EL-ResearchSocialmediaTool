# Panduan Deployment via Docker Compose di Dokploy

Panduan langkah demi langkah untuk deploy sistem **Social Media Scraper + Analytics Dashboard + Panel Chat AI Terintegrasi** di Dokploy VPS menggunakan Docker Compose.

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

Di tab **Domains** pada Dokploy, arahkan satu domain ke service `api`:

### Dashboard, Chat AI & Backend API (1 Domain)
- **Domain**: `sosmed.domainanda.com`
- **Service**: `api`
- **Container Port**: `8000`
- **Certificate**: Let's Encrypt (HTTPS otomatis aktif)

Domain ini melayani dashboard analitik (`/analytics`), panel chat "Tanya AI" yang terpasang
langsung di halaman yang sama, dan seluruh REST API backend — tidak ada domain/container
terpisah yang perlu dikonfigurasi lagi.

### ⚠️ Peringatan: Deploy Manual via SSH/Git Bisa Menghapus Label Traefik

Dokploy TIDAK menyimpan label Traefik (`traefik.enable`, `traefik.http.routers.*`, dll.) di
repo git — label-label itu di-generate dari konfigurasi domain yang tersimpan di database
Dokploy sendiri (Postgres, tabel `domain`), lalu disuntikkan langsung ke file
`docker-compose.yml` di server (`/etc/dokploy/compose/<service>/code/docker-compose.yml`)
HANYA saat Anda klik tombol **Deploy** di UI Dokploy (atau memicu deploy lewat API Dokploy).
File itu sengaja TIDAK di-commit ke git — jadi `git status` di server akan selalu menunjukkan
`docker-compose.yml` sebagai "modified" dibanding versi git, dan itu normal.

Jika Anda (atau AI agent) melakukan deploy manual via SSH dengan `git fetch` + `git reset --hard`
langsung di server (bypass Dokploy UI) untuk menarik commit terbaru lebih cepat, **`git reset --hard`
akan menghapus label Traefik yang disuntikkan Dokploy tadi**, karena label itu hanya ada di
working tree, bukan di git history. Container tetap jalan sehat dan bisa diakses langsung lewat
`http://<ip-vps>:8000`, tapi domain custom (mis. `sosmed.domainanda.com`) akan langsung 404
karena Traefik tidak lagi tahu cara route domain tersebut ke container.

**Cara memperbaiki tanpa Dokploy UI**: cukup restore ulang blok `labels:` dan `networks:`
(`dokploy-network`, `default`) pada `docker-compose.yml` di server persis seperti sebelum
di-reset, lalu jalankan ulang `docker compose up -d` (tidak perlu rebuild) supaya container
direcreate dengan label barunya — Traefik akan langsung mendeteksi lewat Docker provider-nya
(watch-mode, tidak perlu restart Traefik). Isi label yang benar bisa dilihat dari record domain
di database Dokploy (`SELECT * FROM domain WHERE host = '...'`, kolom `uniqueConfigKey` dipakai
sebagai suffix nama router) atau dari `docker inspect` container lain yang punya domain serupa.

**Cara paling aman (direkomendasikan)**: setelah deploy manual via SSH, klik tombol **Redeploy**
sekali di UI Dokploy — itu akan meregenerasi ulang `docker-compose.yml` dengan label yang benar
dari database, tanpa perlu rebuild ulang image jika tidak ada perubahan kode. Perubahan manual
lewat SSH TIDAK otomatis ter-detect/di-heal oleh Dokploy sampai deploy berikutnya dipicu — tidak
ada proses reconcile background yang mengembalikan label yang hilang secara otomatis.

---

## 5. Deploy Stack

1. Klik tombol **Deploy** di Dokploy.
2. Dokploy akan:
   - Membangun container `api` (FastAPI backend, scrapers, dashboard & chat AI).
   - Menjalankan healthcheck otomatis hingga service sehat (`healthy`).

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

1. Buka URL dashboard di browser: `https://sosmed.domainanda.com` (atau `http://<ip-vps>:8000`).
2. Statistik dan grafik untuk EasyLegal, EasyTax, dan EasyOffice langsung tampil di halaman utama.
3. Klik tombol **Tanya AI** di pojok kanan atas untuk membuka halaman chat khusus (`/chat`), lalu tanyakan dalam bahasa natural:
   - *"Berapa rata-rata likes dan engagement rate akun EasyLegal bulan ini?"*
   - *"Bandingkan performa akun easylegal_id vs kompetitor legalku_official"*
   - *"Cari postingan yang membahas tentang izin PT dan OSS"*
   - *"Konten seperti apa yang memiliki likes tertinggi di TikTok EasyLegal?"*
4. Halaman chat memanggil backend tool secara otomatis dan menyajikan jawaban analisis lengkap beserta angka faktual. Klik **Kembali ke Dashboard** di sidebar untuk kembali melihat grafik — tetap satu domain, tidak perlu berpindah aplikasi lain.
