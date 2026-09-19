from __future__ import annotations

import os


def get_analytics_dashboard_url() -> str:
    """Returns the external dashboard URL from environment, or default /analytics."""
    return os.getenv("ANALYTICS_DASHBOARD_URL", "/analytics").strip() or "/analytics"


def render_analytics_dashboard_html() -> str:
    """
    Renders a standalone, responsive, Looker Studio-style single-page analytics dashboard.
    Loads Chart.js via CDN and queries the backend REST endpoints dynamically.
    """
    return """<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>EasyCorp Social Media Analytics & Intelligence</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root {
            --bg-page: #0f172a;
            --bg-card: #1e293b;
            --bg-card-hover: #273549;
            --border-color: #334155;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --primary: #3b82f6;
            --primary-hover: #2563eb;
            --success: #10b981;
            --warning: #f59e0b;
            --danger: #ef4444;
            --accent-purple: #8b5cf6;
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
            font-family: 'Plus Jakarta Sans', sans-serif;
        }

        body {
            background-color: var(--bg-page);
            color: var(--text-main);
            min-height: 100vh;
            padding: 24px 32px;
            overflow-x: hidden;
        }

        /* Header & Filter Toolbar (Looker Studio Style) */
        header {
            display: flex;
            flex-wrap: wrap;
            justify-content: space-between;
            align-items: center;
            gap: 16px;
            padding-bottom: 24px;
            border-bottom: 1px solid var(--border-color);
            margin-bottom: 24px;
        }

        .brand-section h1 {
            font-size: 24px;
            font-weight: 800;
            letter-spacing: -0.5px;
            display: flex;
            align-items: center;
            gap: 10px;
        }

        .brand-section p {
            color: var(--text-muted);
            font-size: 14px;
            margin-top: 4px;
        }

        .badge-live {
            background: rgba(16, 185, 129, 0.2);
            color: var(--success);
            border: 1px solid rgba(16, 185, 129, 0.4);
            font-size: 11px;
            font-weight: 700;
            padding: 4px 10px;
            border-radius: 9999px;
            text-transform: uppercase;
        }

        .controls-section {
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            gap: 12px;
        }

        .filter-group {
            display: flex;
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 3px;
        }

        .filter-btn {
            background: transparent;
            border: none;
            color: var(--text-muted);
            padding: 6px 14px;
            font-size: 13px;
            font-weight: 600;
            border-radius: 6px;
            cursor: pointer;
            transition: all 0.2s ease;
        }

        .filter-btn.active {
            background: var(--primary);
            color: #ffffff;
            box-shadow: 0 2px 8px rgba(59, 130, 246, 0.35);
        }

        .sync-btn {
            background: linear-gradient(135deg, var(--primary), var(--accent-purple));
            color: white;
            border: none;
            padding: 8px 18px;
            font-size: 13px;
            font-weight: 700;
            border-radius: 8px;
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 8px;
            transition: transform 0.15s, opacity 0.2s;
        }

        .sync-btn:hover {
            opacity: 0.95;
            transform: translateY(-1px);
        }

        .sync-btn:disabled {
            opacity: 0.6;
            cursor: not-allowed;
            transform: none;
        }

        /* KPI Scorecards Grid */
        .kpi-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
            gap: 18px;
            margin-bottom: 24px;
        }

        .kpi-card {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 20px;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            transition: border-color 0.2s;
        }

        .kpi-card:hover {
            border-color: #475569;
        }

        .kpi-title {
            color: var(--text-muted);
            font-size: 13px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 8px;
        }

        .kpi-value {
            font-size: 32px;
            font-weight: 800;
            letter-spacing: -0.5px;
            color: #ffffff;
        }

        .kpi-sub {
            margin-top: 10px;
            font-size: 12px;
            color: var(--text-muted);
            display: flex;
            align-items: center;
            gap: 6px;
        }

        .sub-highlight {
            color: var(--success);
            font-weight: 700;
        }

        /* Charts Section */
        .charts-grid {
            display: grid;
            grid-template-columns: 2fr 1fr;
            gap: 20px;
            margin-bottom: 24px;
        }

        @media (max-width: 1024px) {
            .charts-grid {
                grid-template-columns: 1fr;
            }
        }

        .chart-card {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 20px;
            position: relative;
        }

        .chart-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 16px;
        }

        .chart-header h3 {
            font-size: 16px;
            font-weight: 700;
            letter-spacing: -0.2px;
        }

        .chart-container {
            position: relative;
            height: 320px;
            width: 100%;
        }

        /* Leaderboard Table Section */
        .table-card {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 24px;
        }

        .table-card h3 {
            font-size: 16px;
            font-weight: 700;
            margin-bottom: 16px;
        }

        .table-wrapper {
            overflow-x: auto;
        }

        table {
            width: 100%;
            border-collapse: collapse;
            text-align: left;
            font-size: 13px;
        }

        th {
            background: #192231;
            color: var(--text-muted);
            font-weight: 700;
            padding: 12px 16px;
            border-bottom: 1px solid var(--border-color);
            white-space: nowrap;
        }

        td {
            padding: 14px 16px;
            border-bottom: 1px solid rgba(51, 65, 85, 0.5);
            vertical-align: middle;
        }

        tr:hover td {
            background: var(--bg-card-hover);
        }

        .platform-badge {
            display: inline-block;
            padding: 3px 8px;
            border-radius: 6px;
            font-size: 11px;
            font-weight: 700;
            text-transform: uppercase;
        }

        .platform-instagram { background: rgba(225, 48, 108, 0.2); color: #f43f5e; border: 1px solid rgba(225, 48, 108, 0.4); }
        .platform-tiktok { background: rgba(0, 242, 234, 0.2); color: #06b6d4; border: 1px solid rgba(0, 242, 234, 0.4); }
        .platform-threads { background: rgba(255, 255, 255, 0.15); color: #ffffff; border: 1px solid rgba(255, 255, 255, 0.3); }

        .brand-tag {
            font-size: 11px;
            font-weight: 700;
            padding: 2px 8px;
            border-radius: 999px;
        }
        .tag-own { background: rgba(59, 130, 246, 0.2); color: #60a5fa; }
        .tag-comp { background: rgba(245, 158, 11, 0.2); color: #fbbf24; }

        .hook-text {
            color: #ffffff;
            font-weight: 600;
            line-height: 1.4;
            max-width: 420px;
        }

        .link-btn {
            color: var(--primary);
            text-decoration: none;
            font-weight: 700;
            display: inline-flex;
            align-items: center;
            gap: 4px;
        }
        .link-btn:hover {
            text-decoration: underline;
        }

        /* Toast notifications */
        #toast {
            position: fixed;
            bottom: 24px;
            right: 24px;
            background: #1e293b;
            color: white;
            border: 1px solid var(--primary);
            padding: 12px 20px;
            border-radius: 8px;
            box-shadow: 0 10px 25px rgba(0,0,0,0.5);
            display: none;
            z-index: 1000;
            font-size: 14px;
            font-weight: 600;
        }
    </style>
</head>
<body>

    <!-- Header / Looker Studio Toolbar -->
    <header>
        <div class="brand-section">
            <h1>
                <span>📊 EasyCorp Social Media Intelligence</span>
                <span class="badge-live">Live Data</span>
            </h1>
            <p>Dashboard Analitik Tren Harian, Mingguan & Kompetitor (@id.easylegal, @id.easytax, @id.easyoffice)</p>
        </div>
        <div class="controls-section">
            <!-- Timeframe Filter -->
            <div class="filter-group" id="timeframe-group">
                <button class="filter-btn" data-days="3">3 Hari</button>
                <button class="filter-btn active" data-days="7">7 Hari</button>
                <button class="filter-btn" data-days="30">30 Hari</button>
                <button class="filter-btn" data-days="3650">Semua</button>
            </div>
            <!-- Platform Filter -->
            <div class="filter-group" id="platform-group">
                <button class="filter-btn active" data-platform="all">Semua</button>
                <button class="filter-btn" data-platform="instagram">Instagram</button>
                <button class="filter-btn" data-platform="tiktok">TikTok</button>
                <button class="filter-btn" data-platform="threads">Threads</button>
            </div>
            <!-- Manual Sync Button -->
            <button class="sync-btn" id="sync-now-btn" onclick="triggerManualSync()">
                <span>⚡ Sync Sekarang</span>
            </button>
        </div>
    </header>

    <!-- Top KPI Cards -->
    <div class="kpi-grid">
        <div class="kpi-card">
            <div class="kpi-title">Total Postingan Termonitor</div>
            <div class="kpi-value" id="kpi-total-posts">-</div>
            <div class="kpi-sub">
                <span>Brand: <b id="sub-brand-posts">-</b></span>
                <span>•</span>
                <span>Kompetitor: <b id="sub-comp-posts">-</b></span>
            </div>
        </div>
        <div class="kpi-card">
            <div class="kpi-title">Total Tayangan (Views)</div>
            <div class="kpi-value" id="kpi-total-views">-</div>
            <div class="kpi-sub">
                <span class="sub-highlight">🔥 Jangkauan Video & Reels</span>
            </div>
        </div>
        <div class="kpi-card">
            <div class="kpi-title">Total Interaksi (Likes & Komentar)</div>
            <div class="kpi-value" id="kpi-total-likes">-</div>
            <div class="kpi-sub">
                <span>Komentar: <b id="sub-total-comments">-</b></span>
            </div>
        </div>
        <div class="kpi-card">
            <div class="kpi-title">Rata-Rata Engagement Rate</div>
            <div class="kpi-value" id="kpi-avg-er">-%</div>
            <div class="kpi-sub">
                <span>Brand: <b id="sub-brand-er">-</b></span>
                <span>•</span>
                <span>Kompetitor: <b id="sub-comp-er">-</b></span>
            </div>
        </div>
    </div>

    <!-- Interactive Charts Grid -->
    <div class="charts-grid">
        <div class="chart-card">
            <div class="chart-header">
                <h3>📈 Pergerakan Tren Harian (Views & Likes)</h3>
                <span style="color: var(--text-muted); font-size: 12px;">Pola Lonjakan Virality</span>
            </div>
            <div class="chart-container">
                <canvas id="timeseriesChart"></canvas>
            </div>
        </div>
        <div class="chart-card">
            <div class="chart-header">
                <h3>⚖️ Brand vs Kompetitor per Topik</h3>
                <span style="color: var(--text-muted); font-size: 12px;">Share of Voice</span>
            </div>
            <div class="chart-container">
                <canvas id="competitorChart"></canvas>
            </div>
        </div>
    </div>

    <!-- Leaderboard Table -->
    <div class="table-card">
        <h3>🏆 Leaderboard Konten Viral & Analisis Hook Terbaik</h3>
        <div class="table-wrapper">
            <table>
                <thead>
                    <tr>
                        <th>Platform</th>
                        <th>Akun</th>
                        <th>Kategori</th>
                        <th>Hook Pembuka (2 Detik Pertama)</th>
                        <th>Views</th>
                        <th>Likes</th>
                        <th>Engagement</th>
                        <th>Aksi</th>
                    </tr>
                </thead>
                <tbody id="leaderboard-body">
                    <tr><td colspan="8" style="text-align: center; color: var(--text-muted);">Memuat data analitik...</td></tr>
                </tbody>
            </table>
        </div>
    </div>

    <div id="toast"></div>

    <script>
        let currentDays = 7;
        let currentPlatform = 'all';
        let timeseriesChart = null;
        let competitorChart = null;

        // Initialize on load
        window.addEventListener('DOMContentLoaded', () => {
            setupFilters();
            loadAllDashboardData();
        });

        function setupFilters() {
            // Timeframe buttons
            document.querySelectorAll('#timeframe-group .filter-btn').forEach(btn => {
                btn.addEventListener('click', (e) => {
                    document.querySelectorAll('#timeframe-group .filter-btn').forEach(b => b.classList.remove('active'));
                    btn.classList.add('active');
                    currentDays = parseInt(btn.dataset.days);
                    loadAllDashboardData();
                });
            });

            // Platform buttons
            document.querySelectorAll('#platform-group .filter-btn').forEach(btn => {
                btn.addEventListener('click', (e) => {
                    document.querySelectorAll('#platform-group .filter-btn').forEach(b => b.classList.remove('active'));
                    btn.classList.add('active');
                    currentPlatform = btn.dataset.platform;
                    loadAllDashboardData();
                });
            });
        }

        async function loadAllDashboardData() {
            try {
                await Promise.all([
                    loadOverview(),
                    loadTimeseries(),
                    loadCompetitorComparison(),
                    loadLeaderboard()
                ]);
            } catch (err) {
                console.error("Failed to load dashboard data:", err);
            }
        }

        async function loadOverview() {
            const res = await fetch(`/api/analytics/overview?days=${currentDays}`);
            const json = await res.json();
            if (json.status !== 'success') return;
            const kpi = json.data.kpis;
            const bvc = json.data.brand_vs_competitor;

            document.getElementById('kpi-total-posts').textContent = Number(kpi.total_posts).toLocaleString();
            document.getElementById('kpi-total-views').textContent = Number(kpi.total_views).toLocaleString();
            document.getElementById('kpi-total-likes').textContent = Number(kpi.total_likes).toLocaleString();
            document.getElementById('kpi-avg-er').textContent = kpi.avg_engagement_rate + '%';

            document.getElementById('sub-brand-posts').textContent = bvc.brand.posts_count;
            document.getElementById('sub-comp-posts').textContent = bvc.competitor.posts_count;
            document.getElementById('sub-total-comments').textContent = Number(kpi.total_comments).toLocaleString();
            document.getElementById('sub-brand-er').textContent = bvc.brand.avg_engagement_rate + '%';
            document.getElementById('sub-comp-er').textContent = bvc.competitor.avg_engagement_rate + '%';
        }

        async function loadTimeseries() {
            const platParam = currentPlatform !== 'all' ? `&platform=${currentPlatform}` : '';
            const res = await fetch(`/api/analytics/trends?days=${currentDays}${platParam}`);
            const json = await res.json();
            if (json.status !== 'success') return;
            const rows = json.data;

            const labels = rows.map(r => r.date);
            const viewsData = rows.map(r => r.total_views);
            const likesData = rows.map(r => r.total_likes);

            if (timeseriesChart) timeseriesChart.destroy();
            const ctx = document.getElementById('timeseriesChart').getContext('2d');
            timeseriesChart = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: labels,
                    datasets: [
                        {
                            label: 'Views',
                            data: viewsData,
                            borderColor: '#3b82f6',
                            backgroundColor: 'rgba(59, 130, 246, 0.15)',
                            fill: true,
                            tension: 0.35,
                            borderWidth: 3,
                            pointBackgroundColor: '#3b82f6',
                            pointRadius: 4,
                        },
                        {
                            label: 'Likes',
                            data: likesData,
                            borderColor: '#10b981',
                            backgroundColor: 'transparent',
                            tension: 0.35,
                            borderWidth: 2,
                            borderDash: [5, 5],
                            pointRadius: 3,
                        }
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {
                        x: { grid: { color: 'rgba(51, 65, 85, 0.4)' }, ticks: { color: '#94a3b8' } },
                        y: { grid: { color: 'rgba(51, 65, 85, 0.4)' }, ticks: { color: '#94a3b8' } }
                    },
                    plugins: {
                        legend: { labels: { color: '#f8fafc', font: { weight: '600' } } }
                    }
                }
            });
        }

        async function loadCompetitorComparison() {
            const res = await fetch(`/api/analytics/competitor-comparison?days=${currentDays}`);
            const json = await res.json();
            if (json.status !== 'success') return;
            const topicsDist = json.data.topics_distribution || {};

            const topics = Object.keys(topicsDist);
            const brandViews = topics.map(t => topicsDist[t].brand_views);
            const compViews = topics.map(t => topicsDist[t].competitor_views);

            if (competitorChart) competitorChart.destroy();
            const ctx = document.getElementById('competitorChart').getContext('2d');
            competitorChart = new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: topics.length ? topics : ['Belum Ada Data'],
                    datasets: [
                        {
                            label: 'EasyCorp Brand',
                            data: brandViews.length ? brandViews : [0],
                            backgroundColor: '#3b82f6',
                            borderRadius: 6
                        },
                        {
                            label: 'Kompetitor',
                            data: compViews.length ? compViews : [0],
                            backgroundColor: '#f59e0b',
                            borderRadius: 6
                        }
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {
                        x: { grid: { display: false }, ticks: { color: '#94a3b8' } },
                        y: { grid: { color: 'rgba(51, 65, 85, 0.4)' }, ticks: { color: '#94a3b8' } }
                    },
                    plugins: {
                        legend: { labels: { color: '#f8fafc', font: { weight: '600' } } }
                    }
                }
            });
        }

        async function loadLeaderboard() {
            const platParam = currentPlatform !== 'all' ? `&platform=${currentPlatform}` : '';
            const res = await fetch(`/api/analytics/leaderboard?limit=10&days=${currentDays}${platParam}`);
            const json = await res.json();
            const tbody = document.getElementById('leaderboard-body');

            if (json.status !== 'success' || !json.data.length) {
                tbody.innerHTML = '<tr><td colspan="8" style="text-align: center; color: var(--text-muted);">Belum ada postingan di rentang waktu ini. Klik Sync Sekarang untuk mengambil data.</td></tr>';
                return;
            }

            tbody.innerHTML = json.data.map(item => `
                <tr>
                    <td><span class="platform-badge platform-${item.platform}">${item.platform}</span></td>
                    <td><b>@${item.username}</b></td>
                    <td>
                        <span class="brand-tag ${item.is_own_brand ? 'tag-own' : 'tag-comp'}">
                            ${item.is_own_brand ? 'EasyCorp' : 'Kompetitor'}
                        </span>
                        <div style="font-size: 11px; color: var(--text-muted); margin-top: 2px;">${item.topic}</div>
                    </td>
                    <td><div class="hook-text">"${item.hook || item.caption}"</div></td>
                    <td><b>${Number(item.views).toLocaleString()}</b></td>
                    <td>${Number(item.likes).toLocaleString()}</td>
                    <td><b style="color: var(--success);">${item.engagement_rate}%</b></td>
                    <td>
                        <a href="${item.permalink}" target="_blank" rel="noopener noreferrer" class="link-btn">
                            Lihat ↗
                        </a>
                    </td>
                </tr>
            `).join('');
        }

        async function triggerManualSync() {
            const btn = document.getElementById('sync-now-btn');
            btn.disabled = true;
            btn.innerHTML = '<span>⏳ Sedang Sync...</span>';
            showToast('Memulai sinkronisasi harian paralel di latar belakang...');

            try {
                const res = await fetch('/api/cron/daily-sync?max_posts=10&background=true', { method: 'POST' });
                const json = await res.json();
                if (json.status === 'accepted' || json.status === 'success') {
                    showToast('Sync berhasil dipicu! Data sedang ditarik paralel dari Instagram, TikTok, dan Threads.');
                    // Reload data after 15s and 30s
                    setTimeout(loadAllDashboardData, 15000);
                    setTimeout(loadAllDashboardData, 30000);
                } else {
                    showToast('Gagal memicu sync: ' + (json.message || 'Unknown error'));
                }
            } catch (err) {
                showToast('Terjadi kesalahan koneksi saat memicu sync.');
            } finally {
                setTimeout(() => {
                    btn.disabled = false;
                    btn.innerHTML = '<span>⚡ Sync Sekarang</span>';
                }, 5000);
            }
        }

        function showToast(msg) {
            const toast = document.getElementById('toast');
            toast.textContent = msg;
            toast.style.display = 'block';
            setTimeout(() => { toast.style.display = 'none'; }, 4000);
        }
    </script>
</body>
</html>
"""
