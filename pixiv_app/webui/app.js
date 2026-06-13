const state = {
  view: "gallery",
  query: "",
  tag: "",
  type: "",
  artistId: "",
  offset: 0,
  limit: 48,
  total: 0,
  pluginPreviewId: "",
  activePlugin: "",
};

const els = {
  search: document.querySelector("#searchInput"),
  type: document.querySelector("#typeFilter"),
  status: document.querySelector("#statusLine"),
  grid: document.querySelector("#artworkGrid"),
  tags: document.querySelector("#tagList"),
  artists: document.querySelector("#artistList"),
  followed: document.querySelector("#followedList"),
  title: document.querySelector("#viewTitle"),
  subtitle: document.querySelector("#viewSubtitle"),
  pageInfo: document.querySelector("#pageInfo"),
  dialog: document.querySelector("#detailDialog"),
  detail: document.querySelector("#detailContent"),
  pluginNav: document.querySelector("#pluginNav"),
  pluginTarget: document.querySelector("#pluginTargetInput"),
  pluginLimit: document.querySelector("#pluginLimitInput"),
  pluginGrid: document.querySelector("#pluginGrid"),
  pluginSummary: document.querySelector("#pluginSummary"),
  pluginSelectAll: document.querySelector("#pluginSelectAll"),
};

function setStatus(text) {
  els.status.textContent = text;
}

window.addEventListener("unhandledrejection", (event) => {
  setStatus(event.reason?.message || "操作失败，请查看后端日志。");
});

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok || payload.error) {
    throw new Error(payload.error || `HTTP ${response.status}`);
  }
  return payload;
}

function artworkUrl(item, pageIndex = 0, kind = "thumbnail") {
  return `/media/${kind}/${item.artwork_id}/${pageIndex}`;
}

function renderArtworkGrid(payload) {
  state.total = payload.total || 0;
  els.grid.innerHTML = "";
  if (!payload.items.length) {
    els.grid.innerHTML = `<div class="empty">还没有作品。可以先下载作品，或点击"扫描旧目录"导入已有文件。</div>`;
  } else {
    els.grid.append(...payload.items.map(renderArtworkCard));
  }
  const currentPage = Math.floor(state.offset / state.limit) + 1;
  const totalPages = Math.max(1, Math.ceil(state.total / state.limit));
  els.pageInfo.textContent = `${currentPage} / ${totalPages}`;
}

function renderArtworkCard(item) {
  const card = document.createElement("article");
  card.className = "artwork-card";
  const thumb = item.thumbnail_path
    ? `<img loading="lazy" src="${artworkUrl(item)}" alt="${escapeHtml(item.title || String(item.artwork_id))}">`
    : `<div class="thumb-placeholder">${item.type === "novel" ? "小说" : "暂无缩略图"}</div>`;
  card.innerHTML = `
    <button class="thumb-button" data-open="${item.artwork_id}">${thumb}</button>
    <div class="artwork-meta">
      <div class="artwork-title">${escapeHtml(item.title || String(item.artwork_id))}</div>
      <div class="artwork-sub">${escapeHtml(item.artist_name || "未知作者")} · ${escapeHtml(item.type || "")}</div>
      <div class="card-actions">
        <button class="small-button ${item.is_favorite ? "active" : ""}" data-fav="${item.artwork_id}" data-on="${item.is_favorite ? 1 : 0}">
          ${item.is_favorite ? "已收藏" : "收藏"}
        </button>
        ${item.artist_id ? `<button class="small-button" data-artist="${item.artist_id}">作者</button>` : ""}
      </div>
    </div>
  `;
  return card;
}

async function loadArtworks() {
  const params = new URLSearchParams({
    limit: state.limit,
    offset: state.offset,
    ensure_thumbnails: "true",
  });
  if (state.query) params.set("q", state.query);
  if (state.tag) params.set("tag", state.tag);
  if (state.type) params.set("type", state.type);
  if (state.artistId) params.set("artist_id", state.artistId);
  setStatus("正在加载图库...");
  const payload = await api(`/api/artworks?${params}`);
  renderArtworkGrid(payload);
  setStatus(`已加载 ${payload.items.length} / ${payload.total} 个作品`);
}

async function loadFavorites() {
  setStatus("正在加载收藏...");
  const params = new URLSearchParams({ limit: state.limit, offset: state.offset });
  const payload = await api(`/api/favorites?${params}`);
  renderArtworkGrid(payload);
  setStatus(`收藏 ${payload.total} 个作品`);
}

async function loadTags() {
  const tags = await api("/api/tags?limit=80");
  els.tags.innerHTML = "";
  if (!tags.length) {
    els.tags.innerHTML = `<span class="tag-chip">暂无标签</span>`;
    return;
  }
  for (const tag of tags) {
    const button = document.createElement("button");
    button.className = `tag-chip ${state.tag === tag.name ? "active" : ""}`;
    button.textContent = `${tag.name} ${tag.artwork_count}`;
    button.dataset.tag = tag.name;
    els.tags.append(button);
  }
}

async function loadArtists() {
  setStatus("正在加载作者...");
  const artists = await api("/api/artists?limit=200");
  els.artists.innerHTML = "";
  if (!artists.length) {
    els.artists.innerHTML = `<div class="empty">暂无作者数据。</div>`;
  } else {
    els.artists.append(...artists.map(renderArtistRow));
  }
  setStatus(`已加载 ${artists.length} 位作者`);
}

async function loadFollowed() {
  const followed = await api("/api/followed-artists");
  els.followed.innerHTML = "";
  if (!followed.length) {
    els.followed.innerHTML = `<div class="empty">还没有关注作者。</div>`;
  } else {
    els.followed.append(...followed.map(renderArtistRow));
  }
}

function renderArtistRow(artist) {
  const row = document.createElement("div");
  row.className = "artist-row";
  row.innerHTML = `
    <div>
      <strong>${escapeHtml(artist.name || `artist_${artist.artist_id}`)}</strong>
      <span>ID ${artist.artist_id} · ${artist.artwork_count || 0} 个作品${artist.last_synced_at ? ` · 上次同步 ${artist.last_synced_at}` : ""}</span>
    </div>
    <div class="card-actions">
      <button class="small-button" data-artist="${artist.artist_id}">详情</button>
      <button class="small-button" data-follow="${artist.artist_id}" data-name="${escapeHtml(artist.name || "")}">关注</button>
    </div>
  `;
  return row;
}

async function openArtwork(artworkId) {
  const payload = await api(`/api/artworks/${artworkId}`);
  const artwork = payload.artwork;
  const files = payload.files || [];
  const tags = payload.tags || [];
  const firstFile = files[0];
  const image = firstFile
    ? `<img src="/media/source/${artwork.artwork_id}/${firstFile.page_index}" alt="${escapeHtml(artwork.title)}">`
    : `<div class="thumb-placeholder">没有本地文件</div>`;
  els.detail.innerHTML = `
    <div class="detail-layout">
      <div class="detail-media">${image}</div>
      <div class="detail-info">
        <h2>${escapeHtml(artwork.title || String(artwork.artwork_id))}</h2>
        <p>${escapeHtml(artwork.artist_name || "未知作者")} · ${escapeHtml(artwork.type)}</p>
        <div class="card-actions">
          <button class="small-button ${artwork.is_favorite ? "active" : ""}" data-fav="${artwork.artwork_id}" data-on="${artwork.is_favorite ? 1 : 0}">
            ${artwork.is_favorite ? "已收藏" : "收藏"}
          </button>
          ${artwork.artist_id ? `<button class="small-button" data-follow="${artwork.artist_id}" data-name="${escapeHtml(artwork.artist_name || "")}">关注作者</button>` : ""}
        </div>
        <div class="detail-tags">
          ${tags.map((tag) => `<button class="tag-chip" data-tag="${escapeHtml(tag.name)}">${escapeHtml(tag.name)}</button>`).join("")}
        </div>
        <p>${escapeHtml(stripHtml(artwork.caption || ""))}</p>
        <p>页数 ${files.length || artwork.page_count || 0} · 收藏 ${artwork.bookmark_count || 0} · 浏览 ${artwork.view_count || 0}</p>
      </div>
    </div>
  `;
  els.dialog.showModal();
}

async function showArtist(artistId) {
  setActiveView("gallery");
  state.artistId = String(artistId);
  state.tag = "";
  state.offset = 0;
  const detail = await api(`/api/artists/${artistId}?limit=1`);
  els.title.textContent = detail.artist.name || `作者 ${artistId}`;
  els.subtitle.textContent = `作者 ID ${artistId} · ${detail.artist.artwork_count || 0} 个本地作品`;
  await loadArtworks();
}

async function toggleFavorite(artworkId, currentOn) {
  await api("/api/favorites", {
    method: "POST",
    body: JSON.stringify({ artwork_id: Number(artworkId), favorite: !Number(currentOn) }),
  });
  await reloadCurrent();
}

async function followArtist(artistId, name = "") {
  await api("/api/followed-artists", {
    method: "POST",
    body: JSON.stringify({ artist_id: Number(artistId), name, followed: true }),
  });
  setStatus(`已关注作者 ${artistId}`);
  await loadFollowed();
}

async function scanLegacy() {
  setStatus("正在扫描旧下载目录...");
  const result = await api("/api/import/legacy", { method: "POST", body: JSON.stringify({ root: "Sakura_Downloads" }) });
  setStatus(`扫描完成：导入 ${result.imported_files} 个文件，${result.imported_artworks} 个作品`);
  await Promise.all([loadTags(), reloadCurrent()]);
}

async function buildThumbnails() {
  setStatus("正在生成缩略图...");
  const result = await api("/api/thumbnails/build?limit=500");
  setStatus(`缩略图完成：处理 ${result.items.length} 个文件`);
  await reloadCurrent();
}

async function loadPluginPages() {
  const payload = await api("/api/plugins/pages");
  const pages = payload.items || [];
  els.pluginNav.innerHTML = "";
  for (const page of pages) {
    const button = document.createElement("button");
    button.className = "nav-button";
    button.dataset.view = page.view;
    button.textContent = page.title;
    els.pluginNav.append(button);
  }
}

async function importCookie() {
  const fileInput = document.querySelector("#cookieFileInput");
  const file = fileInput.files?.[0];
  if (!file) {
    setStatus("请先选择 cookies.txt 或 json 文件。");
    return;
  }
  setStatus("正在导入 Cookie...");
  const text = await file.text();
  const result = await api("/api/cookies/import", {
    method: "POST",
    body: JSON.stringify({ text }),
  });
  setStatus(`Cookie 导入完成：${result.summary || `${result.count || 0} 个 Cookie`}`);
}

async function previewPlugin(event) {
  event.preventDefault();
  if (!state.activePlugin) {
    setStatus("请先从左侧选择一个插件页面。");
    return;
  }
  const target = els.pluginTarget.value.trim();
  if (!target) {
    setStatus("请输入链接或关键词。");
    return;
  }
  els.pluginGrid.innerHTML = `<div class="empty">正在解析...</div>`;
  els.pluginSummary.textContent = "解析中...";
  const payload = await api("/api/plugin/preview", {
    method: "POST",
    body: JSON.stringify({
      plugin: state.activePlugin,
      target,
      limit: Number(els.pluginLimit.value || 20),
    }),
  });
  state.pluginPreviewId = payload.preview_id;
  renderPluginItems(payload);
  setStatus(`解析完成：${payload.count} 个项目`);
}

function renderPluginItems(payload) {
  els.pluginGrid.innerHTML = "";
  els.pluginSummary.textContent = `${payload.count} 个项目`;
  if (!payload.items.length) {
    els.pluginGrid.innerHTML = `<div class="empty">没有解析到内容。</div>`;
    return;
  }
  els.pluginGrid.append(...payload.items.map(renderPluginCard));
  els.pluginSelectAll.checked = true;
}

function renderPluginCard(item) {
  const card = document.createElement("article");
  card.className = "x-media-card";
  const preview = item.thumbnail || item.metadata?.cover_url || "";
  const media = preview
    ? `<img loading="lazy" referrerpolicy="no-referrer" src="${escapeHtml(preview)}" alt="${escapeHtml(item.title || item.id)}">`
    : `<div class="thumb-placeholder">暂无预览</div>`;
  card.innerHTML = `
    <label class="x-check">
      <input type="checkbox" data-plugin-id="${escapeHtml(item.id)}" checked />
      <span>${item.kind || "项目"}</span>
    </label>
    <div class="x-thumb">${media}</div>
    <div class="artwork-meta">
      <div class="artwork-title">${escapeHtml(item.title || item.id)}</div>
      <div class="artwork-sub">${escapeHtml(item.author || "")} · ${item.file_count || 0} 个文件</div>
    </div>
  `;
  return card;
}

async function downloadPluginSelected() {
  if (!state.pluginPreviewId) {
    setStatus("请先解析内容。");
    return;
  }
  const selected = [...document.querySelectorAll("[data-plugin-id]:checked")].map((item) => item.dataset.pluginId);
  if (!selected.length) {
    setStatus("没有选中的项目。");
    return;
  }
  setStatus(`正在下载 ${selected.length} 个项目...`);
  const result = await api("/api/plugin/download", {
    method: "POST",
    body: JSON.stringify({ preview_id: state.pluginPreviewId, selected_ids: selected }),
  });
  setStatus(`下载完成：保存 ${result.downloaded_files} 个文件，失败 ${result.failed_items} 项`);
}

function setActiveView(view) {
  state.view = view;
  document.querySelectorAll(".nav-button").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === view);
  });
  document.querySelectorAll(".view").forEach((section) => section.classList.remove("active"));
  if (view === "artists") {
    document.querySelector("#artistsView").classList.add("active");
  } else if (view === "sync") {
    document.querySelector("#syncView").classList.add("active");
  } else if (view !== "gallery" && view !== "favorites") {
    // Plugin view
    state.activePlugin = view;
    document.querySelector("#pluginViewTitle").textContent = view;
    document.querySelector("#pluginViewDesc").textContent = "插件预览和下载";
    document.querySelector("#pluginView").classList.add("active");
  } else {
    document.querySelector("#galleryView").classList.add("active");
  }
}

async function reloadCurrent() {
  if (state.view === "favorites") {
    els.title.textContent = "本地收藏";
    els.subtitle.textContent = "你标记收藏的作品";
    await loadFavorites();
  } else if (state.view === "artists") {
    await loadArtists();
  } else if (state.view === "sync") {
    await loadFollowed();
  } else if (state.view !== "gallery") {
    setStatus("插件页已就绪。");
  } else {
    if (!state.artistId) {
      els.title.textContent = state.tag ? `标签：${state.tag}` : "全部作品";
      els.subtitle.textContent = state.query ? `搜索：${state.query}` : "本地已入库作品";
    }
    await loadArtworks();
  }
}

function clearFilters() {
  state.query = "";
  state.tag = "";
  state.type = "";
  state.artistId = "";
  state.offset = 0;
  els.search.value = "";
  els.type.value = "";
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[char]);
}

function stripHtml(value) {
  const tmp = document.createElement("div");
  tmp.innerHTML = value;
  return tmp.textContent || tmp.innerText || "";
}

// Event listeners
document.querySelectorAll(".nav-button").forEach((button) => {
  button.addEventListener("click", async () => {
    setActiveView(button.dataset.view);
    state.offset = 0;
    if (state.view !== "gallery") {
      state.artistId = "";
    }
    await reloadCurrent();
  });
});

// Re-bind nav buttons after plugin nav changes
const navObserver = new MutationObserver(() => {
  document.querySelectorAll(".nav-button").forEach((button) => {
    if (button._bound) return;
    button._bound = true;
    button.addEventListener("click", async () => {
      setActiveView(button.dataset.view);
      state.offset = 0;
      if (state.view !== "gallery") {
        state.artistId = "";
      }
      await reloadCurrent();
    });
  });
});
navObserver.observe(els.pluginNav, { childList: true, subtree: true });

els.search.addEventListener("input", debounce(async () => {
  state.query = els.search.value.trim();
  state.offset = 0;
  setActiveView("gallery");
  await reloadCurrent();
}, 280));

els.type.addEventListener("change", async () => {
  state.type = els.type.value;
  state.offset = 0;
  setActiveView("gallery");
  await reloadCurrent();
});

document.addEventListener("click", async (event) => {
  const target = event.target.closest("button");
  if (!target) return;
  if (target.dataset.open) await openArtwork(target.dataset.open);
  if (target.dataset.fav) await toggleFavorite(target.dataset.fav, target.dataset.on);
  if (target.dataset.artist) await showArtist(target.dataset.artist);
  if (target.dataset.tag) {
    state.tag = target.dataset.tag;
    state.artistId = "";
    state.offset = 0;
    setActiveView("gallery");
    await loadTags();
    await reloadCurrent();
  }
  if (target.dataset.follow) await followArtist(target.dataset.follow, target.dataset.name || "");
});

document.querySelector("#clearFiltersButton").addEventListener("click", async () => {
  clearFilters();
  await loadTags();
  await reloadCurrent();
});

document.querySelector("#prevPageButton").addEventListener("click", async () => {
  state.offset = Math.max(0, state.offset - state.limit);
  await reloadCurrent();
});

document.querySelector("#nextPageButton").addEventListener("click", async () => {
  if (state.offset + state.limit < state.total) {
    state.offset += state.limit;
    await reloadCurrent();
  }
});

document.querySelector("#scanButton").addEventListener("click", scanLegacy);
document.querySelector("#buildThumbsButton").addEventListener("click", buildThumbnails);
document.querySelector("#closeDialogButton").addEventListener("click", () => els.dialog.close());
document.querySelector("#importCookieButton").addEventListener("click", importCookie);
document.querySelector("#pluginForm").addEventListener("submit", previewPlugin);
document.querySelector("#downloadPluginSelectedButton").addEventListener("click", downloadPluginSelected);
els.pluginSelectAll.addEventListener("change", () => {
  document.querySelectorAll("[data-plugin-id]").forEach((item) => {
    item.checked = els.pluginSelectAll.checked;
  });
});

document.querySelector("#followForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const id = document.querySelector("#followArtistId").value.trim();
  const name = document.querySelector("#followArtistName").value.trim();
  if (!id) return;
  await followArtist(id, name);
  event.target.reset();
});

function debounce(fn, delay) {
  let timer = 0;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), delay);
  };
}

async function boot() {
  try {
    await Promise.all([loadPluginPages(), loadTags(), loadArtworks(), loadFollowed()]);
  } catch (error) {
    setStatus(error.message);
  }
}

boot();
