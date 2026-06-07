const labelList = document.getElementById("label-list");
const messageList = document.getElementById("message-list");
const readerHeader = document.getElementById("reader-header");
const readerAtt = document.getElementById("reader-attachments");
const readerBody = document.getElementById("reader-body");
const readerPdf = document.getElementById("reader-pdf");
const statusBar = document.getElementById("status-bar");
const q = document.getElementById("q");

const PAGE_SIZE = 50;
let activeLabel = null;
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
  if (activeLabel) params.set("label", activeLabel);
  if (currentQuery) { params.set("q", currentQuery); return `/api/search?${params.toString()}`; }
  return `/api/messages?${params.toString()}`;
}

function appendMessages(messages) {
  for (const m of messages) {
    const li = document.createElement("li");
    li.innerHTML = `<div class="subject">${escapeHtml(m.subject || "(no subject)")}</div>
      <div class="meta">${escapeHtml(m.from || "")} — ${escapeHtml((m.date || "").slice(0, 10))}</div>`;
    li.onclick = () => { setActive(messageList, li); openMessage(m.id); };
    messageList.appendChild(li);
  }
  renderLoadMore(messages.length);
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
  try {
    const data = await getJSON(pageUrl(1));
    appendMessages(data.messages);
  } catch (err) {
    messageList.innerHTML = `<li>Failed to load messages: ${escapeHtml(String(err.message))}</li>`;
  }
}

async function loadNextPage() {
  currentPage += 1;
  try {
    const data = await getJSON(pageUrl(currentPage));
    appendMessages(data.messages);
  } catch (err) {
    renderLoadMore(0);
  }
}

function viewPdf(id, idx) {
  readerPdf.src = `/api/messages/${id}/attachments/${idx}?inline=1`;
  readerPdf.hidden = false;
  readerBody.hidden = true;
}

async function openMessage(id, allowRemote = false) {
  currentOpenId = id;
  readerPdf.hidden = true;
  readerPdf.removeAttribute("src");
  readerBody.hidden = false;
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
    readerBody.srcdoc = m.body_html;
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
    if (s.error) {
      statusBar.hidden = false;
      statusBar.className = "error";
      statusBar.textContent = "Indexing failed: " + s.error;
      return;
    }
    if (s.indexing) {
      statusBar.hidden = false;
      statusBar.className = "";
      statusBar.textContent = `Indexing… ${s.percent}% · ${Number(s.messages).toLocaleString()} messages`;
      // Refresh content every ~10s (every 5th tick) to avoid list churn while the
      // percentage in the bar still updates every 2s.
      if (pollTick % 5 === 0) {
        loadLabels();
        if (currentOpenId === null) reload();
      }
      pollTick += 1;
      setTimeout(pollStatus, 2000);
    } else {
      statusBar.hidden = true;
      loadLabels();
      if (currentOpenId === null) reload();
    }
  } catch (err) {
    statusBar.hidden = false;
    statusBar.className = "error";
    statusBar.textContent = "Status unavailable: " + err.message;
    setTimeout(pollStatus, 3000);
  }
}

let searchTimer;
q.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { currentQuery = q.value.trim(); reload(); }, 250);
});

pollStatus();
