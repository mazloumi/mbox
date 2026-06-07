const labelList = document.getElementById("label-list");
const messageList = document.getElementById("message-list");
const readerHeader = document.getElementById("reader-header");
const readerAtt = document.getElementById("reader-attachments");
const readerBody = document.getElementById("reader-body");
const q = document.getElementById("q");

let activeLabel = null;

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
      li.onclick = () => { activeLabel = l.name; setActive(labelList, li); loadMessages(); };
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

function renderMessages(messages) {
  messageList.innerHTML = "";
  for (const m of messages) {
    const li = document.createElement("li");
    li.innerHTML = `<div class="subject">${escapeHtml(m.subject || "(no subject)")}</div>
      <div class="meta">${escapeHtml(m.from || "")} — ${escapeHtml((m.date || "").slice(0, 10))}</div>`;
    li.onclick = () => { setActive(messageList, li); openMessage(m.id); };
    messageList.appendChild(li);
  }
}

async function loadMessages() {
  const url = activeLabel ? `/api/messages?label=${encodeURIComponent(activeLabel)}` : "/api/messages";
  try {
    renderMessages((await getJSON(url)).messages);
  } catch (err) {
    messageList.innerHTML = `<li>Failed to load messages: ${escapeHtml(String(err.message))}</li>`;
  }
}

async function runSearch() {
  const term = q.value.trim();
  if (!term) return loadMessages();
  const url = `/api/search?q=${encodeURIComponent(term)}` +
    (activeLabel ? `&label=${encodeURIComponent(activeLabel)}` : "");
  try {
    renderMessages((await getJSON(url)).messages);
  } catch (err) {
    messageList.innerHTML = `<li>Search failed: ${escapeHtml(String(err.message))}</li>`;
  }
}

async function openMessage(id) {
  try {
    const m = await getJSON(`/api/messages/${id}`);
    readerHeader.innerHTML = `<div class="subject">${escapeHtml(m.subject || "(no subject)")}</div>
      <div class="meta">From: ${escapeHtml(m.from || "")}<br>To: ${escapeHtml(m.to || "")}<br>${escapeHtml(m.date || "")}</div>`;
    readerAtt.innerHTML = (m.attachments || []).map(a =>
      `<a href="/api/messages/${id}/attachments/${a.idx}">${escapeHtml(a.filename)} (${escapeHtml(String(a.size))}b)</a>`).join("");
    readerBody.srcdoc = m.body_html;
  } catch (err) {
    readerHeader.innerHTML = `<div class="meta">Failed to open message: ${escapeHtml(String(err.message))}</div>`;
    readerAtt.innerHTML = "";
    readerBody.srcdoc = "";
  }
}

let searchTimer;
q.addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(runSearch, 250); });

loadLabels();
loadMessages();
