const timeline = document.getElementById("timeline");
const composer = document.getElementById("composer");
const promptInput = document.getElementById("prompt");
const sendButton = document.getElementById("send");
const statusEl = document.getElementById("status");
const statusDot = document.getElementById("status-dot");
const sessionListEl = document.getElementById("session-list");
const titleEl = document.getElementById("title");
const newSessionButton = document.getElementById("new-session");
const clearHistoryButton = document.getElementById("clear-history");
const messageTemplate = document.getElementById("message-template");
const progressTemplate = document.getElementById("progress-template");

const STORAGE_KEY = "creative_claw_webchat_session_id";
const SESSION_INDEX_KEY = "creative_claw_webchat_sessions";
const HISTORY_KEY_PREFIX = "creative_claw_webchat_history:";
const HIDDEN_PROGRESS_TITLES = new Set(["Starting", "Finalize Result"]);

let sessionId = ensureSessionId();
let socket = null;
let activeProgressCard = null;

connect();
restoreHistory();
renderSessionList();

function ensureSessionId() {
  const existing = window.localStorage.getItem(STORAGE_KEY);
  if (existing) {
    return existing;
  }
  const created = `web-${crypto.randomUUID()}`;
  window.localStorage.setItem(STORAGE_KEY, created);
  return created;
}

function historyKey(currentSessionId = sessionId) {
  return `${HISTORY_KEY_PREFIX}${currentSessionId}`;
}

function loadHistory(currentSessionId = sessionId) {
  try {
    const raw = window.localStorage.getItem(historyKey(currentSessionId));
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function saveHistory(items, currentSessionId = sessionId) {
  window.localStorage.setItem(historyKey(currentSessionId), JSON.stringify(items.slice(-120)));
}

function appendHistory(entry, currentSessionId = sessionId) {
  const items = loadHistory(currentSessionId);
  items.push(entry);
  saveHistory(items, currentSessionId);
  recordSessionActivity(currentSessionId);
  renderSessionList();
}

function loadSessions() {
  try {
    const raw = window.localStorage.getItem(SESSION_INDEX_KEY);
    if (!raw) {
      return [];
    }
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function recordSessionActivity(currentSessionId) {
  let sessions = loadSessions();
  sessions = sessions.filter((value) => value !== currentSessionId);
  sessions.unshift(currentSessionId);
  window.localStorage.setItem(SESSION_INDEX_KEY, JSON.stringify(sessions.slice(0, 20)));
}

function removeSessionActivity(currentSessionId) {
  const sessions = loadSessions().filter((value) => value !== currentSessionId);
  window.localStorage.setItem(SESSION_INDEX_KEY, JSON.stringify(sessions));
}

function renderSessionList() {
  const sessions = loadSessions();
  if (!sessions.includes(sessionId)) {
    sessions.unshift(sessionId);
  }
  sessionListEl.innerHTML = "";

  for (const item of sessions) {
    const li = document.createElement("li");
    li.className = `session-item${item === sessionId ? " active" : ""}`;

    const title = document.createElement("div");
    title.className = "session-item-title";
    title.textContent = item === sessionId ? "Current Session" : "Saved Session";

    const meta = document.createElement("div");
    meta.className = "session-item-meta";
    meta.textContent = item.replace("web-", "").slice(0, 8);

    li.appendChild(title);
    li.appendChild(meta);
    li.addEventListener("click", () => {
      if (item !== sessionId) {
        switchSession(item);
      }
    });

    sessionListEl.appendChild(li);
  }
}

function switchSession(nextSessionId) {
  sessionId = nextSessionId;
  window.localStorage.setItem(STORAGE_KEY, sessionId);
  disconnect();
  clearTimeline();
  restoreHistory();
  renderSessionList();
  connect();
}

function createNewSession() {
  const nextSessionId = `web-${crypto.randomUUID()}`;
  sessionId = nextSessionId;
  window.localStorage.setItem(STORAGE_KEY, nextSessionId);
  disconnect();
  clearTimeline();
  renderEmptyState();
  renderSessionList();
  connect();
}

function clearCurrentSession() {
  window.localStorage.removeItem(historyKey());
  removeSessionActivity(sessionId);
  clearTimeline();
  renderEmptyState();
  renderSessionList();
}

function wsUrl() {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/ws?session_id=${encodeURIComponent(sessionId)}`;
}

function connect() {
  setStatus("connecting");
  socket = new WebSocket(wsUrl());

  socket.addEventListener("open", () => {
    setStatus("connected");
  });

  socket.addEventListener("close", () => {
    setStatus("disconnected");
  });

  socket.addEventListener("error", () => {
    setStatus("error");
  });

  socket.addEventListener("message", (event) => {
    let payload = {};
    try {
      payload = JSON.parse(event.data);
    } catch {
      addMessageCard("error", "CreativeClaw", "Received an invalid response payload.");
      return;
    }
    handleEvent(payload);
  });
}

function disconnect() {
  if (socket) {
    socket.close();
    socket = null;
  }
  activeProgressCard = null;
}

function setStatus(status) {
  statusEl.textContent = status;
  statusDot.className = "status-dot";
  if (status === "connected" || status === "ready") {
    statusDot.classList.add("connected");
  }
  if (status === "error" || status === "disconnected") {
    statusDot.classList.add("error");
  }
}

function handleEvent(payload) {
  if (payload.type === "ready") {
    setStatus("ready");
    if (payload.title) {
      titleEl.textContent = payload.title;
      document.title = payload.title;
    }
    renderEmptyStateIfNeeded();
    return;
  }

  if (payload.type === "progress") {
    upsertProgressCard(payload.content || "", payload.metadata || {});
    return;
  }

  activeProgressCard = null;

  if (payload.type === "assistant_message") {
    addMessageCard("assistant", "CreativeClaw", payload.content || "", payload.artifacts || []);
    appendHistory({
      type: "assistant",
      role: "CreativeClaw",
      content: payload.content || "",
      artifacts: payload.artifacts || [],
    });
    return;
  }

  if (payload.type === "error") {
    addMessageCard("error", "CreativeClaw", payload.content || payload.message || "Unknown error.");
    appendHistory({
      type: "error",
      role: "CreativeClaw",
      content: payload.content || payload.message || "Unknown error.",
      artifacts: [],
    });
  }
}

function renderEmptyStateIfNeeded() {
  if (timeline.children.length === 0) {
    renderEmptyState();
  }
}

function renderEmptyState() {
  const block = document.createElement("article");
  block.className = "empty-state";
  block.textContent = "Start with a prompt such as: “Create a cinematic travel poster”, “Describe this image idea”, or “Rewrite this prompt for cleaner composition.”";
  timeline.appendChild(block);
}

function clearTimeline() {
  timeline.innerHTML = "";
  activeProgressCard = null;
}

function restoreHistory() {
  clearTimeline();
  const items = loadHistory();
  if (items.length === 0) {
    renderEmptyState();
    return;
  }

  for (const item of items) {
    addMessageCard(item.type || "assistant", item.role || "CreativeClaw", item.content || "", item.artifacts || [], false);
  }
  scrollToBottom();
}

function removeEmptyState() {
  const empty = timeline.querySelector(".empty-state");
  if (empty) {
    empty.remove();
  }
}

function addMessageCard(type, role, content, artifacts = [], scroll = true) {
  removeEmptyState();
  const fragment = messageTemplate.content.cloneNode(true);
  const root = fragment.querySelector(".message-card");
  root.classList.add(type);
  fragment.querySelector(".message-role").textContent = role;
  fragment.querySelector(".message-body").innerHTML = renderMarkdown(content || "");
  const artifactGrid = fragment.querySelector(".artifact-grid");
  renderArtifacts(artifactGrid, artifacts);
  timeline.appendChild(fragment);
  if (scroll) {
    scrollToBottom();
  }
}

function upsertProgressCard(content, metadata) {
  removeEmptyState();
  if (!activeProgressCard) {
    const fragment = progressTemplate.content.cloneNode(true);
    timeline.appendChild(fragment);
    activeProgressCard = timeline.lastElementChild;
  }
  const rawTitle = String(metadata.stage_title || "").trim();
  const titleEl = activeProgressCard.querySelector(".progress-title");
  if (HIDDEN_PROGRESS_TITLES.has(rawTitle)) {
    titleEl.hidden = true;
    titleEl.textContent = "";
  } else {
    titleEl.hidden = false;
    titleEl.textContent = rawTitle || "Working";
  }
  activeProgressCard.querySelector(".progress-body").innerHTML = renderMarkdown(content);
  scrollToBottom();
}

function renderArtifacts(container, artifacts) {
  container.innerHTML = "";
  if (!artifacts || artifacts.length === 0) {
    container.style.display = "none";
    return;
  }
  container.style.display = "grid";
  for (const artifact of artifacts) {
    const anchor = document.createElement("a");
    anchor.className = "artifact-card";
    anchor.href = artifact.url;
    anchor.target = "_blank";
    anchor.rel = "noreferrer";

    if (artifact.isImage) {
      const image = document.createElement("img");
      image.src = artifact.url;
      image.alt = artifact.name || "artifact";
      image.addEventListener("load", scrollToBottom, { once: true });
      anchor.appendChild(image);
    }

    const name = document.createElement("div");
    name.className = "artifact-name";
    name.textContent = artifact.name || "artifact";
    anchor.appendChild(name);

    const meta = document.createElement("div");
    meta.className = "artifact-meta";
    meta.textContent = artifact.path || artifact.mimeType || "";
    anchor.appendChild(meta);

    container.appendChild(anchor);
  }
}

function renderMarkdown(text) {
  const lines = String(text || "").replace(/\r\n?/g, "\n").split("\n");
  const rendered = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];

    if (!line.trim()) {
      index += 1;
      continue;
    }

    const fence = line.match(/^\s*```([A-Za-z0-9_-]+)?\s*$/);
    if (fence) {
      const codeLines = [];
      index += 1;
      while (index < lines.length && !/^\s*```\s*$/.test(lines[index])) {
        codeLines.push(lines[index]);
        index += 1;
      }
      if (index < lines.length) {
        index += 1;
      }
      const languageClass = fence[1] ? ` class="language-${escapeAttribute(fence[1])}"` : "";
      rendered.push(`<pre><code${languageClass}>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
      continue;
    }

    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      const level = heading[1].length;
      rendered.push(`<h${level}>${renderInline(heading[2].trim())}</h${level}>`);
      index += 1;
      continue;
    }

    if (/^\s{0,3}([-*_])(?:\s*\1){2,}\s*$/.test(line)) {
      rendered.push("<hr>");
      index += 1;
      continue;
    }

    if (isTableStart(lines, index)) {
      const tableLines = [];
      while (index < lines.length && looksLikeTableRow(lines[index])) {
        tableLines.push(lines[index]);
        index += 1;
      }
      rendered.push(renderTable(tableLines));
      continue;
    }

    if (/^\s{0,3}>\s?/.test(line)) {
      const quoteLines = [];
      while (index < lines.length && /^\s{0,3}>\s?/.test(lines[index])) {
        quoteLines.push(lines[index].replace(/^\s{0,3}>\s?/, ""));
        index += 1;
      }
      rendered.push(`<blockquote>${renderMarkdown(quoteLines.join("\n"))}</blockquote>`);
      continue;
    }

    const list = listMatch(line);
    if (list) {
      const listType = list.ordered ? "ol" : "ul";
      const items = [];
      while (index < lines.length) {
        const nextList = listMatch(lines[index]);
        if (!nextList || nextList.ordered !== list.ordered) {
          break;
        }
        items.push(`<li>${renderInline(nextList.content)}</li>`);
        index += 1;
      }
      rendered.push(`<${listType}>${items.join("")}</${listType}>`);
      continue;
    }

    const paragraphLines = [line.trim()];
    index += 1;
    while (index < lines.length && lines[index].trim() && !startsMarkdownBlock(lines, index)) {
      paragraphLines.push(lines[index].trim());
      index += 1;
    }
    rendered.push(`<p>${renderInline(paragraphLines.join("\n")).replaceAll("\n", "<br>")}</p>`);
  }

  return rendered.join("");
}

function escapeHtml(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function escapeAttribute(text) {
  return escapeHtml(text).replaceAll("`", "&#96;");
}

function startsMarkdownBlock(lines, index) {
  const line = lines[index] || "";
  return (
    /^\s*```/.test(line) ||
    /^(#{1,6})\s+/.test(line) ||
    /^\s{0,3}([-*_])(?:\s*\1){2,}\s*$/.test(line) ||
    /^\s{0,3}>\s?/.test(line) ||
    Boolean(listMatch(line)) ||
    isTableStart(lines, index)
  );
}

function listMatch(line) {
  const unordered = line.match(/^\s{0,3}[-*+]\s+(.+)$/);
  if (unordered) {
    return { ordered: false, content: unordered[1].trim() };
  }
  const ordered = line.match(/^\s{0,3}\d+[.)]\s+(.+)$/);
  if (ordered) {
    return { ordered: true, content: ordered[1].trim() };
  }
  return null;
}

function looksLikeTableRow(line) {
  return /^\s*\|?.+\|.+\|?\s*$/.test(line || "");
}

function isTableDivider(line) {
  return /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line || "");
}

function isTableStart(lines, index) {
  return looksLikeTableRow(lines[index]) && isTableDivider(lines[index + 1]);
}

function splitTableRow(line) {
  return String(line || "")
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

function tableAlignment(dividerCell) {
  const cell = dividerCell.trim();
  if (cell.startsWith(":") && cell.endsWith(":")) return "center";
  if (cell.endsWith(":")) return "right";
  return "left";
}

function renderTable(tableLines) {
  const header = splitTableRow(tableLines[0]);
  const alignments = splitTableRow(tableLines[1]).map(tableAlignment);
  const bodyRows = tableLines.slice(2).map(splitTableRow);
  const headerHtml = header
    .map((cell, cellIndex) => renderTableCell("th", cell, alignments[cellIndex]))
    .join("");
  const bodyHtml = bodyRows
    .map((row) => `<tr>${row.map((cell, cellIndex) => renderTableCell("td", cell, alignments[cellIndex])).join("")}</tr>`)
    .join("");
  return `<div class="markdown-table-wrap"><table><thead><tr>${headerHtml}</tr></thead><tbody>${bodyHtml}</tbody></table></div>`;
}

function renderTableCell(tagName, content, alignment) {
  const style = alignment && alignment !== "left" ? ` style="text-align: ${alignment}"` : "";
  return `<${tagName}${style}>${renderInline(content)}</${tagName}>`;
}

function renderInline(text) {
  const tokens = [];
  let working = String(text || "");

  working = working.replace(/`([^`\n]+)`/g, (_, code) => inlineToken(tokens, `<code>${escapeHtml(code)}</code>`));
  working = working.replace(
    /!\[([^\]\n]*)\]\(([^)\s]+)(?:\s+"([^"]+)")?\)/g,
    (_, alt, rawUrl, title) => {
      const safeUrl = sanitizeUrl(rawUrl, { allowMailto: false });
      if (!safeUrl) return alt;
      const cleanAlt = stripInlineTokens(alt);
      const titleAttr = title ? ` title="${escapeAttribute(stripInlineTokens(title))}"` : "";
      return inlineToken(
        tokens,
        `<img class="markdown-image" src="${escapeAttribute(safeUrl)}" alt="${escapeAttribute(cleanAlt)}"${titleAttr}>`
      );
    }
  );
  working = working.replace(
    /\[([^\]\n]+)\]\(([^)\s]+)(?:\s+"([^"]+)")?\)/g,
    (_, label, rawUrl, title) => {
      const safeUrl = sanitizeUrl(rawUrl, { allowMailto: true });
      if (!safeUrl) return label;
      const titleAttr = title ? ` title="${escapeAttribute(stripInlineTokens(title))}"` : "";
      return inlineToken(
        tokens,
        `<a href="${escapeAttribute(safeUrl)}" target="_blank" rel="noreferrer"${titleAttr}>${renderInlineText(label)}</a>`
      );
    }
  );

  working = renderInlineText(working);
  return restoreInlineTokens(working, tokens);
}

function renderInlineText(text) {
  let html = escapeHtml(text || "");
  html = html.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/__([^_\n]+)__/g, "<strong>$1</strong>");
  html = html.replace(/~~([^~\n]+)~~/g, "<del>$1</del>");
  html = html.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>");
  html = html.replace(/(^|[^\w_])_([^_\n]+)_($|[^\w_])/g, "$1<em>$2</em>$3");
  return html;
}

function inlineToken(tokens, html) {
  const token = `\uE000${tokens.length}\uE000`;
  tokens.push(html);
  return token;
}

function restoreInlineTokens(html, tokens) {
  return tokens.reduce((current, tokenHtml, index) => current.replaceAll(`\uE000${index}\uE000`, tokenHtml), html);
}

function stripInlineTokens(text) {
  return String(text || "").replace(/\uE000\d+\uE000/g, "");
}

function sanitizeUrl(rawUrl, options = {}) {
  const value = String(rawUrl || "").trim();
  if (!value || /[\u0000-\u001f\s\uE000]/.test(value) || value.startsWith("//")) {
    return "";
  }

  const scheme = value.match(/^([A-Za-z][A-Za-z0-9+.-]*):/);
  if (!scheme) {
    return value;
  }

  const protocol = scheme[1].toLowerCase();
  if (protocol === "http" || protocol === "https" || (options.allowMailto && protocol === "mailto")) {
    return value;
  }
  return "";
}

function scrollToBottom() {
  window.requestAnimationFrame(() => {
    timeline.scrollTop = timeline.scrollHeight;
  });
}

function sendPrompt() {
  const content = promptInput.value.trim();
  if (!content || !socket || socket.readyState !== WebSocket.OPEN) {
    return;
  }
  socket.send(JSON.stringify({ type: "chat", content }));
  addMessageCard("user", "You", content);
  appendHistory({ type: "user", role: "You", content, artifacts: [] });
  promptInput.value = "";
  promptInput.style.height = "";
  activeProgressCard = null;
}

composer.addEventListener("submit", (event) => {
  event.preventDefault();
  sendPrompt();
});

promptInput.addEventListener("input", () => {
  promptInput.style.height = "";
  promptInput.style.height = `${Math.min(promptInput.scrollHeight, 220)}px`;
});

promptInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    sendPrompt();
  }
});

newSessionButton.addEventListener("click", () => {
  createNewSession();
});

clearHistoryButton.addEventListener("click", () => {
  clearCurrentSession();
});

window.addEventListener("beforeunload", () => {
  disconnect();
});
