const timeline = document.getElementById("timeline");
const composer = document.getElementById("composer");
const promptInput = document.getElementById("prompt");
const sendButton = document.getElementById("send");
const statusEl = document.getElementById("status");
const statusDot = document.getElementById("status-dot");
const newSessionButton = document.getElementById("new-session");
const messageTemplate = document.getElementById("message-template");
const progressTemplate = document.getElementById("progress-template");
const previewTabs = Array.from(document.querySelectorAll("[data-preview-tab]"));
const previewViews = Array.from(document.querySelectorAll("[data-preview-view]"));
const previewTray = document.getElementById("preview-tray");
const tldrawPreview = document.getElementById("tldraw-preview");
const htmlPreview = document.getElementById("html-preview");
const pptPreview = document.getElementById("ppt-preview");

const STORAGE_KEY = "creative_claw_webchat_session_id";
const HISTORY_KEY_PREFIX = "creative_claw_webchat_history:";
const HIDDEN_PROGRESS_TITLES = new Set(["Starting", "Finalize Result"]);
const PREVIEW_TABS = ["tldraw", "html", "ppt"];
const AUTO_PREVIEW_PRIORITY = ["ppt", "html", "tldraw"];

let sessionId = ensureSessionId();
let socket = null;
let activeProgressCard = null;
let activePreviewTab = "tldraw";
let previewArtifactsByTab = buildEmptyPreviewGroups();
let selectedPreviewByTab = buildEmptyPreviewSelections();

connect();
restoreHistory();
renderAllPreviewViews();

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
}

function createNewSession() {
  const nextSessionId = `web-${crypto.randomUUID()}`;
  sessionId = nextSessionId;
  window.localStorage.setItem(STORAGE_KEY, nextSessionId);
  disconnect();
  clearTimeline();
  clearPreviewState();
  renderEmptyState();
  connect();
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
  clearPreviewState({ keepActiveTab: true });
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
  if (type === "assistant") {
    previewArtifactSet(artifacts);
  }
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
    const previewTab = previewTabForArtifact(artifact);
    const anchor = document.createElement("a");
    anchor.className = `artifact-card${previewTab ? " previewable" : ""}`;
    anchor.href = artifact.url;
    anchor.target = "_blank";
    anchor.rel = "noreferrer";
    if (previewTab) {
      anchor.addEventListener("click", (event) => {
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
          return;
        }
        event.preventDefault();
        selectPreviewArtifact(artifact);
      });
    }

    if (artifact.isImage) {
      const image = document.createElement("img");
      image.src = artifact.url;
      image.alt = artifact.name || "artifact";
      image.addEventListener("load", scrollToBottom, { once: true });
      anchor.appendChild(image);
    } else if (isVideoArtifact(artifact)) {
      const video = document.createElement("video");
      video.src = artifact.url;
      video.muted = true;
      video.playsInline = true;
      video.preload = "metadata";
      anchor.appendChild(video);
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

function buildEmptyPreviewGroups() {
  return PREVIEW_TABS.reduce((groups, tabName) => {
    groups[tabName] = [];
    return groups;
  }, {});
}

function buildEmptyPreviewSelections() {
  return PREVIEW_TABS.reduce((selections, tabName) => {
    selections[tabName] = null;
    return selections;
  }, {});
}

function clearPreviewState(options = {}) {
  previewArtifactsByTab = buildEmptyPreviewGroups();
  selectedPreviewByTab = buildEmptyPreviewSelections();
  if (!options.keepActiveTab) {
    activePreviewTab = "tldraw";
  }
  renderAllPreviewViews();
  activatePreviewTab(activePreviewTab);
}

function previewArtifactSet(artifacts) {
  const groups = groupPreviewArtifacts(artifacts);
  if (!PREVIEW_TABS.some((tabName) => groups[tabName].length > 0)) {
    return;
  }

  previewArtifactsByTab = groups;
  selectedPreviewByTab = buildEmptyPreviewSelections();
  for (const tabName of PREVIEW_TABS) {
    selectedPreviewByTab[tabName] = groups[tabName][0] || null;
  }
  renderAllPreviewViews();

  const nextTab = AUTO_PREVIEW_PRIORITY.find((tabName) => groups[tabName].length > 0);
  if (nextTab) {
    activatePreviewTab(nextTab);
  }
}

function groupPreviewArtifacts(artifacts) {
  const groups = buildEmptyPreviewGroups();
  for (const artifact of artifacts || []) {
    const tabName = previewTabForArtifact(artifact);
    if (tabName) {
      addUniqueArtifact(groups[tabName], artifact);
    }
  }
  return groups;
}

function selectPreviewArtifact(artifact) {
  const tabName = previewTabForArtifact(artifact);
  if (!tabName) {
    return;
  }
  addUniqueArtifact(previewArtifactsByTab[tabName], artifact);
  selectedPreviewByTab[tabName] = artifact;
  renderPreviewView(tabName);
  activatePreviewTab(tabName);
}

function addUniqueArtifact(collection, artifact) {
  const key = artifactKey(artifact);
  if (!key || collection.some((item) => artifactKey(item) === key)) {
    return;
  }
  collection.push(artifact);
}

function artifactKey(artifact) {
  return String(artifact?.url || artifact?.path || artifact?.name || "");
}

function activatePreviewTab(tabName) {
  if (!PREVIEW_TABS.includes(tabName)) {
    return;
  }
  activePreviewTab = tabName;
  for (const tab of previewTabs) {
    const isActive = tab.dataset.previewTab === tabName;
    tab.classList.toggle("active", isActive);
    tab.setAttribute("aria-selected", String(isActive));
  }
  for (const view of previewViews) {
    const isActive = view.dataset.previewView === tabName;
    view.classList.toggle("active", isActive);
    view.hidden = !isActive;
  }
  renderPreviewTray();
}

function renderAllPreviewViews() {
  for (const tabName of PREVIEW_TABS) {
    renderPreviewView(tabName);
  }
  renderPreviewTray();
}

function renderPreviewView(tabName) {
  if (tabName === "tldraw") {
    renderTldrawPreview();
    return;
  }
  if (tabName === "html") {
    renderHtmlPreview();
    return;
  }
  if (tabName === "ppt") {
    renderPptPreview();
  }
}

function renderTldrawPreview() {
  tldrawPreview.innerHTML = "";
  const artifact = selectedPreviewByTab.tldraw;
  if (!artifact) {
    tldrawPreview.appendChild(previewEmpty("No media preview"));
    return;
  }

  tldrawPreview.appendChild(buildPreviewToolbar(artifact));

  const board = document.createElement("div");
  board.className = "tldraw-board";
  const frame = document.createElement("div");
  frame.className = "tldraw-media-frame";

  if (isVideoArtifact(artifact)) {
    const video = document.createElement("video");
    video.src = artifact.url;
    video.controls = true;
    video.playsInline = true;
    frame.appendChild(video);
  } else {
    const image = document.createElement("img");
    image.src = artifact.url;
    image.alt = artifact.name || "artifact";
    frame.appendChild(image);
  }

  board.appendChild(frame);
  tldrawPreview.appendChild(board);
}

function renderHtmlPreview() {
  htmlPreview.innerHTML = "";
  const artifact = selectedPreviewByTab.html;
  if (!artifact) {
    htmlPreview.appendChild(previewEmpty("No HTML preview"));
    return;
  }

  const refreshButton = document.createElement("button");
  refreshButton.className = "preview-action";
  refreshButton.type = "button";
  refreshButton.textContent = "Refresh";

  htmlPreview.appendChild(buildPreviewToolbar(artifact, [refreshButton]));

  const iframe = document.createElement("iframe");
  iframe.className = "html-preview-frame";
  iframe.src = artifact.url;
  iframe.setAttribute("sandbox", "allow-scripts allow-forms allow-popups allow-downloads");
  iframe.title = artifact.name || "HTML preview";
  refreshButton.addEventListener("click", () => {
    iframe.src = artifact.url;
  });
  htmlPreview.appendChild(iframe);
}

function renderPptPreview() {
  pptPreview.innerHTML = "";
  const artifact = selectedPreviewByTab.ppt;
  if (!artifact) {
    pptPreview.appendChild(previewEmpty("No PPT preview"));
    return;
  }

  pptPreview.appendChild(buildPreviewToolbar(artifact));

  if (isPdfArtifact(artifact) || isPptxArtifact(artifact)) {
    const iframe = document.createElement("iframe");
    iframe.className = "ppt-preview-frame";
    iframe.src = isPptxArtifact(artifact) ? previewUrlForArtifact(artifact) : artifact.url;
    iframe.title = artifact.name || "PPT preview";
    pptPreview.appendChild(iframe);
    return;
  }

  const card = document.createElement("div");
  card.className = "document-preview-card";

  const icon = document.createElement("div");
  icon.className = "document-preview-icon";
  icon.textContent = artifactExtension(artifact).replace(".", "").toUpperCase() || "PPT";

  const copy = document.createElement("div");
  copy.className = "document-preview-copy";
  const title = document.createElement("div");
  title.className = "document-preview-title";
  title.textContent = artifact.name || "Presentation file";
  const meta = document.createElement("div");
  meta.className = "document-preview-meta";
  meta.textContent = artifact.path || artifact.mimeType || "";
  copy.appendChild(title);
  copy.appendChild(meta);

  const open = document.createElement("a");
  open.className = "preview-open-link";
  open.href = artifact.url;
  open.target = "_blank";
  open.rel = "noreferrer";
  open.textContent = "Open file";

  card.appendChild(icon);
  card.appendChild(copy);
  card.appendChild(open);
  pptPreview.appendChild(card);
}

function buildPreviewToolbar(artifact, actions = []) {
  const toolbar = document.createElement("div");
  toolbar.className = "preview-toolbar";

  const meta = document.createElement("div");
  meta.className = "preview-file";
  const name = document.createElement("div");
  name.className = "preview-file-name";
  name.textContent = artifact.name || "artifact";
  const path = document.createElement("div");
  path.className = "preview-file-path";
  path.textContent = artifact.path || artifact.mimeType || "";
  meta.appendChild(name);
  meta.appendChild(path);

  const actionGroup = document.createElement("div");
  actionGroup.className = "preview-actions";
  for (const action of actions) {
    actionGroup.appendChild(action);
  }
  const open = document.createElement("a");
  open.className = "preview-action";
  open.href = artifact.url;
  open.target = "_blank";
  open.rel = "noreferrer";
  open.textContent = "Open";
  actionGroup.appendChild(open);

  toolbar.appendChild(meta);
  toolbar.appendChild(actionGroup);
  return toolbar;
}

function previewEmpty(text) {
  const empty = document.createElement("div");
  empty.className = "preview-empty";
  empty.textContent = text;
  return empty;
}

function renderPreviewTray() {
  const artifacts = previewArtifactsByTab[activePreviewTab] || [];
  previewTray.innerHTML = "";
  if (artifacts.length <= 1) {
    previewTray.hidden = true;
    return;
  }

  previewTray.hidden = false;
  for (const artifact of artifacts) {
    const button = document.createElement("button");
    button.className = "preview-tray-item";
    button.type = "button";
    button.classList.toggle("active", artifactKey(artifact) === artifactKey(selectedPreviewByTab[activePreviewTab]));
    button.textContent = artifact.name || "artifact";
    button.addEventListener("click", () => {
      selectedPreviewByTab[activePreviewTab] = artifact;
      renderPreviewView(activePreviewTab);
      renderPreviewTray();
    });
    previewTray.appendChild(button);
  }
}

function previewTabForArtifact(artifact) {
  if (!artifact) {
    return "";
  }
  if (isPptArtifact(artifact) || isPdfArtifact(artifact)) {
    return "ppt";
  }
  if (isHtmlArtifact(artifact)) {
    return "html";
  }
  if (artifact.isImage || isVideoArtifact(artifact)) {
    return "tldraw";
  }
  return "";
}

function isHtmlArtifact(artifact) {
  const extension = artifactExtension(artifact);
  const mimeType = artifactMimeType(artifact);
  return extension === ".html" || extension === ".htm" || mimeType === "text/html";
}

function isPptArtifact(artifact) {
  const extension = artifactExtension(artifact);
  const mimeType = artifactMimeType(artifact);
  return (
    extension === ".ppt" ||
    isPptxArtifact(artifact) ||
    mimeType === "application/vnd.ms-powerpoint" ||
    mimeType === "application/vnd.openxmlformats-officedocument.presentationml.presentation"
  );
}

function isPptxArtifact(artifact) {
  return (
    artifactExtension(artifact) === ".pptx" ||
    artifactMimeType(artifact) === "application/vnd.openxmlformats-officedocument.presentationml.presentation"
  );
}

function isPdfArtifact(artifact) {
  return artifactExtension(artifact) === ".pdf" || artifactMimeType(artifact) === "application/pdf";
}

function isVideoArtifact(artifact) {
  return artifactMimeType(artifact).startsWith("video/");
}

function artifactMimeType(artifact) {
  return String(artifact?.mimeType || "").toLowerCase();
}

function artifactExtension(artifact) {
  const source = String(artifact?.name || artifact?.path || artifact?.url || "").split("?")[0].split("#")[0];
  const dotIndex = source.lastIndexOf(".");
  return dotIndex >= 0 ? source.slice(dotIndex).toLowerCase() : "";
}

function previewUrlForArtifact(artifact) {
  const url = String(artifact?.url || "");
  if (url.startsWith("/workspace/")) {
    return url.replace("/workspace/", "/workspace-preview/");
  }
  return url;
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

for (const tab of previewTabs) {
  tab.addEventListener("click", () => {
    activatePreviewTab(tab.dataset.previewTab);
  });
}

window.addEventListener("beforeunload", () => {
  disconnect();
});
