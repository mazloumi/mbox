const labelList = document.getElementById("label-list");
const messageList = document.getElementById("message-list");
const readerHeader = document.getElementById("reader-header");
const readerAtt = document.getElementById("reader-attachments");
const readerBody = document.getElementById("reader-body");
const readerPdf = document.getElementById("reader-pdf");
const tabFolders = document.getElementById("tab-folders");
const tabFiles = document.getElementById("tab-files");
const readerText = document.getElementById("reader-text");
const searchbar = document.getElementById("searchbar");
const appEl = document.getElementById("app");
const archiveBtn = document.getElementById("archive-images");
const mboxNameEl = document.getElementById("mbox-name");
const indexStateEl = document.getElementById("index-state");
const archiveStateEl = document.getElementById("archive-state");
const q = document.getElementById("q");

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
    params.set("category", activeCategory || "");
    return `/api/files?${params.toString()}`;
  }
  if (activeLabel) params.set("label", activeLabel);
  if (currentQuery) { params.set("q", currentQuery); return `/api/search?${params.toString()}`; }
  return `/api/messages?${params.toString()}`;
}

function appendRows(items) {
  for (const it of items) {
    const li = document.createElement("li");
    if (browseMode === "files") {
      li.innerHTML = `<div class="subject">${escapeHtml(it.filename || "(no name)")}</div>
        <div class="meta">${escapeHtml(it.subject || "")} — ${humanSize(it.size)}</div>`;
      li.onclick = () => { setActive(messageList, li); openFile(it.message_id, it.idx, it.filename, it.mime, it.size); };
    } else {
      li.innerHTML = `<div class="subject">${escapeHtml(it.subject || "(no subject)")}</div>
        <div class="meta">${escapeHtml(it.from || "")} — ${escapeHtml((it.date || "").slice(0, 10))}</div>`;
      li.onclick = () => { setActive(messageList, li); openMessage(it.id); };
    }
    messageList.appendChild(li);
  }
  renderLoadMore(items.length);
}

function renderLoadMore(lastCount) {
  const existing = document.getElementById("load-more");
  if (existing) existing.remove();
  if (lastCount === PAGE_SIZE) {
    const li = document.createElement("li");
    li.id = "load-more";
    li.textContent = "Load more…";
    li.onclick = loadNextPage;
    messageList.appendChild(li);
  }
}

async function reload() {
  currentPage = 1;
  messageList.innerHTML = "";
  if (browseMode === "files" && !activeCategory) return;  // pick a category first
  try {
    const data = await getJSON(pageUrl(1));
    appendRows(data.messages || data.files || []);
  } catch (err) {
    messageList.innerHTML = `<li>Failed to load: ${escapeHtml(String(err.message))}</li>`;
  }
}

async function loadNextPage() {
  currentPage += 1;
  try {
    const data = await getJSON(pageUrl(currentPage));
    appendRows(data.messages || data.files || []);
  } catch (err) {
    renderLoadMore(0);
  }
}

function viewPdf(id, idx) {
  readerPdf.src = `/api/messages/${id}/attachments/${idx}?inline=1`;
  readerPdf.hidden = false;
  readerBody.hidden = true;
  readerText.hidden = true;
}

async function openFile(mid, idx, filename, mime, size) {
  currentOpenId = mid;
  readerPdf.hidden = true; readerPdf.removeAttribute("src");
  readerBody.hidden = true; readerBody.srcdoc = "";
  readerText.hidden = false;
  readerHeader.innerHTML = `<div class="subject">${escapeHtml(filename || "(no name)")}</div>
    <div class="meta">${escapeHtml(mime || "")} · ${humanSize(size)}</div>`;
  readerAtt.innerHTML = `<a href="/api/messages/${mid}/attachments/${idx}" download>Download</a>`;
  readerText.textContent = "Loading…";
  try {
    const d = await getJSON(`/api/files/${mid}/${idx}/text`);
    readerText.textContent = (d.text && d.text.trim())
      ? d.text : "No extractable text for this file type.";
  } catch (err) {
    readerText.textContent = "Failed to load file text: " + err.message;
  }
}

async function openMessage(id, allowRemote = false) {
  currentOpenId = id;
  readerPdf.hidden = true;
  readerPdf.removeAttribute("src");
  readerBody.hidden = false;
  readerText.hidden = true;
  try {
    const m = await getJSON(`/api/messages/${id}?allow_remote=${allowRemote}`);
    const remoteBtn = allowRemote ? "" : `<button id="load-remote" type="button">Load remote images</button>`;
    readerHeader.innerHTML = `<div class="subject">${escapeHtml(m.subject || "(no subject)")}</div>
      <div class="meta">From: ${escapeHtml(m.from || "")}<br>To: ${escapeHtml(m.to || "")}<br>${escapeHtml(m.date || "")}</div>
      ${remoteBtn}`;
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
  appEl.classList.remove("folders-collapsed");
  searchbar.style.display = (mode === "files") ? "none" : "";
  readerHeader.innerHTML = "";
  readerAtt.innerHTML = "";
  readerBody.srcdoc = ""; readerBody.hidden = (mode === "files");
  readerPdf.hidden = true; readerPdf.removeAttribute("src");
  readerText.hidden = true; readerText.textContent = "";
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

// --- Arrow-key navigation between emails in the list ---
document.addEventListener("keydown", (e) => {
  if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
  const tag = (document.activeElement && document.activeElement.tagName) || "";
  if (tag === "INPUT" || tag === "TEXTAREA") return;  // don't hijack typing in search
  const items = Array.from(messageList.querySelectorAll("li")).filter(li => li.id !== "load-more");
  if (items.length === 0) return;
  const current = messageList.querySelector("li.active");
  let idx = current ? items.indexOf(current) : -1;
  idx = e.key === "ArrowDown" ? Math.min(idx + 1, items.length - 1) : Math.max(idx - 1, 0);
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
