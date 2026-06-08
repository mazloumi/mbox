const labelList = document.getElementById("label-list");
const messageList = document.getElementById("message-list");
const readerHeader = document.getElementById("reader-header");
const readerAtt = document.getElementById("reader-attachments");
const readerBody = document.getElementById("reader-body");
const readerPdf = document.getElementById("reader-pdf");
const tabFolders = document.getElementById("tab-folders");
const tabFiles = document.getElementById("tab-files");
const readerText = document.getElementById("reader-text");
const readerImage = document.getElementById("reader-image");
const readerAudio = document.getElementById("reader-audio");
const readerVideo = document.getElementById("reader-video");
const readerTable = document.getElementById("reader-table");
const READER_PANES = [readerBody, readerPdf, readerText, readerImage, readerAudio, readerVideo, readerTable];
const appEl = document.getElementById("app");
const archiveBtn = document.getElementById("archive-images");
const mboxNameEl = document.getElementById("mbox-name");
const indexStateEl = document.getElementById("index-state");
const archiveStateEl = document.getElementById("archive-state");
const q = document.getElementById("q");
const filterFrom = document.getElementById("filter-from");
const filterTo = document.getElementById("filter-to");
const filterSender = document.getElementById("filter-sender");
const filterHasAtt = document.getElementById("filter-hasatt");
const filterSort = document.getElementById("filter-sort");
const downloadAllBtn = document.getElementById("download-all");
const integrityEl = document.getElementById("integrity");

const PAGE_SIZE = 50;
let activeLabel = null;
let browseMode = "folders";   // "folders" | "files"
let activeCategory = null;
let currentQuery = "";
let currentPage = 1;
let currentOpenId = null;

async function getJSON(url) {
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
  return resp.json();
}

function escapeHtml(s) {
  return (s || "").replace(/[&<>"]/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

// Escape text, then wrap each active query term in <mark>. Escaping happens
// BEFORE wrapping so email-controlled text can never inject markup.
function highlight(text) {
  const safe = escapeHtml(text);
  const terms = (currentQuery || "").split(/\s+/).filter(Boolean);
  if (terms.length === 0) return safe;
  const pattern = terms.map(t => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|");
  return safe.replace(new RegExp(pattern, "gi"), m => `<mark>${m}</mark>`);
}

function humanSize(bytes) {
  const b = Number(bytes) || 0;
  if (b < 1024) return b + " B";
  if (b < 1024 * 1024) return (b / 1024).toFixed(0) + " KB";
  return (b / 1024 / 1024).toFixed(1) + " MB";
}

function refreshLeft() {
  if (browseMode === "files") loadCategories();
  else loadLabels();
}

async function loadCategories() {
  try {
    const cats = await getJSON("/api/filetypes");
    labelList.innerHTML = "";
    for (const c of cats) {
      const li = document.createElement("li");
      li.innerHTML = `${escapeHtml(c.category)}<span class="count">${escapeHtml(String(c.count))}</span>`;
      li.onclick = () => { activeCategory = c.category; setActive(labelList, li); reload(); };
      labelList.appendChild(li);
    }
  } catch (err) {
    labelList.innerHTML = `<li>Failed to load file types: ${escapeHtml(String(err.message))}</li>`;
  }
}

async function loadLabels() {
  try {
    const labels = await getJSON("/api/labels");
    labelList.innerHTML = "";
    for (const l of labels) {
      const li = document.createElement("li");
      li.innerHTML = `${escapeHtml(l.name)}<span class="count">${escapeHtml(String(l.count))}</span>`;
      li.onclick = () => { activeLabel = l.name; setActive(labelList, li); reload(); };
      labelList.appendChild(li);
    }
  } catch (err) {
    labelList.innerHTML = `<li>Failed to load folders: ${escapeHtml(String(err.message))}</li>`;
  }
}

function setActive(container, el) {
  container.querySelectorAll("li").forEach(x => x.classList.remove("active"));
  el.classList.add("active");
}

function pageUrl(page) {
  const params = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) });
  if (browseMode === "files") {
    if (activeCategory) params.set("category", activeCategory);
    if (currentQuery) params.set("q", currentQuery);
    return `/api/files?${params.toString()}`;
  }
  if (activeLabel) params.set("label", activeLabel);
  if (filterFrom.value) params.set("date_from", filterFrom.value);
  if (filterTo.value) params.set("date_to", filterTo.value);
  if (filterSender.value.trim()) params.set("from_q", filterSender.value.trim());
  if (filterHasAtt.checked) params.set("has_attachment", "1");
  if (filterSort.value) params.set("sort", filterSort.value);
  if (currentQuery) { params.set("q", currentQuery); return `/api/search?${params.toString()}`; }
  return `/api/messages?${params.toString()}`;
}

function isGallery() {
  return browseMode === "files" && activeCategory === "Images";
}

function appendRows(items) {
  messageList.classList.toggle("gallery", isGallery());
  for (const it of items) {
    const li = document.createElement("li");
    if (browseMode === "files") {
      if (isGallery()) {
        li.innerHTML = `<img class="thumb" loading="lazy" src="/api/messages/${it.message_id}/attachments/${it.idx}?inline=1" title="${escapeHtml(it.filename || "")}">`;
      } else {
        li.innerHTML = `<div class="subject">${escapeHtml(it.filename || "(no name)")}</div>
          <div class="meta">${escapeHtml(it.subject || "")} — ${humanSize(it.size)}</div>`;
      }
      li.onclick = () => { setActive(messageList, li); openFile(it.message_id, it.idx, it.filename, it.mime, it.size); };
    } else {
      li.innerHTML = `<div class="subject">${highlight(it.subject || "(no subject)")}</div>
        <div class="meta">${escapeHtml(it.from || "")} — ${escapeHtml((it.date || "").slice(0, 10))}</div>
        <div class="preview">${highlight(it.preview || "")}</div>`;
      li.onclick = () => { setActive(messageList, li); openMessage(it.id); };
    }
    messageList.appendChild(li);
  }
  renderLoadMore(items.length);
}

// --- Infinite scroll: auto-load the next page near the bottom of the list ---
let loadingMore = false;
let noMorePages = false;
let loadGeneration = 0;   // bumped on every reload; stale in-flight fetches discard their result

const loadSentinel = document.createElement("li");
loadSentinel.id = "load-more";            // id kept so arrow-key nav excludes it
loadSentinel.className = "load-more";

// Observe a bottom sentinel within the scrolling list pane; fire ~400px early.
const moreObserver = ("IntersectionObserver" in window)
  ? new IntersectionObserver((entries) => {
      if (entries.some(e => e.isIntersecting)) loadNextPage();
    }, { root: document.getElementById("list"), rootMargin: "400px" })
  : null;

function renderLoadMore(lastCount) {
  if (lastCount === PAGE_SIZE) {
    noMorePages = false;
    loadSentinel.textContent = moreObserver ? "Loading…" : "Load more…";
    messageList.appendChild(loadSentinel);   // move to the bottom
    if (moreObserver) {
      // Re-observe to force a fresh intersection check, so a list that doesn't
      // fill the viewport keeps loading until the API returns a short page.
      moreObserver.unobserve(loadSentinel);
      moreObserver.observe(loadSentinel);
    } else {
      loadSentinel.onclick = loadNextPage;   // fallback when IO is unavailable
    }
  } else {
    noMorePages = true;
    if (moreObserver) moreObserver.unobserve(loadSentinel);
    if (loadSentinel.parentNode) loadSentinel.remove();
  }
}

async function reload() {
  const gen = ++loadGeneration;            // supersede any in-flight page fetch
  currentPage = 1;
  loadingMore = false;
  noMorePages = false;
  messageList.innerHTML = "";              // detaches the sentinel; re-added by appendRows
  messageList.classList.toggle("gallery", isGallery());
  updateDownloadAll();
  if (browseMode === "files" && !activeCategory && !currentQuery) return;  // need a category or a query
  try {
    const data = await getJSON(pageUrl(1));
    if (gen !== loadGeneration) return;    // a newer reload took over
    appendRows(data.messages || data.files || []);
  } catch (err) {
    if (gen !== loadGeneration) return;
    messageList.innerHTML = `<li>Failed to load: ${escapeHtml(String(err.message))}</li>`;
  }
}

async function loadNextPage() {
  if (loadingMore || noMorePages) return;
  loadingMore = true;
  const gen = loadGeneration;
  currentPage += 1;
  try {
    const data = await getJSON(pageUrl(currentPage));
    if (gen !== loadGeneration) return;    // context switched mid-fetch — drop stale rows
    appendRows(data.messages || data.files || []);
  } catch (err) {
    if (gen !== loadGeneration) return;
    noMorePages = true;
    if (moreObserver) moreObserver.unobserve(loadSentinel);
    if (loadSentinel.parentNode) loadSentinel.remove();
  } finally {
    if (gen === loadGeneration) loadingMore = false;   // newer reload already reset state
  }
}

// Show exactly one reader pane; hide the rest and stop/clear their content
// (so switching files stops audio/video and frees iframes).
function showOnlyPane(el) {
  for (const p of READER_PANES) {
    if (p === el) { p.hidden = false; continue; }
    p.hidden = true;
    if (p === readerAudio || p === readerVideo) {
      try { p.pause(); } catch (e) { /* ignore */ }
      p.removeAttribute("src"); p.load();
    } else if (p === readerPdf || p === readerImage) {
      p.removeAttribute("src");
    } else if (p === readerBody) {
      p.srcdoc = "";
    } else if (p === readerTable) {
      p.innerHTML = "";
    } else if (p === readerText) {
      p.textContent = "";
    }
  }
}

function viewPdf(id, idx) {
  showOnlyPane(readerPdf);
  readerPdf.src = `/api/messages/${id}/attachments/${idx}?inline=1`;
}

function parseCsv(text) {
  const rows = []; let row = [], field = "", q = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (q) {
      if (c === '"') { if (text[i + 1] === '"') { field += '"'; i++; } else { q = false; } }
      else { field += c; }
    } else if (c === '"') { q = true; }
    else if (c === ",") { row.push(field); field = ""; }
    else if (c === "\n") { row.push(field); rows.push(row); row = []; field = ""; }
    else if (c !== "\r") { field += c; }
  }
  if (field !== "" || row.length) { row.push(field); rows.push(row); }
  return rows.filter(r => r.length > 1 || (r.length === 1 && r[0] !== ""));
}

function renderTableRows(rows) {
  const CAP = 500;
  const shown = rows.slice(0, CAP + 1); // +1 header
  const head = shown[0] || [];
  const body = shown.slice(1);
  const th = head.map(c => `<th>${escapeHtml(c)}</th>`).join("");
  const trs = body.map(r => "<tr>" + r.map(c => `<td>${escapeHtml(c)}</td>`).join("") + "</tr>").join("");
  let html = `<table><thead><tr>${th}</tr></thead><tbody>${trs}</tbody></table>`;
  if (rows.length - 1 > CAP) html += `<p class="csv-note">Showing first ${CAP} of ${rows.length - 1} rows.</p>`;
  return html;
}

function renderCsvTable(text) { return renderTableRows(parseCsv(text)); }

function parseTsv(text) {
  return text.split("\n").filter(l => l !== "").map(l => l.split("\t"));
}

const _SPREADSHEET_MIMES = new Set([
  "application/vnd.ms-excel",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "application/vnd.oasis.opendocument.spreadsheet",
  "application/x-msexcel", "application/msexcel", "application/excel",
]);
function isSpreadsheet(m, name) {
  return _SPREADSHEET_MIMES.has(m) || /\.(xls|xlsx|ods|xlsm)$/.test(name);
}

const _UNPLAYABLE_MIMES = new Set([
  "audio/x-ms-wma", "video/x-ms-wmv", "video/x-ms-asf", "audio/x-ms-wax",
]);
function isUnplayable(m, name) {
  return _UNPLAYABLE_MIMES.has(m) || /\.(wma|wmv|asf)$/.test(name);
}

const _ARCHIVE_MIMES = new Set([
  "application/zip", "application/x-zip-compressed", "application/java-archive",
  "application/gzip", "application/x-gzip", "application/x-tar", "application/x-gtar",
  "application/x-bzip-compressed-tar",
]);
function isArchive(m, name) {
  return _ARCHIVE_MIMES.has(m) || /\.(zip|jar|war|ear|tar|tgz|tbz2|tar\.gz|tar\.bz2|gz|bz2)$/.test(name);
}

async function openFile(mid, idx, filename, mime, size) {
  currentOpenId = mid;
  const m = (mime || "").toLowerCase();
  const name = (filename || "").toLowerCase();
  const inlineUrl = `/api/messages/${mid}/attachments/${idx}?inline=1`;
  readerHeader.innerHTML = `<div class="subject">${escapeHtml(filename || "(no name)")}</div>
    <div class="meta">${escapeHtml(mime || "")} · ${humanSize(size)}</div>`;
  readerAtt.innerHTML =
    `<a href="/api/messages/${mid}/attachments/${idx}" download>Download</a>` +
    ` <button type="button" class="open-email" onclick="openEmailFromFile(${mid})">Open email</button>`;
  // Windows Media can't be decoded by any browser — catch it by MIME OR extension
  // (these files often arrive as application/octet-stream) before trying a player.
  if (isUnplayable(m, name)) {
    showOnlyPane(readerText);
    readerText.textContent = "This format (Windows Media) can't be played in the browser. Use the Download link above.";
    return;
  }
  if (m.startsWith("image/")) { showOnlyPane(readerImage); readerImage.src = inlineUrl; return; }
  if (m.startsWith("audio/") || m.startsWith("video/")) {
    const pane = m.startsWith("audio/") ? readerAudio : readerVideo;
    showOnlyPane(pane);
    pane.onerror = () => {
      showOnlyPane(readerText);
      readerText.textContent = "This file couldn't be played in the browser. Use the Download link above.";
    };
    pane.src = inlineUrl;
    return;
  }
  if (m === "text/csv" || name.endsWith(".csv")) {
    showOnlyPane(readerTable);
    readerTable.innerHTML = "Loading…";
    try {
      const d = await getJSON(`/api/files/${mid}/${idx}/text`);
      readerTable.innerHTML = (d.text && d.text.trim())
        ? renderCsvTable(d.text) : "No content.";
    } catch (err) {
      readerTable.textContent = "Failed to load file: " + err.message;
    }
    return;
  }
  if (isSpreadsheet(m, name)) {
    showOnlyPane(readerTable);
    readerTable.innerHTML = "Loading…";
    try {
      const d = await getJSON(`/api/files/${mid}/${idx}/text`);
      readerTable.innerHTML = (d.text && d.text.trim()) ? renderTableRows(parseTsv(d.text)) : "No content.";
    } catch (err) {
      readerTable.textContent = "Failed to load file: " + err.message;
    }
    return;
  }
  if (isArchive(m, name)) {
    showOnlyPane(readerTable);
    readerTable.innerHTML = "Loading…";
    try {
      const d = await getJSON(`/api/files/${mid}/${idx}/text`);
      readerTable.innerHTML = (d.text && d.text.trim()) ? renderTableRows(parseTsv(d.text)) : "No files listed.";
    } catch (err) {
      readerTable.textContent = "Failed to load archive: " + err.message;
    }
    return;
  }
  if (m === "application/ms-tnef" || name.endsWith(".dat")) {
    showOnlyPane(readerText);
    readerText.textContent = "Loading…";
    let contained = "";
    try {
      const list = await getJSON(`/api/messages/${mid}/attachments/${idx}/inner`);
      if (list.files && list.files.length) {
        contained = " · Contained: " + list.files.map(f =>
          `<a href="/api/messages/${mid}/attachments/${idx}/inner/${f.k}" download>${escapeHtml(f.name)} (${humanSize(f.size)})</a>`
        ).join(" ");
      }
    } catch (e) { /* ignore — still show text */ }
    readerAtt.innerHTML += contained;
    try {
      const d = await getJSON(`/api/files/${mid}/${idx}/text`);
      readerText.textContent = (d.text && d.text.trim()) ? d.text : "No extractable content.";
    } catch (err) {
      readerText.textContent = "Failed to load: " + err.message;
    }
    return;
  }
  showOnlyPane(readerText);
  readerText.textContent = "Loading…";
  try {
    const d = await getJSON(`/api/files/${mid}/${idx}/text`);
    readerText.textContent = (d.text && d.text.trim())
      ? d.text : "No extractable text for this file type.";
  } catch (err) {
    readerText.textContent = "Failed to load file text: " + err.message;
  }
}

function openEmailFromFile(mid) {
  setMode("folders");
  openMessage(mid);
}

async function openMessage(id, allowRemote = false) {
  currentOpenId = id;
  showOnlyPane(readerBody);
  try {
    const m = await getJSON(`/api/messages/${id}?allow_remote=${allowRemote}`);
    const remoteBtn = allowRemote ? "" : `<button id="load-remote" type="button">Load remote images</button>`;
    const emlLink = `<a class="eml-link" href="/api/messages/${id}/raw" download>Download .eml</a>`;
    readerHeader.innerHTML = `<div class="subject">${escapeHtml(m.subject || "(no subject)")}</div>
      <div class="meta">From: ${escapeHtml(m.from || "")}<br>To: ${escapeHtml(m.to || "")}<br>${escapeHtml(m.date || "")}</div>
      ${remoteBtn}${emlLink}`;
    readerAtt.innerHTML = (m.attachments || []).map(a => {
      const dl = `<a href="/api/messages/${id}/attachments/${a.idx}" download>${escapeHtml(a.filename)} (${escapeHtml(String(a.size))}b)</a>`;
      const view = a.mime === "application/pdf"
        ? ` <button type="button" class="view-pdf" onclick="viewPdf(${id}, ${a.idx})">View</button>` : "";
      return `<span class="att">${dl}${view}</span>`;
    }).join("");
    // Blocked remote images have an empty src (the backend blanks them); hide them
    // so the reader shows text instead of broken-image icons. "Load remote images"
    // re-fetches with real (non-empty) src values, which this rule does not match.
    readerBody.srcdoc = '<style>img[src=""], img:not([src]) { display: none }</style>' + m.body_html;
    const btn = document.getElementById("load-remote");
    if (btn) btn.onclick = () => openMessage(id, true);
  } catch (err) {
    readerHeader.innerHTML = `<div class="meta">Failed to open message: ${escapeHtml(String(err.message))}</div>`;
    readerAtt.innerHTML = "";
    readerBody.srcdoc = "";
  }
}

let pollTick = 0;

async function pollStatus() {
  try {
    const s = await getJSON("/api/status");
    if (s.mbox) mboxNameEl.textContent = "📁 " + s.mbox;
    if (s.error) {
      indexStateEl.className = "err";
      indexStateEl.textContent = "Indexing failed: " + s.error;
      return;
    }
    indexStateEl.className = "";
    if (s.indexing) {
      indexStateEl.textContent =
        `Indexing… ${s.percent}% · ${Number(s.messages).toLocaleString()} messages`;
      if (pollTick % 5 === 0) {
        refreshLeft();
        if (currentOpenId === null) reload();
      }
      pollTick += 1;
      setTimeout(pollStatus, 2000);
    } else {
      indexStateEl.textContent = s.current
        ? `Indexed ${Number(s.messages).toLocaleString()} messages`
        : "⚠ Source changed — restart to re-index";
      refreshLeft();
      if (currentOpenId === null) reload();
    }
  } catch (e) {
    indexStateEl.textContent = "Status unavailable";
    setTimeout(pollStatus, 3000);
  }
}

let searchTimer;
q.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { currentQuery = q.value.trim(); reload(); }, 250);
});

// --- Search filters: re-run the list whenever any filter control changes ---
for (const el of [filterFrom, filterTo, filterSender, filterHasAtt, filterSort]) {
  el.addEventListener("change", () => reload());
}

// --- Bulk export: show "Download all" in Files mode when scoped ---
function updateDownloadAll() {
  const show = browseMode === "files" && (activeCategory || currentQuery);
  downloadAllBtn.hidden = !show;
}
downloadAllBtn.addEventListener("click", () => {
  window.location = "/api/files/export?" +
    new URLSearchParams({ category: activeCategory || "", q: currentQuery || "" }).toString();
});

// --- Integrity footer: indexed/skipped counts (best-effort) ---
async function loadIntegrity() {
  try {
    const d = await getJSON("/api/integrity");
    const n = (x) => Number(x).toLocaleString();
    integrityEl.textContent = `Index: ${n(d.indexed)} indexed · ${n(d.skipped)} skipped`;
    if (d.skipped > 0) {
      integrityEl.classList.add("warn");
      const sample = (d.sample || []).slice(0, 5)
        .map(s => typeof s === "string" ? s : (s.reason || JSON.stringify(s)));
      if (sample.length) integrityEl.title = sample.join("\n");
    }
  } catch (e) { /* ignore — footer stays empty */ }
}
loadIntegrity();

// --- Folders/Files mode tabs + collapse (persisted) ---
function toggleCollapse() {
  const collapsed = !appEl.classList.contains("folders-collapsed");
  appEl.classList.toggle("folders-collapsed", collapsed);
  try { localStorage.setItem("foldersCollapsed", collapsed ? "1" : "0"); } catch (e) { /* ignore */ }
}

function setMode(mode) {
  browseMode = mode;
  currentOpenId = null;
  activeCategory = null;
  activeLabel = null;
  tabFolders.classList.toggle("active", mode === "folders");
  tabFiles.classList.toggle("active", mode === "files");
  currentQuery = "";
  q.value = "";
  appEl.classList.remove("folders-collapsed");
  readerHeader.innerHTML = "";
  readerAtt.innerHTML = "";
  showOnlyPane(mode === "files" ? null : readerBody);
  readerBody.srcdoc = "";
  updateDownloadAll();
  refreshLeft();
  reload();
}

tabFolders.addEventListener("click", () => {
  if (browseMode !== "folders") setMode("folders"); else toggleCollapse();
});
tabFiles.addEventListener("click", () => {
  if (browseMode !== "files") setMode("files"); else toggleCollapse();
});
try {
  if (localStorage.getItem("foldersCollapsed") === "1") appEl.classList.add("folders-collapsed");
} catch (e) { /* ignore */ }

// --- Keyboard shortcuts + arrow-key navigation between emails ---
document.addEventListener("keydown", (e) => {
  const tag = (document.activeElement && document.activeElement.tagName) || "";
  const typing = tag === "INPUT" || tag === "TEXTAREA";
  // Escape blurs the search box even while typing in it.
  if (e.key === "Escape" && document.activeElement === q) { q.blur(); return; }
  if (typing) return;  // don't hijack typing in inputs
  // "/" focuses the search box.
  if (e.key === "/") { e.preventDefault(); q.focus(); return; }
  // j/k mirror ArrowDown/ArrowUp; everything else (besides arrows) is ignored.
  const down = e.key === "ArrowDown" || e.key === "j";
  const up = e.key === "ArrowUp" || e.key === "k";
  if (!down && !up) return;
  const items = Array.from(messageList.querySelectorAll("li")).filter(li => li.id !== "load-more");
  if (items.length === 0) return;
  const current = messageList.querySelector("li.active");
  let idx = current ? items.indexOf(current) : -1;
  idx = down ? Math.min(idx + 1, items.length - 1) : Math.max(idx - 1, 0);
  const target = items[idx];
  if (target) {
    e.preventDefault();
    target.click();  // reuses the row handler: setActive + openMessage
    target.scrollIntoView({ block: "nearest" });
  }
});

// --- Opt-in remote-image archiving ---
async function pollArchive() {
  try {
    const s = await getJSON("/api/archive/status");
    const n = (x) => Number(x).toLocaleString();
    if (s.error) {
      archiveStateEl.className = "err";
      archiveStateEl.textContent = "Archive failed: " + s.error;
      archiveBtn.disabled = false;
      return;
    }
    archiveStateEl.className = "";
    if (s.running) {
      archiveBtn.disabled = true;
      archiveStateEl.textContent =
        `Archiving images… ${n(s.messages_scanned)}/${n(s.total_messages)} · ` +
        `${n(s.downloaded)} saved · ${n(s.skipped)} skipped · ${n(s.failed)} failed`;
      setTimeout(pollArchive, 2000);
    } else {
      archiveBtn.disabled = false;
      const a = s.archived || { ok: 0, skipped: 0, failed: 0, gave_up: 0, total: 0 };
      if (!a.total) {
        archiveStateEl.textContent = "Images: not archived yet";
      } else {
        const breakdown = `Images: ${n(a.total)} total · ${n(a.ok)} archived · ` +
          `${n(a.skipped)} skipped · ${n(a.failed)} failed · ${n(a.gave_up)} unreachable`;
        archiveStateEl.textContent = breakdown + (s.up_to_date ? " ✓" : " · click to update");
      }
    }
  } catch (e) {
    archiveStateEl.textContent = "Images: status unavailable";
    setTimeout(pollArchive, 3000);
  }
}

archiveBtn.addEventListener("click", async () => {
  const ok = confirm(
    "Archive remote images for offline viewing?\n\n" +
    "This downloads images from senders' servers so they display even when you're " +
    "offline or the images are later removed. It may signal to senders that these " +
    "emails were opened (your IP and the time). Tracking pixels are skipped.\n\nProceed?");
  if (!ok) return;
  try { await fetch("/api/archive/start", { method: "POST" }); } catch (e) { /* ignore */ }
  pollArchive();
});

pollArchive();  // reflect any in-progress/finished archive on load

pollStatus();
