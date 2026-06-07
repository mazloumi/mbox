const labelList = document.getElementById("label-list");
const messageList = document.getElementById("message-list");
const readerHeader = document.getElementById("reader-header");
const readerAtt = document.getElementById("reader-attachments");
const readerBody = document.getElementById("reader-body");
const q = document.getElementById("q");

let activeLabel = null;

async function getJSON(url) { return (await fetch(url)).json(); }

async function loadLabels() {
  const labels = await getJSON("/api/labels");
  labelList.innerHTML = "";
  for (const l of labels) {
    const li = document.createElement("li");
    li.innerHTML = `${l.name}<span class="count">${l.count}</span>`;
    li.onclick = () => { activeLabel = l.name; setActive(labelList, li); loadMessages(); };
    labelList.appendChild(li);
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
  renderMessages((await getJSON(url)).messages);
}

async function runSearch() {
  const term = q.value.trim();
  if (!term) return loadMessages();
  const url = `/api/search?q=${encodeURIComponent(term)}` +
    (activeLabel ? `&label=${encodeURIComponent(activeLabel)}` : "");
  renderMessages((await getJSON(url)).messages);
}

async function openMessage(id) {
  const m = await getJSON(`/api/messages/${id}`);
  readerHeader.innerHTML = `<div class="subject">${escapeHtml(m.subject || "(no subject)")}</div>
    <div class="meta">From: ${escapeHtml(m.from || "")}<br>To: ${escapeHtml(m.to || "")}<br>${escapeHtml(m.date || "")}</div>`;
  readerAtt.innerHTML = m.attachments.map(a =>
    `<a href="/api/messages/${id}/attachments/${a.idx}">${escapeHtml(a.filename)} (${a.size}b)</a>`).join("");
  readerBody.srcdoc = m.body_html;
}

function escapeHtml(s) {
  return (s || "").replace(/[&<>"]/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

let searchTimer;
q.addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(runSearch, 250); });

loadLabels();
loadMessages();
