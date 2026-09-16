document.addEventListener("DOMContentLoaded", () => {
  // Navigation elements
  const tabs = document.querySelectorAll(".nav-tab");
  const panes = document.querySelectorAll(".tab-pane");

  // Topic research elements
  const topicCount = document.getElementById("topic-count");
  const topicSearchInput = document.getElementById("topic-search-input");
  const topicSearchBtn = document.getElementById("topic-search-btn");
  const topicPills = document.querySelectorAll(".topic-pill");
  const topicTotalPosts = document.getElementById("topic-total-posts");
  const topicAvgLikes = document.getElementById("topic-avg-likes");
  const topicMaxLikes = document.getElementById("topic-max-likes");
  const topicAvgViews = document.getElementById("topic-avg-views");
  const topicEr = document.getElementById("topic-er");
  const topicViralPostsBody = document.getElementById("topic-viral-posts-body");
  const askAiTopicBtn = document.getElementById("ask-ai-topic-btn");

  // Chat elements
  const chatMessages = document.getElementById("chat-messages");
  const chatForm = document.getElementById("chat-form");
  const chatInput = document.getElementById("chat-input");
  const promptChips = document.querySelectorAll(".prompt-chip");
  const triggerScrapeBtn = document.getElementById("trigger-scrape-btn");
  const seedDataBtn = document.getElementById("seed-data-btn");

  // Accounts elements
  const accountsTableBody = document.getElementById("accounts-table-body");
  const accountCount = document.getElementById("account-count");
  const addAccountToggleBtn = document.getElementById("add-account-toggle-btn");
  const addAccountCard = document.getElementById("add-account-card");
  const addAccountForm = document.getElementById("add-account-form");
  const cancelAddAccountBtn = document.getElementById("cancel-add-account-btn");

  // Posts elements
  const postsTableBody = document.getElementById("posts-table-body");
  const postCount = document.getElementById("post-count");
  const postSearchKeyword = document.getElementById("post-search-keyword");
  const postFilterPlatform = document.getElementById("post-filter-platform");
  const postFilterBtn = document.getElementById("post-filter-btn");

  // Logs elements
  const logsTableBody = document.getElementById("logs-table-body");
  const refreshLogsBtn = document.getElementById("refresh-logs-btn");

  let currentResearchedTopic = "pendirian PT";

  // --- 1. Tab Navigation ---
  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      tabs.forEach((t) => t.classList.remove("active"));
      panes.forEach((p) => p.classList.remove("active"));

      tab.classList.add("active");
      const targetPane = document.getElementById(tab.dataset.tab);
      if (targetPane) targetPane.classList.add("active");

      // Auto load data on tab switch
      if (tab.dataset.tab === "topics-tab") loadTopics();
      if (tab.dataset.tab === "accounts-tab") loadAccounts();
      if (tab.dataset.tab === "posts-tab") loadPosts();
      if (tab.dataset.tab === "logs-tab") loadLogs();
    });
  });

  // --- 2. Topic & Content Research ---
  async function loadTopics() {
    try {
      const res = await fetch("/topics");
      const json = await res.json();
      const topics = json.data || [];
      if (topicCount) topicCount.textContent = topics.length;
    } catch (err) {
      console.error("Gagal memuat topik:", err);
    }
  }

  async function researchTopic(keyword) {
    if (!keyword || !keyword.trim()) return;
    const cleanKw = keyword.trim();
    currentResearchedTopic = cleanKw;
    if (topicSearchInput) topicSearchInput.value = cleanKw;

    topicViralPostsBody.innerHTML = `<tr><td colspan="7" class="text-center">Sedang meriset topik '${cleanKw}'...</td></tr>`;

    try {
      const res = await fetch(`/topics/summary?keyword=${encodeURIComponent(cleanKw)}`);
      const json = await res.json();
      const data = json.data || {};

      topicTotalPosts.textContent = (data.total_posts || 0).toLocaleString();
      topicAvgLikes.textContent = (data.avg_likes || 0).toLocaleString();
      topicMaxLikes.textContent = (data.max_likes || 0).toLocaleString();
      topicAvgViews.textContent = (data.avg_views || 0).toLocaleString();
      topicEr.textContent = `${data.engagement_rate || 0}%`;

      const viralPosts = data.viral_references || [];
      if (viralPosts.length === 0) {
        topicViralPostsBody.innerHTML = `<tr><td colspan="7" class="text-center">Belum ada data untuk topik '${cleanKw}'. Klik tombol 'Isi Sample Data' atau jalankan scraper untuk mengumpulkan konten.</td></tr>`;
        return;
      }

      topicViralPostsBody.innerHTML = viralPosts
        .map((p) => {
          const platBadge = p.platform === "instagram" ? "badge-ig" : "badge-tt";
          const formattedDate = p.posted_at ? p.posted_at.substring(0, 10) : "-";
          return `
            <tr>
              <td><span class="badge ${platBadge}">${(p.platform || "").toUpperCase()}</span></td>
              <td><strong>@${p.username || "-"}</strong></td>
              <td title="${(p.caption || "").replace(/"/g, "&quot;")}">${p.caption || "-"}</td>
              <td><strong style="color: #2563eb;">${(p.likes || 0).toLocaleString()}</strong></td>
              <td>${(p.comments || 0).toLocaleString()}</td>
              <td>${p.views !== null && p.views !== undefined ? Number(p.views).toLocaleString() : "-"}</td>
              <td>${formattedDate}</td>
            </tr>
          `;
        })
        .join("");
    } catch (err) {
      topicViralPostsBody.innerHTML = `<tr><td colspan="7" class="text-center text-danger">Gagal meriset topik: ${err.message}</td></tr>`;
    }
  }

  if (topicSearchBtn) {
    topicSearchBtn.addEventListener("click", () => {
      researchTopic(topicSearchInput.value);
    });
  }

  if (topicSearchInput) {
    topicSearchInput.addEventListener("keypress", (e) => {
      if (e.key === "Enter") researchTopic(topicSearchInput.value);
    });
  }

  topicPills.forEach((pill) => {
    pill.addEventListener("click", () => {
      researchTopic(pill.dataset.keyword);
    });
  });

  if (askAiTopicBtn) {
    askAiTopicBtn.addEventListener("click", () => {
      document.querySelector('[data-tab="chat-tab"]').click();
      sendChatMessage(`Berikan analisis taktis dan ide konten berkinerja tinggi untuk topik '${currentResearchedTopic}'`);
    });
  }

  // --- 3. Chat Functionality ---
  function appendMessage(role, text, toolUsed = null) {
    const msgDiv = document.createElement("div");
    msgDiv.className = `message ${role}-message`;

    const avatar = document.createElement("div");
    avatar.className = "message-avatar";
    avatar.textContent = role === "user" ? "U" : "AI";

    const contentDiv = document.createElement("div");
    contentDiv.className = "message-content";

    const paragraphs = text.split("\n").filter((p) => p.trim() !== "");
    if (paragraphs.length === 0) {
      const p = document.createElement("p");
      p.textContent = text;
      contentDiv.appendChild(p);
    } else {
      paragraphs.forEach((para) => {
        const p = document.createElement("p");
        p.textContent = para;
        contentDiv.appendChild(p);
      });
    }

    if (toolUsed) {
      const badge = document.createElement("span");
      badge.className = "message-tool-badge";
      badge.textContent = `🛠️ Tool: ${toolUsed}`;
      contentDiv.appendChild(badge);
    }

    msgDiv.appendChild(avatar);
    msgDiv.appendChild(contentDiv);
    chatMessages.appendChild(msgDiv);
    chatMessages.scrollTop = chatMessages.scrollHeight;
    return msgDiv;
  }

  async function sendChatMessage(query) {
    if (!query || !query.trim()) return;
    const cleanQuery = query.trim();

    appendMessage("user", cleanQuery);
    chatInput.value = "";

    const loadingDiv = appendMessage("assistant", "Sedang menganalisis tren konten & data media sosial...");

    try {
      const res = await fetch("/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: cleanQuery }),
      });
      const data = await res.json();
      chatMessages.removeChild(loadingDiv);

      if (data.status === "success") {
        appendMessage("assistant", data.reply, data.tool_used);
      } else {
        appendMessage("assistant", data.message || "Terjadi kesalahan saat memproses pertanyaan.");
      }
    } catch (err) {
      chatMessages.removeChild(loadingDiv);
      appendMessage("assistant", `Gagal menghubungi server API: ${err.message}`);
    }
  }

  chatForm.addEventListener("submit", (e) => {
    e.preventDefault();
    sendChatMessage(chatInput.value);
  });

  promptChips.forEach((chip) => {
    chip.addEventListener("click", () => {
      sendChatMessage(chip.dataset.query);
    });
  });

  // --- 4. Accounts Management ---
  async function loadAccounts() {
    try {
      const res = await fetch("/accounts");
      const json = await res.json();
      const accounts = json.data || [];
      accountCount.textContent = accounts.length;

      if (accounts.length === 0) {
        accountsTableBody.innerHTML = `<tr><td colspan="4" class="text-center">Belum ada akun yang didaftarkan.</td></tr>`;
        return;
      }

      accountsTableBody.innerHTML = accounts
        .map((acc) => {
          const platBadge = acc.platform === "instagram" ? "badge-ig" : "badge-tt";
          const brandBadge = acc.is_own_brand ? "badge-brand" : "badge-comp";
          const brandText = acc.is_own_brand ? "Brand Sendiri" : "Kompetitor";
          return `
            <tr>
              <td><span class="badge ${platBadge}">${acc.platform.toUpperCase()}</span></td>
              <td><strong>@${acc.username}</strong></td>
              <td><span class="badge ${brandBadge}">${brandText}</span></td>
              <td>
                <button class="btn btn-secondary btn-sm" onclick="queryAccountSummary('${acc.username}', '${acc.platform}')">
                  Lihat Performa
                </button>
              </td>
            </tr>
          `;
        })
        .join("");
    } catch (err) {
      accountsTableBody.innerHTML = `<tr><td colspan="4" class="text-center text-danger">Gagal memuat akun: ${err.message}</td></tr>`;
    }
  }

  async function searchAccounts() {
    const query = document.getElementById("account-search-input").value.trim();
    if (!query) {
      loadAccounts();
      return;
    }
    try {
      const res = await fetch(`/accounts/search?q=${encodeURIComponent(query)}`);
      const json = await res.json();
      const accounts = json.data || [];
      accountCount.textContent = accounts.length;
      if (accounts.length === 0) {
        accountsTableBody.innerHTML = `<tr><td colspan="4" class="text-center">Tidak ada akun cocok.</td></tr>`;
        return;
      }
      accountsTableBody.innerHTML = accounts
        .map((acc) => {
          const platBadge = acc.platform === "instagram" ? "badge-ig" : "badge-tt";
          const brandBadge = acc.is_own_brand ? "badge-brand" : "badge-comp";
          const brandText = acc.is_own_brand ? "Brand Sendiri" : "Kompetitor";
          return `
            <tr>
              <td><span class="badge ${platBadge}">${acc.platform.toUpperCase()}</span></td>
              <td><strong>@${acc.username}</strong></td>
              <td><span class="badge ${brandBadge}">${brandText}</span></td>
              <td>
                <button class="btn btn-primary btn-sm" onclick="quickAddAccount('${acc.username}', '${acc.platform}')">Tambah</button>
                <button class="btn btn-secondary btn-sm" onclick="queryAccountSummary('${acc.username}', '${acc.platform}')">Lihat Performa</button>
              </td>
            </tr>
          `;
        })
        .join("");
    } catch (err) {
      accountsTableBody.innerHTML = `<tr><td colspan="4" class="text-center text-danger">Gagal mencari akun: ${err.message}</td></tr>`;
    }
  }

  window.quickAddAccount = async (username, platform) => {
    try {
      const res = await fetch("/accounts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ platform, username, is_own_brand: false }),
      });
      const data = await res.json();
      if (data.status === "success") {
        alert(`Akun @${username} berhasil ditambahkan!`);
        loadAccounts();
      } else {
        alert(data.message || "Gagal menambahkan akun.");
      }
    } catch (err) {
      alert(`Error: ${err.message}`);
    }
  };

  document.getElementById("account-search-btn").addEventListener("click", searchAccounts);
  document.getElementById("account-search-input").addEventListener("keypress", (e) => {
    if (e.key === "Enter") searchAccounts();
  });

  window.queryAccountSummary = (username, platform) => {
    document.querySelector('[data-tab="chat-tab"]').click();
    sendChatMessage(`Bagaimana ringkasan performa engagement untuk akun @${username} di ${platform}?`);
  };

  addAccountToggleBtn.addEventListener("click", () => {
    addAccountCard.style.display = addAccountCard.style.display === "none" ? "block" : "none";
  });

  cancelAddAccountBtn.addEventListener("click", () => {
    addAccountCard.style.display = "none";
  });

  addAccountForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const platform = document.getElementById("acc-platform").value;
    const username = document.getElementById("acc-username").value.trim();
    const isOwn = document.getElementById("acc-own-brand").value === "true";

    try {
      const res = await fetch("/accounts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ platform, username, is_own_brand: isOwn }),
      });
      const data = await res.json();
      if (data.status === "success") {
        addAccountForm.reset();
        addAccountCard.style.display = "none";
        loadAccounts();
        alert(`Akun @${username} berhasil didaftarkan!`);
      } else {
        alert(data.message || "Gagal menambahkan akun.");
      }
    } catch (err) {
      alert(`Error: ${err.message}`);
    }
  });

  // --- 5. Posts Data ---
  async function loadPosts() {
    const keyword = postSearchKeyword.value.trim();
    const platform = postFilterPlatform.value;

    let url = `/posts?limit=50`;
    if (keyword) url += `&keyword=${encodeURIComponent(keyword)}`;
    if (platform) url += `&platform=${encodeURIComponent(platform)}`;

    try {
      const res = await fetch(url);
      const json = await res.json();
      const posts = json.data || [];
      postCount.textContent = posts.length;

      if (posts.length === 0) {
        postsTableBody.innerHTML = `<tr><td colspan="7" class="text-center">Belum ada postingan yang sesuai filter. Jalankan scraper untuk mengisi data.</td></tr>`;
        return;
      }

      postsTableBody.innerHTML = posts
        .map((p) => {
          const platBadge = p.platform === "instagram" ? "badge-ig" : "badge-tt";
          const formattedDate = p.posted_at ? p.posted_at.substring(0, 10) : "-";
          const captionExcerpt = (p.caption || "-").length > 75 ? p.caption.substring(0, 75) + "..." : p.caption;
          return `
            <tr>
              <td><span class="badge ${platBadge}">${(p.platform || "").toUpperCase()}</span></td>
              <td><strong>@${p.username || "-"}</strong></td>
              <td title="${(p.caption || "").replace(/"/g, "&quot;")}">${captionExcerpt}</td>
              <td>${(p.likes || 0).toLocaleString()}</td>
              <td>${(p.comments || 0).toLocaleString()}</td>
              <td>${p.views !== null && p.views !== undefined ? Number(p.views).toLocaleString() : "-"}</td>
              <td>${formattedDate}</td>
            </tr>
          `;
        })
        .join("");
    } catch (err) {
      postsTableBody.innerHTML = `<tr><td colspan="7" class="text-center">Gagal memuat post: ${err.message}</td></tr>`;
    }
  }

  postFilterBtn.addEventListener("click", loadPosts);
  postSearchKeyword.addEventListener("keypress", (e) => {
    if (e.key === "Enter") loadPosts();
  });

  // --- 6. Scrape Controls & Logs ---
  async function loadLogs() {
    try {
      const res = await fetch("/scrape/logs?limit=50");
      const json = await res.json();
      const logs = json.data || [];

      if (logs.length === 0) {
        logsTableBody.innerHTML = `<tr><td colspan="4" class="text-center">Belum ada log scraping tercatat.</td></tr>`;
        return;
      }

      logsTableBody.innerHTML = logs
        .map((log) => {
          const statusBadge = log.status === "success" ? "badge-success" : "badge-failed";
          const formattedRun = log.run_at ? log.run_at.replace("T", " ").substring(0, 19) : "-";
          return `
            <tr>
              <td>${formattedRun}</td>
              <td>${(log.platform || "").toUpperCase()}</td>
              <td><span class="badge ${statusBadge}">${log.status.toUpperCase()}</span></td>
              <td class="${log.error_message ? 'text-danger' : ''}">${log.error_message || "Sukses"}</td>
            </tr>
          `;
        })
        .join("");
    } catch (err) {
      logsTableBody.innerHTML = `<tr><td colspan="4" class="text-center">Gagal memuat log: ${err.message}</td></tr>`;
    }
  }

  refreshLogsBtn.addEventListener("click", loadLogs);

  triggerScrapeBtn.addEventListener("click", async () => {
    if (!confirm("Jalankan scraping sekarang untuk semua akun yang dimonitor?")) return;
    try {
      const res = await fetch("/scrape/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      const data = await res.json();
      alert(data.message || "Proses scraping dimulai di background!");
      loadLogs();
    } catch (err) {
      alert(`Gagal memicu scraping: ${err.message}`);
    }
  });

  if (seedDataBtn) {
    seedDataBtn.addEventListener("click", async () => {
      if (!confirm("Isi database dengan sample data postingan Instagram & TikTok untuk riset?")) return;
      try {
        const res = await fetch("/api/seed-sample-data", { method: "POST" });
        const data = await res.json();
        alert(data.message || "Sample data berhasil dimuat!");
        loadTopics();
        loadAccounts();
        loadPosts();
        researchTopic("pendirian PT");
      } catch (err) {
        alert(`Gagal memuat sample data: ${err.message}`);
      }
    });
  }

  // Initial load
  loadTopics();
  researchTopic("pendirian PT");
});
