const state = {
  view: "gallery",
  query: "",
  tag: "",
  type: "",
  artistId: "",
  offset: 0,
  limit: 48,
  total: 0,
  xPreviewId: "",
  xhsPreviewId: "",
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
  pixivCookieFile: document.querySelector("#pixivCookieFile"),
  xCookieFile: document.querySelector("#xCookieFile"),
  xUsername: document.querySelector("#xUsernameInput"),
  xMediaType: document.querySelector("#xMediaType"),
  xMaxItems: document.querySelector("#xMaxItems"),
  xMediaGrid: document.querySelector("#xMediaGrid"),
  xMediaSummary: document.querySelector("#xMediaSummary"),
  xSelectAll: document.querySelector("#xSelectAll"),
  xLocalGrid: document.querySelector("#xLocalGrid"),
  xLocalSummary: document.querySelector("#xLocalSummary"),
  xLocalSelectAll: document.querySelector("#xLocalSelectAll"),
  xhsNavButton: document.querySelector("#xiaohongshuNavButton"),
  xhsTarget: document.querySelector("#xhsTargetInput"),
  xhsLimit: document.querySelector("#xhsLimitInput"),
  xhsGrid: document.querySelector("#xhsGrid"),
  xhsSummary: document.querySelector("#xhsSummary"),
  xhsSelectAll: document.querySelector("#xhsSelectAll"),
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
    els.grid.innerHTML = `<div class="empty">还没有作品。可以先下载作品，或点击“扫描旧目录”导入已有文件。</div>`;
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

async function syncFollowed() {
  setStatus("正在同步关注作者...");
  const result = await api("/api/sync/followed", {
    method: "POST",
    body: JSON.stringify({ download: true, max_new: 20 }),
  });
  const downloaded = result.artists.reduce((sum, item) => sum + (item.downloaded || 0), 0);
  setStatus(`同步完成：下载 ${downloaded} 个新作品`);
  await Promise.all([loadFollowed(), loadTags(), reloadCurrent()]);
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
  const hasXhs = pages.some((item) => item.view === "xiaohongshu");
  els.xhsNavButton.classList.toggle("hidden", !hasXhs);
}

async function importCookieFile(platform, fileInput) {
  const file = fileInput.files?.[0];
  if (!file) {
    setStatus("请先选择 cookies.txt 或 json 文件。");
    return;
  }
  setStatus(`正在导入 ${platform} Cookie...`);
  const text = await file.text();
  const result = await api("/api/cookies/import", {
    method: "POST",
    body: JSON.stringify({ platform, text }),
  });
  setStatus(`Cookie 导入完成：${result.summary || `${result.count || 0} 个 Cookie`}`);
}

async function previewXMedia(event) {
  event.preventDefault();
  const username = els.xUsername.value.trim();
  if (!username) {
    setStatus("请输入 X 作者名称。");
    return;
  }
  els.xMediaGrid.innerHTML = `<div class="empty">正在解析 X 媒体页，首次启动 Playwright 可能需要一些时间。</div>`;
  els.xMediaSummary.textContent = "解析中...";
  setStatus(`正在解析 X 作者 @${username.replace(/^@/, "")}...`);
  const payload = await api("/api/x/media/preview", {
    method: "POST",
    body: JSON.stringify({
      username,
      media_type: els.xMediaType.value,
      max_items: Number(els.xMaxItems.value || 120),
    }),
  });
  state.xPreviewId = payload.preview_id;
  renderXMedia(payload);
  setStatus(`X 解析完成：${payload.count} 个媒体项`);
}

function renderXMedia(payload) {
  els.xMediaGrid.innerHTML = "";
  els.xMediaSummary.textContent = `${payload.username} · ${payload.count} 个媒体项`;
  if (!payload.items.length) {
    els.xMediaGrid.innerHTML = `<div class="empty">没有解析到媒体。可能需要先导入 X Cookie，或该作者媒体页为空。</div>`;
    return;
  }
  els.xMediaGrid.append(...payload.items.map(renderXMediaCard));
  els.xSelectAll.checked = true;
}

function renderXMediaCard(item) {
  const card = document.createElement("article");
  card.className = "x-media-card";
  const preview = item.thumbnail || item.files?.[0]?.url || "";
  const media = item.kind === "video"
    ? `<div class="video-tile">VIDEO</div>`
    : `<img loading="lazy" referrerpolicy="no-referrer" src="${escapeHtml(preview)}" alt="${escapeHtml(item.title || item.id)}">`;
  card.innerHTML = `
    <label class="x-check">
      <input type="checkbox" data-x-id="${escapeHtml(item.id)}" checked />
      <span>${item.kind === "video" ? "视频" : "图片"}</span>
    </label>
    <div class="x-thumb">${media}</div>
    <div class="artwork-meta">
      <div class="artwork-title">${escapeHtml(item.title || item.id)}</div>
      <div class="artwork-sub">@${escapeHtml(item.author_id || "")} · ${item.file_count || 1} 个文件</div>
    </div>
  `;
  return card;
}

async function downloadXSelected() {
  if (!state.xPreviewId) {
    setStatus("请先解析 X 作者媒体。");
    return;
  }
  const selected = [...document.querySelectorAll("[data-x-id]:checked")].map((item) => item.dataset.xId);
  if (!selected.length) {
    setStatus("没有选中的媒体。");
    return;
  }
  setStatus(`正在下载 ${selected.length} 个 X 媒体项...`);
  const result = await api("/api/x/media/download", {
    method: "POST",
    body: JSON.stringify({ preview_id: state.xPreviewId, selected_ids: selected }),
  });
  setStatus(`X 下载完成：保存 ${result.downloaded_files} 个文件，失败 ${result.failed_items} 项`);
  await loadXLocalMedia();
}

async function loadXLocalMedia() {
  const payload = await api("/api/x/local-media?limit=160");
  els.xLocalGrid.innerHTML = "";
  els.xLocalSummary.textContent = `本地 ${payload.total || 0} 个文件`;
  if (!payload.items.length) {
    els.xLocalGrid.innerHTML = `<div class="empty">还没有本地 X 图片或视频。</div>`;
    return;
  }
  els.xLocalGrid.append(...payload.items.map(renderXLocalCard));
}

function renderXLocalCard(item) {
  const card = document.createElement("article");
  card.className = "x-media-card";
  const media = item.kind === "video"
    ? `<video src="${escapeHtml(item.url)}" muted preload="metadata" controls></video>`
    : `<img loading="lazy" src="${escapeHtml(item.url)}" alt="${escapeHtml(item.name)}">`;
  card.innerHTML = `
    <label class="x-check">
      <input type="checkbox" data-x-local-id="${escapeHtml(item.id)}" />
      <span>${item.kind === "video" ? "视频" : "图片"}</span>
    </label>
    <div class="x-thumb">${media}</div>
    <div class="artwork-meta">
      <div class="artwork-title">${escapeHtml(item.name)}</div>
      <div class="artwork-sub">${escapeHtml(item.relative_path)} · ${formatBytes(item.size)}</div>
    </div>
  `;
  return card;
}

async function deleteXLocalSelected() {
  const selected = [...document.querySelectorAll("[data-x-local-id]:checked")].map((item) => item.dataset.xLocalId);
  if (!selected.length) {
    setStatus("没有选中的本地 X 文件。");
    return;
  }
  setStatus(`正在删除 ${selected.length} 个本地 X 文件...`);
  const result = await api("/api/x/local-media/delete", {
    method: "POST",
    body: JSON.stringify({ ids: selected }),
  });
  setStatus(`删除完成：${result.deleted.length} 个，失败 ${result.failed.length} 个`);
  els.xLocalSelectAll.checked = false;
  await loadXLocalMedia();
}

async function previewXhs(event) {
  event.preventDefault();
  const target = els.xhsTarget.value.trim();
  if (!target) {
    setStatus("请输入小红书关键词或笔记链接。");
    return;
  }
  els.xhsGrid.innerHTML = `<div class="empty">正在解析小红书内容...</div>`;
  els.xhsSummary.textContent = "解析中...";
  const payload = await api("/api/xiaohongshu/preview", {
    method: "POST",
    body: JSON.stringify({ target, limit: Number(els.xhsLimit.value || 20) }),
  });
  state.xhsPreviewId = payload.preview_id;
  renderXhs(payload);
  setStatus(`小红书解析完成：${payload.count} 个项目`);
}

function renderXhs(payload) {
  els.xhsGrid.innerHTML = "";
  els.xhsSummary.textContent = `${payload.count} 个项目`;
  if (!payload.items.length) {
    els.xhsGrid.innerHTML = `<div class="empty">没有解析到内容，可能需要先登录或换一个关键词。</div>`;
    return;
  }
  els.xhsGrid.append(...payload.items.map(renderXhsCard));
  els.xhsSelectAll.checked = true;
}

function renderXhsCard(item) {
  const card = document.createElement("article");
  card.className = "x-media-card";
  const preview = item.thumbnail || item.metadata?.cover_url || "";
  const media = preview
    ? `<img loading="lazy" referrerpolicy="no-referrer" src="${escapeHtml(preview)}" alt="${escapeHtml(item.title || item.id)}">`
    : `<div class="thumb-placeholder">暂无预览</div>`;
  card.innerHTML = `
    <label class="x-check">
      <input type="checkbox" data-xhs-id="${escapeHtml(item.id)}" checked />
      <span>笔记</span>
    </label>
    <div class="x-thumb">${media}</div>
    <div class="artwork-meta">
      <div class="artwork-title">${escapeHtml(item.title || item.id)}</div>
      <div class="artwork-sub">${escapeHtml(item.author || "")} · ${item.file_count || 0} 个文件</div>
    </div>
  `;
  return card;
}

async function downloadXhsSelected() {
  if (!state.xhsPreviewId) {
    setStatus("请先解析小红书内容。");
    return;
  }
  const selected = [...document.querySelectorAll("[data-xhs-id]:checked")].map((item) => item.dataset.xhsId);
  if (!selected.length) {
    setStatus("没有选中的小红书项目。");
    return;
  }
  setStatus(`正在下载 ${selected.length} 个小红书项目...`);
  const result = await api("/api/xiaohongshu/download", {
    method: "POST",
    body: JSON.stringify({ preview_id: state.xhsPreviewId, selected_ids: selected }),
  });
  setStatus(`小红书下载完成：保存 ${result.downloaded_files} 个文件，失败 ${result.failed_items} 项`);
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
  } else if (view === "xMedia") {
    document.querySelector("#xMediaView").classList.add("active");
  } else if (view === "xiaohongshu") {
    document.querySelector("#xiaohongshuView").classList.add("active");
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
  } else if (state.view === "xMedia") {
    await loadXLocalMedia();
    setStatus("X 作者媒体页已就绪。");
  } else if (state.view === "xiaohongshu") {
    setStatus("小红书插件页已就绪。");
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

function formatBytes(value) {
  const size = Number(value || 0);
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

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
document.querySelector("#syncButton").addEventListener("click", syncFollowed);
document.querySelector("#buildThumbsButton").addEventListener("click", buildThumbnails);
document.querySelector("#closeDialogButton").addEventListener("click", () => els.dialog.close());
document.querySelector("#importPixivCookieButton").addEventListener("click", () => importCookieFile("pixiv", els.pixivCookieFile));
document.querySelector("#importXCookieButton").addEventListener("click", () => importCookieFile("x", els.xCookieFile));
document.querySelector("#xMediaForm").addEventListener("submit", previewXMedia);
document.querySelector("#downloadXSelectedButton").addEventListener("click", downloadXSelected);
document.querySelector("#refreshXLocalButton").addEventListener("click", loadXLocalMedia);
document.querySelector("#deleteXLocalButton").addEventListener("click", deleteXLocalSelected);
document.querySelector("#xhsForm").addEventListener("submit", previewXhs);
document.querySelector("#downloadXhsSelectedButton").addEventListener("click", downloadXhsSelected);
els.xSelectAll.addEventListener("change", () => {
  document.querySelectorAll("[data-x-id]").forEach((item) => {
    item.checked = els.xSelectAll.checked;
  });
});
els.xLocalSelectAll.addEventListener("change", () => {
  document.querySelectorAll("[data-x-local-id]").forEach((item) => {
    item.checked = els.xLocalSelectAll.checked;
  });
});
els.xhsSelectAll.addEventListener("change", () => {
  document.querySelectorAll("[data-xhs-id]").forEach((item) => {
    item.checked = els.xhsSelectAll.checked;
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
