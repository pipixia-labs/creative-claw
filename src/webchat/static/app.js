const timeline = document.getElementById("timeline");
const composer = document.getElementById("composer");
const promptInput = document.getElementById("prompt");
const sendButton = document.getElementById("send");
const attachButton = document.getElementById("attach-files");
const fileInput = document.getElementById("file-input");
const attachmentList = document.getElementById("attachment-list");
const statusEl = document.getElementById("status");
const statusDot = document.getElementById("status-dot");
const newSessionButton = document.getElementById("new-session");
const sessionHistoryButton = document.getElementById("session-history");
const sessionPopover = document.getElementById("session-popover");
const sessionList = document.getElementById("session-list");
const messageTemplate = document.getElementById("message-template");
const progressTemplate = document.getElementById("progress-template");
const previewTabs = Array.from(document.querySelectorAll("[data-preview-tab]"));
const previewViews = Array.from(document.querySelectorAll("[data-preview-view]"));
const tldrawPreview = document.getElementById("tldraw-preview");
const htmlPreview = document.getElementById("html-preview");
const pptPreview = document.getElementById("ppt-preview");

const STORAGE_KEY = "creative_claw_webchat_session_id";
const HISTORY_KEY_PREFIX = "creative_claw_webchat_history:";
const SESSION_INDEX_KEY = "creative_claw_webchat_sessions";
const HIDDEN_PROGRESS_TITLES = new Set(["Starting", "Finalize Result"]);
const PREVIEW_TABS = ["tldraw", "html", "ppt"];
const AUTO_PREVIEW_PRIORITY = ["ppt", "html", "tldraw"];
const UPLOAD_CHUNK_SIZE = 512 * 1024;
const MEDIA_CANVAS_MIN_ZOOM = 0.45;
const MEDIA_CANVAS_MAX_ZOOM = 2.4;
const HTML_PREVIEW_MIN_ZOOM = 0.1;
const HTML_PREVIEW_MAX_ZOOM = 4;
const HTML_PREVIEW_ZOOM_STEP = 0.1;
const HTML_PREVIEW_MAX_STAGE_SIZE = 40000;

let sessionId = ensureSessionId();
let socket = null;
let activeProgressCard = null;
let progressBodyCounter = 0;
let currentRunId = null;
let runState = "idle";
let activePreviewTab = "tldraw";
let htmlPreviewZoom = 1;
let htmlPreviewPanX = 0;
let htmlPreviewPanY = 0;
let previewArtifactsByTab = buildEmptyPreviewGroups();
let selectedPreviewByTab = buildEmptyPreviewSelections();
const attachedFiles = [];
const uploadWaiters = new Map();
const mediaCanvasState = {
  zoom: 1,
  panX: 0,
  panY: 0,
  signature: "",
  fitSignature: "",
  positions: new Map(),
};
let tldrawCanvasUnmount = null;

connect();
restoreHistory();
renderAllPreviewViews();
renderSessionHistory();

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
  const timestampedEntry = {
    ...entry,
    createdAt: entry.createdAt || new Date().toISOString(),
  };
  items.push(timestampedEntry);
  saveHistory(items, currentSessionId);
  touchSessionIndex(currentSessionId, items);
  renderSessionHistory();
}

function loadSessionIndex() {
  try {
    const raw = window.localStorage.getItem(SESSION_INDEX_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function saveSessionIndex(sessions) {
  window.localStorage.setItem(SESSION_INDEX_KEY, JSON.stringify(sessions.slice(0, 80)));
}

function touchSessionIndex(currentSessionId = sessionId, items = loadHistory(currentSessionId)) {
  if (!currentSessionId) {
    return;
  }
  const summary = buildSessionSummary(currentSessionId, items);
  const sessions = loadSessionIndex().filter((item) => item.id !== currentSessionId);
  sessions.unshift(summary);
  saveSessionIndex(sessions.sort(compareSessionSummaries));
}

function historySessionIds() {
  const ids = new Set(loadSessionIndex().map((item) => item.id).filter(Boolean));
  ids.add(sessionId);
  for (let index = 0; index < window.localStorage.length; index += 1) {
    const key = window.localStorage.key(index) || "";
    if (key.startsWith(HISTORY_KEY_PREFIX)) {
      const foundSessionId = key.slice(HISTORY_KEY_PREFIX.length);
      if (foundSessionId) {
        ids.add(foundSessionId);
      }
    }
  }
  return Array.from(ids);
}

function sessionSummaries() {
  const indexed = new Map(loadSessionIndex().map((item) => [item.id, item]));
  const summaries = historySessionIds().map((foundSessionId) => {
    const items = loadHistory(foundSessionId);
    return buildSessionSummary(foundSessionId, items, indexed.get(foundSessionId));
  });
  return summaries.sort(compareSessionSummaries);
}

function compareSessionSummaries(left, right) {
  if (left.id === sessionId && right.id !== sessionId) return -1;
  if (right.id === sessionId && left.id !== sessionId) return 1;
  const leftTime = Date.parse(left.updatedAt || "") || 0;
  const rightTime = Date.parse(right.updatedAt || "") || 0;
  return rightTime - leftTime;
}

function buildSessionSummary(foundSessionId, items = loadHistory(foundSessionId), indexed = {}) {
  const firstUser = items.find((item) => item.type === "user" && item.content);
  const lastItem = [...items].reverse().find((item) => item.createdAt);
  const latestArtifacts = latestSessionArtifacts(items);
  return {
    id: foundSessionId,
    title: compactText(sessionTitleText(firstUser?.content) || indexed.title || "New session", 42),
    updatedAt: lastItem?.createdAt || indexed.updatedAt || "",
    count: items.length,
    artifactTypes: sessionArtifactTypes(latestArtifacts),
  };
}

function sessionTitleText(content) {
  return String(content || "").split("\n\nAttached files:", 1)[0].trim();
}

function latestSessionArtifacts(items) {
  for (let index = items.length - 1; index >= 0; index -= 1) {
    const artifacts = artifactsForHistoryItem(items[index]);
    if (Array.isArray(artifacts) && artifacts.length > 0) {
      return artifacts;
    }
  }
  return [];
}

function sessionArtifactTypes(artifacts) {
  const labels = new Set();
  for (const artifact of artifacts || []) {
    const tab = previewTabForArtifact(artifact);
    if (tab === "tldraw") labels.add("Image");
    if (tab === "html") labels.add("Design");
    if (tab === "ppt") labels.add("PPT");
  }
  return Array.from(labels);
}

function compactText(text, maxLength) {
  const compacted = String(text || "").replace(/\s+/g, " ").trim();
  if (compacted.length <= maxLength) {
    return compacted;
  }
  return `${compacted.slice(0, maxLength - 1)}…`;
}

function formatSessionTime(value) {
  const date = new Date(value);
  if (!value || Number.isNaN(date.getTime())) {
    return "Local";
  }
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function renderSessionHistory() {
  if (!sessionList) {
    return;
  }
  sessionList.innerHTML = "";
  const summaries = sessionSummaries();
  for (const summary of summaries) {
    const button = document.createElement("button");
    button.className = "session-item";
    button.type = "button";
    button.classList.toggle("active", summary.id === sessionId);
    button.addEventListener("click", () => {
      switchSession(summary.id);
    });

    const main = document.createElement("span");
    main.className = "session-item-main";
    main.textContent = summary.title;
    const meta = document.createElement("span");
    meta.className = "session-item-meta";
    meta.textContent = `${formatSessionTime(summary.updatedAt)} · ${summary.count || 0} messages`;
    button.appendChild(main);
    button.appendChild(meta);

    if (summary.artifactTypes.length > 0) {
      const tags = document.createElement("span");
      tags.className = "session-item-tags";
      for (const type of summary.artifactTypes) {
        const tag = document.createElement("span");
        tag.className = "session-tag";
        tag.textContent = type;
        tags.appendChild(tag);
      }
      button.appendChild(tags);
    }

    sessionList.appendChild(button);
  }

  if (summaries.length === 0) {
    const empty = document.createElement("div");
    empty.className = "session-empty";
    empty.textContent = "No local sessions yet.";
    sessionList.appendChild(empty);
  }
}

function toggleSessionPopover() {
  if (!sessionPopover.hidden) {
    closeSessionPopover();
    return;
  }
  renderSessionHistory();
  sessionPopover.hidden = false;
  sessionHistoryButton.setAttribute("aria-expanded", "true");
}

function closeSessionPopover() {
  sessionPopover.hidden = true;
  sessionHistoryButton.setAttribute("aria-expanded", "false");
}

function switchSession(nextSessionId) {
  if (!nextSessionId || nextSessionId === sessionId) {
    closeSessionPopover();
    return;
  }
  touchSessionIndex(sessionId);
  sessionId = nextSessionId;
  window.localStorage.setItem(STORAGE_KEY, nextSessionId);
  disconnect();
  clearAttachments();
  restoreHistory();
  closeSessionPopover();
  renderSessionHistory();
  connect();
}

function createNewSession() {
  touchSessionIndex(sessionId);
  const nextSessionId = `web-${crypto.randomUUID()}`;
  sessionId = nextSessionId;
  window.localStorage.setItem(STORAGE_KEY, nextSessionId);
  disconnect();
  clearAttachments();
  clearTimeline();
  clearPreviewState();
  renderEmptyState();
  touchSessionIndex(nextSessionId, []);
  closeSessionPopover();
  renderSessionHistory();
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
    setRunState("idle");
  });

  socket.addEventListener("error", () => {
    setStatus("error");
    setRunState("idle");
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
  updateComposerButtons();
}

function handleEvent(payload) {
  if (handleUploadEvent(payload)) {
    return;
  }

  if (payload.type === "ready") {
    setStatus("ready");
    if (payload.title) {
      document.title = payload.title;
    }
    renderEmptyStateIfNeeded();
    return;
  }

  if (payload.type === "task_started") {
    handleTaskStarted(payload);
    return;
  }

  if (payload.type === "task_stopping") {
    handleTaskStopping(payload);
    return;
  }

  if (payload.type === "task_finished") {
    handleTaskFinished(payload);
    return;
  }

  if (payload.type === "task_stop_ignored") {
    return;
  }

  if (payload.type === "progress") {
    if (shouldIgnoreStaleRunEvent(payload)) {
      return;
    }
    upsertProgressCard(payload.content || "", payload.metadata || {});
    return;
  }

  activeProgressCard = null;

  if (payload.type === "assistant_message") {
    if (shouldIgnoreStaleRunEvent(payload)) {
      return;
    }
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
    if (payload.code === "task_running") {
      if (payload.runId) {
        setRunState("running", payload.runId);
      }
      return;
    }
    if (shouldIgnoreStaleRunEvent(payload)) {
      return;
    }
    addMessageCard("error", "CreativeClaw", payload.content || payload.message || "Unknown error.");
    appendHistory({
      type: "error",
      role: "CreativeClaw",
      content: payload.content || payload.message || "Unknown error.",
      artifacts: [],
    });
  }
}

function setRunState(next, runId = currentRunId) {
  runState = next;
  currentRunId = next === "idle" ? null : runId;
  sendButton.dataset.state = next;
  const labels = {
    idle: "Send message",
    running: "Stop running task",
    stopping: "Stopping...",
  };
  const label = labels[next] || labels.idle;
  sendButton.setAttribute("aria-label", label);
  sendButton.title = label;
  updateComposerButtons();
}

function handleTaskStarted(payload) {
  if (!payload.runId) {
    return;
  }
  if (runState === "idle") {
    setRunState("running", payload.runId);
  }
}

function handleTaskStopping(payload) {
  if (!payload.runId || payload.runId !== currentRunId) {
    return;
  }
  setRunState("stopping", payload.runId);
}

function handleTaskFinished(payload) {
  if (!payload.runId || payload.runId !== currentRunId) {
    return;
  }
  if (payload.reason === "cancelled") {
    addMessageCard("system", "CreativeClaw", "Task stopped.");
  }
  setRunState("idle");
}

function shouldIgnoreStaleRunEvent(payload) {
  return Boolean(payload?.runId && currentRunId && payload.runId !== currentRunId);
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
    addMessageCard(item.type || "assistant", item.role || "CreativeClaw", item.content || "", artifactsForHistoryItem(item), false);
  }
  scrollToBottom();
}

function artifactsForHistoryItem(item) {
  if (Array.isArray(item?.artifacts) && item.artifacts.length > 0) {
    return item.artifacts;
  }
  return extractArtifactsFromContent(item?.content || "");
}

function extractArtifactsFromContent(content) {
  const artifacts = [];
  const linkPattern = /\[([^\]\n]+)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g;
  let match = linkPattern.exec(content);
  while (match) {
    const artifact = artifactFromMarkdownLink(match[1], match[2]);
    if (artifact && !artifacts.some((item) => artifactKey(item) === artifactKey(artifact))) {
      artifacts.push(artifact);
    }
    match = linkPattern.exec(content);
  }
  return artifacts;
}

function artifactFromMarkdownLink(label, rawUrl) {
  const url = normalizeWorkspaceUrl(rawUrl);
  if (!url || !isPreviewableArtifactUrl(url)) {
    return null;
  }

  const path = workspacePathFromUrl(url);
  const extension = artifactExtension({ path, url });
  return {
    name: artifactNameFromLink(label, path, url),
    path,
    url,
    mimeType: mimeTypeForExtension(extension),
    isImage: [".gif", ".jpeg", ".jpg", ".png", ".svg", ".webp"].includes(extension),
  };
}

function normalizeWorkspaceUrl(rawUrl) {
  const value = String(rawUrl || "").trim();
  if (!value) {
    return "";
  }

  if (value.startsWith("/")) {
    return value;
  }
  if (value.startsWith(`${window.location.host}/`)) {
    return value.slice(window.location.host.length);
  }
  if (value.startsWith("workspace/")) {
    return `/${value}`;
  }

  try {
    const parsed = new URL(value);
    if (parsed.origin === window.location.origin) {
      return `${parsed.pathname}${parsed.search}${parsed.hash}`;
    }
  } catch {
    const workspaceIndex = value.indexOf("/workspace/");
    if (workspaceIndex >= 0) {
      return value.slice(workspaceIndex);
    }
  }

  return value;
}

function isPreviewableArtifactUrl(url) {
  const extension = artifactExtension({ url });
  return (
    url.startsWith("/workspace/") &&
    [".gif", ".htm", ".html", ".jpeg", ".jpg", ".pdf", ".png", ".ppt", ".pptx", ".svg", ".webm", ".mp4", ".mov", ".webp"].includes(
      extension
    )
  );
}

function workspacePathFromUrl(url) {
  return String(url || "").replace(/^\/workspace\//, "").split("?")[0].split("#")[0];
}

function artifactNameFromLink(label, path, url) {
  const cleaned = String(label || "").replace(/\s+/g, " ").trim();
  const pathText = String(path || "").trim();
  if (cleaned && pathText && cleaned.includes(pathText)) {
    const name = cleaned.slice(0, cleaned.indexOf(pathText)).trim();
    if (name) {
      return name;
    }
  }
  if (cleaned && !cleaned.startsWith("generated/")) {
    return cleaned;
  }
  return String(path || url || "artifact").split("/").filter(Boolean).pop() || "artifact";
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
    initializeProgressCard(activeProgressCard);
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
  activeProgressCard.querySelector(".progress-summary").textContent = summarizeProgressContent(content, rawTitle);
  activeProgressCard.querySelector(".progress-body").innerHTML = renderMarkdown(content);
  scrollToBottom();
}

function initializeProgressCard(card) {
  const toggle = card.querySelector(".progress-toggle");
  const body = card.querySelector(".progress-body");
  const bodyId = `progress-body-${++progressBodyCounter}`;
  body.id = bodyId;
  toggle.setAttribute("aria-controls", bodyId);
  toggle.addEventListener("click", () => {
    setProgressExpanded(card, toggle.getAttribute("aria-expanded") !== "true");
  });
  setProgressExpanded(card, false);
}

function setProgressExpanded(card, expanded) {
  const toggle = card.querySelector(".progress-toggle");
  const body = card.querySelector(".progress-body");
  card.classList.toggle("expanded", expanded);
  card.classList.toggle("collapsed", !expanded);
  card.dataset.expanded = expanded ? "true" : "false";
  toggle.setAttribute("aria-expanded", expanded ? "true" : "false");
  body.hidden = !expanded;
}

function summarizeProgressContent(content, fallbackTitle) {
  const fallback = fallbackTitle && !HIDDEN_PROGRESS_TITLES.has(fallbackTitle) ? fallbackTitle : "Working on the request.";
  const lines = String(content || "")
    .replace(/\r\n?/g, "\n")
    .split("\n")
    .map((line) => plainProgressLine(line))
    .filter(Boolean);
  const normalizedTitle = plainProgressLine(fallbackTitle);
  const candidates = lines.filter((line) => {
    const lower = line.toLowerCase();
    return line !== normalizedTitle && !lower.startsWith("args:");
  });
  const preferred =
    candidates.find((line) => /^result:/i.test(line)) ||
    candidates.find((line) => !/^status:/i.test(line)) ||
    candidates[0] ||
    normalizedTitle ||
    fallback;
  return truncateProgressSummary(preferred.replace(/^(current progress|result|status):\s*/i, ""));
}

function plainProgressLine(line) {
  return String(line || "")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/\*([^*]+)\*/g, "$1")
    .replace(/\[(.*?)\]\((.*?)\)/g, "$1")
    .replace(/^\s*[-*+]\s+/, "")
    .replace(/^\s*\d+\.\s+/, "")
    .replace(/^#{1,6}\s+/, "")
    .replace(/[>_~]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function truncateProgressSummary(text) {
  const normalized = String(text || "").trim() || "Working on the request.";
  if (normalized.length <= 136) {
    return normalized;
  }
  return `${normalized.slice(0, 133).trimEnd()}...`;
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
    const hasMedia = artifact.isImage || isVideoArtifact(artifact);
    const anchor = document.createElement("a");
    anchor.className = `artifact-card${previewTab ? " previewable" : ""}${hasMedia ? " has-media" : " file-artifact"}`;
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
  unmountTldrawCanvas();
  resetMediaCanvasState();
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

  const mergedGroups = buildEmptyPreviewGroups();
  for (const artifact of previewArtifactsByTab.tldraw) {
    addUniqueArtifact(mergedGroups.tldraw, artifact);
  }
  for (const artifact of groups.tldraw) {
    addUniqueArtifact(mergedGroups.tldraw, artifact);
  }
  mergedGroups.html = groups.html.length > 0 ? groups.html : previewArtifactsByTab.html;
  mergedGroups.ppt = groups.ppt.length > 0 ? groups.ppt : previewArtifactsByTab.ppt;

  previewArtifactsByTab = mergedGroups;
  const previousSelections = { ...selectedPreviewByTab };
  selectedPreviewByTab = buildEmptyPreviewSelections();
  for (const tabName of PREVIEW_TABS) {
    selectedPreviewByTab[tabName] = mergedGroups[tabName][0] || null;
  }
  if (groups.tldraw.length > 0) {
    selectedPreviewByTab.tldraw = groups.tldraw[groups.tldraw.length - 1];
  }
  for (const tabName of PREVIEW_TABS) {
    if (
      groups[tabName].length === 0 &&
      previousSelections[tabName] &&
      mergedGroups[tabName].some((artifact) => artifactKey(artifact) === artifactKey(previousSelections[tabName]))
    ) {
      selectedPreviewByTab[tabName] = previousSelections[tabName];
    }
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
}

function renderAllPreviewViews() {
  for (const tabName of PREVIEW_TABS) {
    renderPreviewView(tabName);
  }
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
  unmountTldrawCanvas();
  tldrawPreview.innerHTML = "";
  const artifacts = previewArtifactsByTab.tldraw;
  const imageArtifacts = artifacts.filter((artifact) => artifact.isImage && !isVideoArtifact(artifact));

  if (window.CreativeClawTldraw?.mount) {
    const shell = document.createElement("div");
    shell.className = "tldraw-sketch-shell";
    const host = document.createElement("div");
    host.className = "tldraw-sketch-host";
    shell.appendChild(host);
    tldrawPreview.appendChild(shell);
    tldrawCanvasUnmount = window.CreativeClawTldraw.mount(host, {
      artifacts: imageArtifacts,
      sessionId,
      onSubmitSketch: handleTldrawSketchSubmit,
    });
    return;
  }

  if (!artifacts.length) {
    tldrawPreview.appendChild(previewEmpty("No sketch preview"));
    return;
  }

  renderMediaCanvasPreview(artifacts);
}

function unmountTldrawCanvas() {
  if (typeof tldrawCanvasUnmount === "function") {
    tldrawCanvasUnmount();
  }
  tldrawCanvasUnmount = null;
}

function renderMediaCanvasPreview(artifacts = previewArtifactsByTab.tldraw) {
  tldrawPreview.innerHTML = "";
  if (!artifacts.length) {
    tldrawPreview.appendChild(previewEmpty("No media preview"));
    return;
  }

  const board = document.createElement("div");
  board.className = "media-canvas-board";
  board.tabIndex = 0;
  board.setAttribute("aria-label", "Image and video canvas");

  const world = document.createElement("div");
  world.className = "media-canvas-world";

  const signature = mediaCanvasSignature(artifacts);
  if (mediaCanvasState.signature !== signature) {
    mediaCanvasState.signature = signature;
    mediaCanvasState.fitSignature = "";
  }
  ensureMediaCanvasLayout(artifacts);

  if (!selectedPreviewByTab.tldraw) {
    selectedPreviewByTab.tldraw = artifacts[0];
  }

  artifacts.forEach((artifact, index) => {
    world.appendChild(buildMediaCanvasNode(artifact, index, artifacts.length, board));
  });

  board.appendChild(world);
  board.appendChild(buildMediaCanvasControls(board, world, artifacts));
  tldrawPreview.appendChild(board);
  setupMediaCanvasInteractions(board, world, artifacts);

  requestAnimationFrame(() => {
    if (mediaCanvasState.fitSignature !== signature) {
      fitMediaCanvas(board, artifacts);
      mediaCanvasState.fitSignature = signature;
    }
    applyMediaCanvasTransform(world);
  });
}

function resetMediaCanvasState() {
  mediaCanvasState.zoom = 1;
  mediaCanvasState.panX = 0;
  mediaCanvasState.panY = 0;
  mediaCanvasState.signature = "";
  mediaCanvasState.fitSignature = "";
  mediaCanvasState.positions.clear();
}

function mediaCanvasSignature(artifacts) {
  return artifacts.map((artifact) => artifactKey(artifact)).join("|");
}

function ensureMediaCanvasLayout(artifacts) {
  artifacts.forEach((artifact, index) => {
    const key = artifactKey(artifact);
    if (!mediaCanvasState.positions.has(key)) {
      mediaCanvasState.positions.set(key, defaultMediaCanvasPosition(index, artifacts.length));
    }
  });
}

function defaultMediaCanvasPosition(index, count) {
  if (count === 1) {
    return { x: 0, y: 0 };
  }
  const columns = Math.max(2, Math.ceil(Math.sqrt(count)));
  const width = mediaCanvasNodeWidth(count);
  const height = Math.round(width * 0.68);
  const gap = 34;
  return {
    x: (index % columns) * (width + gap),
    y: Math.floor(index / columns) * (height + gap),
  };
}

function mediaCanvasNodeWidth(count) {
  if (count <= 1) return 720;
  if (count <= 4) return 380;
  if (count <= 9) return 320;
  return 280;
}

function buildMediaCanvasNode(artifact, index, count, board) {
  const key = artifactKey(artifact);
  const position = mediaCanvasState.positions.get(key) || defaultMediaCanvasPosition(index, count);
  const node = document.createElement("article");
  node.className = "media-canvas-node";
  node.classList.toggle("active", artifactKey(selectedPreviewByTab.tldraw) === key);
  node.dataset.artifactKey = key;
  node.style.width = `${mediaCanvasNodeWidth(count)}px`;
  node.style.transform = `translate3d(${position.x}px, ${position.y}px, 0)`;

  const badge = document.createElement("div");
  badge.className = "media-canvas-badge";
  badge.textContent = String(index + 1);
  node.appendChild(badge);

  if (isVideoArtifact(artifact)) {
    const video = document.createElement("video");
    video.src = artifact.url;
    video.controls = true;
    video.playsInline = true;
    video.preload = "metadata";
    node.appendChild(video);
  } else {
    const image = document.createElement("img");
    image.src = artifact.url;
    image.alt = artifact.name || "artifact";
    image.draggable = false;
    node.appendChild(image);
  }

  setupMediaNodeDrag(node, artifact, board);
  return node;
}

function setupMediaNodeDrag(node, artifact, board) {
  let dragStart = null;
  node.addEventListener("click", () => {
    selectMediaCanvasNode(board, artifactKey(artifact));
    selectedPreviewByTab.tldraw = artifact;
  });
  node.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || event.target.closest("video")) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    const key = artifactKey(artifact);
    const position = mediaCanvasState.positions.get(key) || { x: 0, y: 0 };
    dragStart = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      nodeX: position.x,
      nodeY: position.y,
      key,
    };
    selectedPreviewByTab.tldraw = artifact;
    selectMediaCanvasNode(board, key);
    node.classList.add("is-dragging");
    node.setPointerCapture(event.pointerId);
  });
  node.addEventListener("pointermove", (event) => {
    if (!dragStart || event.pointerId !== dragStart.pointerId) {
      return;
    }
    const x = dragStart.nodeX + (event.clientX - dragStart.startX) / mediaCanvasState.zoom;
    const y = dragStart.nodeY + (event.clientY - dragStart.startY) / mediaCanvasState.zoom;
    mediaCanvasState.positions.set(dragStart.key, { x, y });
    node.style.transform = `translate3d(${x}px, ${y}px, 0)`;
  });
  const finishDrag = (event) => {
    if (!dragStart || event.pointerId !== dragStart.pointerId) {
      return;
    }
    node.classList.remove("is-dragging");
    node.releasePointerCapture(event.pointerId);
    dragStart = null;
  };
  node.addEventListener("pointerup", finishDrag);
  node.addEventListener("pointercancel", finishDrag);
}

function selectMediaCanvasNode(board, key) {
  for (const node of board.querySelectorAll(".media-canvas-node")) {
    node.classList.toggle("active", node.dataset.artifactKey === key);
  }
}

function buildMediaCanvasControls(board, world, artifacts) {
  const controls = document.createElement("div");
  controls.className = "media-canvas-controls";
  controls.appendChild(buildMediaCanvasButton("−", "Zoom out", () => zoomMediaCanvas(board, world, 0.86)));
  controls.appendChild(buildMediaCanvasButton("+", "Zoom in", () => zoomMediaCanvas(board, world, 1.16)));
  controls.appendChild(buildMediaCanvasButton("⌖", "Fit canvas", () => {
    fitMediaCanvas(board, artifacts);
    applyMediaCanvasTransform(world);
  }));
  return controls;
}

function buildMediaCanvasButton(label, title, onClick) {
  const button = document.createElement("button");
  button.className = "media-canvas-control";
  button.type = "button";
  button.title = title;
  button.setAttribute("aria-label", title);
  button.textContent = label;
  button.addEventListener("click", onClick);
  return button;
}

function setupMediaCanvasInteractions(board, world, artifacts) {
  board.addEventListener(
    "wheel",
    (event) => {
      event.preventDefault();
      zoomMediaCanvas(board, world, event.deltaY > 0 ? 0.9 : 1.1, event.clientX, event.clientY);
    },
    { passive: false }
  );

  let panStart = null;
  board.addEventListener("pointerdown", (event) => {
    const isBackground = event.target === board || event.target === world;
    if (!isBackground && event.button !== 1) {
      return;
    }
    event.preventDefault();
    panStart = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      panX: mediaCanvasState.panX,
      panY: mediaCanvasState.panY,
    };
    board.classList.add("is-panning");
    board.setPointerCapture(event.pointerId);
  });
  board.addEventListener("pointermove", (event) => {
    if (!panStart || event.pointerId !== panStart.pointerId) {
      return;
    }
    mediaCanvasState.panX = panStart.panX + event.clientX - panStart.startX;
    mediaCanvasState.panY = panStart.panY + event.clientY - panStart.startY;
    applyMediaCanvasTransform(world);
  });
  const finishPan = (event) => {
    if (!panStart || event.pointerId !== panStart.pointerId) {
      return;
    }
    board.classList.remove("is-panning");
    board.releasePointerCapture(event.pointerId);
    panStart = null;
  };
  board.addEventListener("pointerup", finishPan);
  board.addEventListener("pointercancel", finishPan);
  board.addEventListener("keydown", (event) => {
    if (event.key === "0") {
      fitMediaCanvas(board, artifacts);
      applyMediaCanvasTransform(world);
    }
  });
}

function zoomMediaCanvas(board, world, factor, clientX, clientY) {
  const previousZoom = mediaCanvasState.zoom;
  const nextZoom = clamp(previousZoom * factor, MEDIA_CANVAS_MIN_ZOOM, MEDIA_CANVAS_MAX_ZOOM);
  if (nextZoom === previousZoom) {
    return;
  }
  const rect = board.getBoundingClientRect();
  const originX = typeof clientX === "number" ? clientX - rect.left : rect.width / 2;
  const originY = typeof clientY === "number" ? clientY - rect.top : rect.height / 2;
  const worldX = (originX - mediaCanvasState.panX) / previousZoom;
  const worldY = (originY - mediaCanvasState.panY) / previousZoom;
  mediaCanvasState.zoom = nextZoom;
  mediaCanvasState.panX = originX - worldX * nextZoom;
  mediaCanvasState.panY = originY - worldY * nextZoom;
  applyMediaCanvasTransform(world);
}

function fitMediaCanvas(board, artifacts) {
  const bounds = mediaCanvasBounds(artifacts);
  const rect = board.getBoundingClientRect();
  if (!rect.width || !rect.height || !bounds) {
    return;
  }
  const padding = 68;
  const zoomX = (rect.width - padding * 2) / bounds.width;
  const zoomY = (rect.height - padding * 2) / bounds.height;
  mediaCanvasState.zoom = clamp(Math.min(zoomX, zoomY, 1.06), MEDIA_CANVAS_MIN_ZOOM, MEDIA_CANVAS_MAX_ZOOM);
  mediaCanvasState.panX = rect.width / 2 - (bounds.x + bounds.width / 2) * mediaCanvasState.zoom;
  mediaCanvasState.panY = rect.height / 2 - (bounds.y + bounds.height / 2) * mediaCanvasState.zoom;
}

function mediaCanvasBounds(artifacts) {
  if (!artifacts.length) {
    return null;
  }
  const widths = artifacts.map((artifact, index) => {
    const position = mediaCanvasState.positions.get(artifactKey(artifact)) || defaultMediaCanvasPosition(index, artifacts.length);
    const width = mediaCanvasNodeWidth(artifacts.length);
    const height = Math.round(width * 0.68);
    return { x: position.x, y: position.y, width, height };
  });
  const minX = Math.min(...widths.map((item) => item.x));
  const minY = Math.min(...widths.map((item) => item.y));
  const maxX = Math.max(...widths.map((item) => item.x + item.width));
  const maxY = Math.max(...widths.map((item) => item.y + item.height));
  return { x: minX, y: minY, width: maxX - minX, height: maxY - minY };
}

function applyMediaCanvasTransform(world) {
  world.style.transform = `translate3d(${mediaCanvasState.panX}px, ${mediaCanvasState.panY}px, 0) scale(${mediaCanvasState.zoom})`;
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function renderHtmlPreview() {
  htmlPreview.innerHTML = "";
  const artifact = selectedPreviewByTab.html;
  if (!artifact) {
    htmlPreview.appendChild(previewEmpty("No Design preview"));
    return;
  }

  const refreshButton = document.createElement("button");
  refreshButton.className = "preview-action";
  refreshButton.type = "button";
  refreshButton.textContent = "Refresh";

  const viewport = document.createElement("div");
  viewport.className = "html-preview-viewport";

  const stage = document.createElement("div");
  stage.className = "html-preview-stage";

  const interactionLayer = document.createElement("div");
  interactionLayer.className = "html-preview-interaction-layer";
  interactionLayer.setAttribute("aria-label", "Design preview canvas controls");

  const iframe = document.createElement("iframe");
  iframe.className = "html-preview-frame";
  iframe.src = artifact.url;
  iframe.setAttribute("sandbox", "allow-scripts allow-same-origin allow-forms allow-popups allow-downloads");
  iframe.setAttribute("scrolling", "no");
  iframe.title = artifact.name || "HTML preview";

  const zoomLabel = document.createElement("span");
  zoomLabel.className = "preview-zoom-label";
  const zoomOutButton = buildHtmlPreviewZoomButton("−", "Zoom out", () => {
    setHtmlPreviewZoom(htmlPreviewZoom - HTML_PREVIEW_ZOOM_STEP, viewport, zoomLabel, htmlPreviewViewportCenter(viewport));
  });
  const zoomInButton = buildHtmlPreviewZoomButton("+", "Zoom in", () => {
    setHtmlPreviewZoom(htmlPreviewZoom + HTML_PREVIEW_ZOOM_STEP, viewport, zoomLabel, htmlPreviewViewportCenter(viewport));
  });
  const fitButton = buildHtmlPreviewZoomButton("Fit", "Fit preview", () => {
    fitHtmlPreviewToViewport(viewport, zoomLabel);
  });

  refreshButton.addEventListener("click", () => {
    iframe.src = artifact.url;
  });
  iframe.addEventListener("load", () => {
    syncHtmlPreviewDocumentSize(iframe, viewport, zoomLabel);
  });

  htmlPreview.appendChild(buildPreviewToolbar(artifact, [refreshButton, zoomOutButton, zoomLabel, zoomInButton, fitButton]));
  stage.appendChild(iframe);
  viewport.appendChild(stage);
  viewport.appendChild(interactionLayer);
  htmlPreview.appendChild(viewport);
  applyHtmlPreviewZoom(viewport, zoomLabel);
  setupHtmlPreviewCanvasInteractions(viewport, interactionLayer, zoomLabel);
}

function buildHtmlPreviewZoomButton(label, title, onClick) {
  const button = document.createElement("button");
  button.className = "preview-action";
  button.type = "button";
  button.textContent = label;
  button.title = title;
  button.setAttribute("aria-label", title);
  button.addEventListener("click", onClick);
  return button;
}

function setHtmlPreviewZoom(nextZoom, viewport, zoomLabel, options = {}) {
  const previousZoom = htmlPreviewZoom;
  htmlPreviewZoom = Math.round(clamp(nextZoom, HTML_PREVIEW_MIN_ZOOM, HTML_PREVIEW_MAX_ZOOM) * 100) / 100;
  const focalPoint = typeof options.clientX === "number" && typeof options.clientY === "number"
    ? options
    : htmlPreviewViewportCenter(viewport);
  if (viewport && focalPoint && typeof focalPoint.clientX === "number" && typeof focalPoint.clientY === "number") {
    preserveHtmlPreviewZoomPoint(viewport, previousZoom, htmlPreviewZoom, focalPoint.clientX, focalPoint.clientY);
  }
  applyHtmlPreviewZoom(viewport, zoomLabel);
}

function resetHtmlPreviewZoom(viewport, zoomLabel) {
  htmlPreviewZoom = 1;
  htmlPreviewPanX = 0;
  htmlPreviewPanY = 0;
  applyHtmlPreviewZoom(viewport, zoomLabel);
}

function fitHtmlPreviewToViewport(viewport, zoomLabel) {
  const stage = viewport?.querySelector(".html-preview-stage");
  const viewportRect = viewport?.getBoundingClientRect();
  if (!stage || !viewportRect?.width || !viewportRect?.height) {
    resetHtmlPreviewZoom(viewport, zoomLabel);
    return;
  }
  const stageWidth = Number(stage.dataset.contentWidth) || stage.offsetWidth || viewportRect.width;
  const stageHeight = Number(stage.dataset.contentHeight) || stage.offsetHeight || viewportRect.height;
  const nextZoom = clamp(
    Math.min(viewportRect.width / stageWidth, viewportRect.height / stageHeight, 1),
    HTML_PREVIEW_MIN_ZOOM,
    HTML_PREVIEW_MAX_ZOOM
  );
  htmlPreviewZoom = Math.round(nextZoom * 100) / 100;
  htmlPreviewPanX = (viewportRect.width - stageWidth * htmlPreviewZoom) / 2;
  htmlPreviewPanY = (viewportRect.height - stageHeight * htmlPreviewZoom) / 2;
  applyHtmlPreviewZoom(viewport, zoomLabel);
}

function htmlPreviewViewportCenter(viewport) {
  if (!viewport) {
    return {};
  }
  const rect = viewport.getBoundingClientRect();
  return {
    clientX: rect.left + rect.width / 2,
    clientY: rect.top + rect.height / 2,
  };
}

function preserveHtmlPreviewZoomPoint(viewport, previousZoom, nextZoom, clientX, clientY) {
  if (!previousZoom || previousZoom === nextZoom) {
    return;
  }
  const rect = viewport.getBoundingClientRect();
  const originX = clientX - rect.left;
  const originY = clientY - rect.top;
  const contentX = (originX - htmlPreviewPanX) / previousZoom;
  const contentY = (originY - htmlPreviewPanY) / previousZoom;
  htmlPreviewPanX = originX - contentX * nextZoom;
  htmlPreviewPanY = originY - contentY * nextZoom;
}

function applyHtmlPreviewZoom(viewport, zoomLabel) {
  if (viewport) {
    viewport.style.setProperty("--html-preview-zoom", htmlPreviewZoom.toFixed(2));
    const stage = viewport.querySelector(".html-preview-stage");
    if (stage) {
      stage.style.transform = `translate3d(${htmlPreviewPanX}px, ${htmlPreviewPanY}px, 0) scale(${htmlPreviewZoom})`;
    }
  }
  if (zoomLabel) {
    zoomLabel.textContent = `${Math.round(htmlPreviewZoom * 100)}%`;
  }
}

function syncHtmlPreviewDocumentSize(iframe, viewport, zoomLabel) {
  const stage = viewport?.querySelector(".html-preview-stage");
  if (!iframe || !viewport || !stage) {
    return;
  }

  const setStageSize = (width, height) => {
    const viewportRect = viewport.getBoundingClientRect();
    const nextWidth = clamp(
      Math.ceil(Math.max(width || 0, viewportRect.width || 1)),
      1,
      HTML_PREVIEW_MAX_STAGE_SIZE
    );
    const nextHeight = clamp(
      Math.ceil(Math.max(height || 0, viewportRect.height || 1)),
      1,
      HTML_PREVIEW_MAX_STAGE_SIZE
    );
    stage.style.width = `${nextWidth}px`;
    stage.style.height = `${nextHeight}px`;
    stage.dataset.contentWidth = String(nextWidth);
    stage.dataset.contentHeight = String(nextHeight);
    iframe.style.width = `${nextWidth}px`;
    iframe.style.height = `${nextHeight}px`;
    applyHtmlPreviewZoom(viewport, zoomLabel);
  };

  setStageSize(viewport.clientWidth, viewport.clientHeight);

  let doc;
  try {
    doc = iframe.contentDocument || iframe.contentWindow?.document;
  } catch {
    return;
  }
  if (!doc?.documentElement) {
    return;
  }

  injectHtmlPreviewNoScrollStyle(doc);

  const measure = () => {
    const root = doc.documentElement;
    const body = doc.body;
    let width = Math.max(root.scrollWidth, root.offsetWidth, root.clientWidth);
    let height = Math.max(root.scrollHeight, root.offsetHeight, root.clientHeight);
    if (body) {
      width = Math.max(width, body.scrollWidth, body.offsetWidth, body.clientWidth);
      height = Math.max(height, body.scrollHeight, body.offsetHeight, body.clientHeight);
      for (const element of body.querySelectorAll("*")) {
        const rect = element.getBoundingClientRect();
        if (!rect.width && !rect.height) {
          continue;
        }
        width = Math.max(width, rect.right);
        height = Math.max(height, rect.bottom);
      }
    }
    setStageSize(width, height);
  };

  requestAnimationFrame(measure);
  window.setTimeout(measure, 120);
  window.setTimeout(measure, 600);
  window.setTimeout(measure, 1600);
}

function injectHtmlPreviewNoScrollStyle(doc) {
  if (!doc.getElementById("creative-claw-preview-no-scroll")) {
    const style = doc.createElement("style");
    style.id = "creative-claw-preview-no-scroll";
    style.textContent = `
      html, body {
        scrollbar-width: none !important;
        -ms-overflow-style: none !important;
      }
      html::-webkit-scrollbar,
      body::-webkit-scrollbar,
      *::-webkit-scrollbar {
        display: none !important;
        width: 0 !important;
        height: 0 !important;
      }
      html,
      body {
        overflow: hidden !important;
      }
    `;
    (doc.head || doc.documentElement).appendChild(style);
  }
  doc.documentElement.style.overflow = "hidden";
  doc.documentElement.style.scrollbarWidth = "none";
  if (doc.body) {
    doc.body.style.overflow = "hidden";
    doc.body.style.scrollbarWidth = "none";
  }
}

function setupHtmlPreviewCanvasInteractions(viewport, interactionLayer, zoomLabel) {
  const handleWheel = (event) => {
    event.preventDefault();
    event.stopPropagation();
    if (event.ctrlKey || event.metaKey) {
      const factor = Math.exp(-event.deltaY * 0.002);
      setHtmlPreviewZoom(htmlPreviewZoom * factor, viewport, zoomLabel, {
        clientX: event.clientX,
        clientY: event.clientY,
      });
      return;
    }
    htmlPreviewPanX -= event.deltaX;
    htmlPreviewPanY -= event.deltaY;
    applyHtmlPreviewZoom(viewport, zoomLabel);
  };

  viewport.addEventListener("wheel", handleWheel, { passive: false });
  interactionLayer.addEventListener("wheel", handleWheel, { passive: false });

  let panStart = null;
  const activePointers = new Map();
  let pinchStart = null;

  interactionLayer.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    try {
      interactionLayer.setPointerCapture(event.pointerId);
    } catch {
      // Some synthetic events used by browser tests do not register as active pointers.
    }
    activePointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    interactionLayer.classList.add("is-panning");
    if (activePointers.size === 1) {
      panStart = {
        pointerId: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        panX: htmlPreviewPanX,
        panY: htmlPreviewPanY,
      };
      pinchStart = null;
    } else if (activePointers.size === 2) {
      panStart = null;
      pinchStart = buildHtmlPreviewPinchState(activePointers, viewport);
    }
  });

  interactionLayer.addEventListener("pointermove", (event) => {
    if (!activePointers.has(event.pointerId)) {
      return;
    }
    event.preventDefault();
    activePointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (activePointers.size >= 2 && pinchStart) {
      const current = buildHtmlPreviewPinchState(activePointers, viewport);
      if (current.distance > 0 && pinchStart.distance > 0) {
        htmlPreviewZoom = Math.round(
          clamp(
            pinchStart.zoom * (current.distance / pinchStart.distance),
            HTML_PREVIEW_MIN_ZOOM,
            HTML_PREVIEW_MAX_ZOOM
          ) * 100
        ) / 100;
        htmlPreviewPanX = current.originX - pinchStart.contentX * htmlPreviewZoom;
        htmlPreviewPanY = current.originY - pinchStart.contentY * htmlPreviewZoom;
        applyHtmlPreviewZoom(viewport, zoomLabel);
      }
      return;
    }
    if (panStart && event.pointerId === panStart.pointerId) {
      htmlPreviewPanX = panStart.panX + event.clientX - panStart.startX;
      htmlPreviewPanY = panStart.panY + event.clientY - panStart.startY;
      applyHtmlPreviewZoom(viewport, zoomLabel);
    }
  });

  const finishPointer = (event) => {
    activePointers.delete(event.pointerId);
    if (interactionLayer.hasPointerCapture(event.pointerId)) {
      try {
        interactionLayer.releasePointerCapture(event.pointerId);
      } catch {
        // Pointer capture may already be gone after cancellation.
      }
    }
    if (activePointers.size === 0) {
      panStart = null;
      pinchStart = null;
      interactionLayer.classList.remove("is-panning");
    } else if (activePointers.size === 1) {
      const [remaining] = activePointers.entries();
      panStart = {
        pointerId: remaining[0],
        startX: remaining[1].x,
        startY: remaining[1].y,
        panX: htmlPreviewPanX,
        panY: htmlPreviewPanY,
      };
      pinchStart = null;
    }
  };

  interactionLayer.addEventListener("pointerup", finishPointer);
  interactionLayer.addEventListener("pointercancel", finishPointer);

  let gestureStartZoom = htmlPreviewZoom;
  viewport.addEventListener(
    "gesturestart",
    (event) => {
      gestureStartZoom = htmlPreviewZoom;
      event.preventDefault();
    },
    { passive: false }
  );
  viewport.addEventListener(
    "gesturechange",
    (event) => {
      event.preventDefault();
      setHtmlPreviewZoom(gestureStartZoom * Number(event.scale || 1), viewport, zoomLabel, {
        clientX: event.clientX,
        clientY: event.clientY,
      });
    },
    { passive: false }
  );
}

function buildHtmlPreviewPinchState(activePointers, viewport) {
  const points = Array.from(activePointers.values()).slice(0, 2);
  const rect = viewport?.getBoundingClientRect();
  if (points.length < 2) {
    const centerX = points[0]?.x || 0;
    const centerY = points[0]?.y || 0;
    const originX = rect ? centerX - rect.left : centerX;
    const originY = rect ? centerY - rect.top : centerY;
    return {
      centerX,
      centerY,
      originX,
      originY,
      contentX: (originX - htmlPreviewPanX) / htmlPreviewZoom,
      contentY: (originY - htmlPreviewPanY) / htmlPreviewZoom,
      distance: 0,
      zoom: htmlPreviewZoom,
      panX: htmlPreviewPanX,
      panY: htmlPreviewPanY,
    };
  }
  const centerX = (points[0].x + points[1].x) / 2;
  const centerY = (points[0].y + points[1].y) / 2;
  const originX = rect ? centerX - rect.left : centerX;
  const originY = rect ? centerY - rect.top : centerY;
  return {
    centerX,
    centerY,
    originX,
    originY,
    contentX: (originX - htmlPreviewPanX) / htmlPreviewZoom,
    contentY: (originY - htmlPreviewPanY) / htmlPreviewZoom,
    distance: Math.hypot(points[0].x - points[1].x, points[0].y - points[1].y),
    zoom: htmlPreviewZoom,
    panX: htmlPreviewPanX,
    panY: htmlPreviewPanY,
  };
}

function renderPptPreview() {
  pptPreview.innerHTML = "";
  const artifact = selectedPreviewByTab.ppt;
  if (!artifact) {
    pptPreview.appendChild(previewEmpty("No PPT preview"));
    return;
  }

  pptPreview.appendChild(buildPreviewToolbar(artifact, [], { showMeta: false }));

  if (isPdfArtifact(artifact) || isPptxArtifact(artifact)) {
    const iframe = document.createElement("iframe");
    iframe.className = "ppt-preview-frame";
    iframe.src = previewUrlForArtifact(artifact);
    iframe.title = artifact.name || "PPT preview";
    iframe.addEventListener("load", () => hideEmbeddedPptChrome(iframe));
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

  card.appendChild(icon);
  card.appendChild(copy);
  pptPreview.appendChild(card);
}

function hideEmbeddedPptChrome(iframe) {
  try {
    const document = iframe.contentDocument;
    if (!document?.head) {
      return;
    }
    const style = document.createElement("style");
    style.textContent = "header { display: none !important; }";
    document.head.appendChild(style);
  } catch {
    // Cross-origin or browser-restricted previews can still render without this polish.
  }
}

function buildPreviewToolbar(artifact, actions = [], options = {}) {
  const toolbar = document.createElement("div");
  toolbar.className = "preview-toolbar";
  const showMeta = options.showMeta !== false;
  if (!showMeta) {
    toolbar.classList.add("preview-toolbar--actions-only");
  }

  let meta = null;
  if (showMeta) {
    meta = document.createElement("div");
    meta.className = "preview-file";
    const name = document.createElement("div");
    name.className = "preview-file-name";
    name.textContent = artifact.name || "artifact";
    const path = document.createElement("div");
    path.className = "preview-file-path";
    path.textContent = artifact.path || artifact.mimeType || "";
    meta.appendChild(name);
    meta.appendChild(path);
  }

  const actionGroup = document.createElement("div");
  actionGroup.className = "preview-actions";
  for (const action of actions) {
    actionGroup.appendChild(action);
  }
  if (options.showOpen !== false) {
    const open = document.createElement("a");
    open.className = "preview-action";
    open.href = artifact.url;
    open.target = "_blank";
    open.rel = "noreferrer";
    open.textContent = "Open";
    actionGroup.appendChild(open);
  }

  if (meta) {
    toolbar.appendChild(meta);
  }
  if (actionGroup.children.length > 0) {
    toolbar.appendChild(actionGroup);
  }
  return toolbar;
}

function previewEmpty(text) {
  const empty = document.createElement("div");
  empty.className = "preview-empty";
  const icon = document.createElement("div");
  icon.className = "preview-empty-icon";
  icon.setAttribute("aria-hidden", "true");
  const title = document.createElement("div");
  title.className = "preview-empty-title";
  title.textContent = text;
  const detail = document.createElement("div");
  detail.className = "preview-empty-detail";
  detail.textContent = "Generated artifacts will appear here automatically.";
  empty.appendChild(icon);
  empty.appendChild(title);
  empty.appendChild(detail);
  return empty;
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

function mimeTypeForExtension(extension) {
  const types = {
    ".gif": "image/gif",
    ".htm": "text/html",
    ".html": "text/html",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".mov": "video/quicktime",
    ".mp4": "video/mp4",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".svg": "image/svg+xml",
    ".webm": "video/webm",
    ".webp": "image/webp",
  };
  return types[extension] || "";
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

function updateComposerButtons() {
  const hasUploadingAttachment = attachedFiles.some((attachment) => attachment.status === "uploading");
  const hasUploadedAttachment = attachedFiles.some((attachment) => attachment.status === "uploaded" && attachment.path);
  const hasPromptText = promptInput.value.trim().length > 0;
  const isConnected = socket && socket.readyState === WebSocket.OPEN;
  if (runState === "running") {
    sendButton.disabled = !isConnected;
  } else if (runState === "stopping") {
    sendButton.disabled = true;
  } else {
    sendButton.disabled = hasUploadingAttachment || !isConnected || (!hasPromptText && !hasUploadedAttachment);
  }
  attachButton.disabled = !isConnected || runState !== "idle";
}

function renderAttachments() {
  attachmentList.innerHTML = "";
  attachmentList.hidden = attachedFiles.length === 0;
  for (const attachment of attachedFiles) {
    const chip = document.createElement("div");
    chip.className = `attachment-chip ${attachment.status || "uploading"}`;

    const name = document.createElement("span");
    name.className = "attachment-name";
    name.textContent = attachment.name || "attachment";

    const status = document.createElement("span");
    status.className = "attachment-status";
    if (attachment.status === "uploaded") {
      status.textContent = "ready";
    } else if (attachment.status === "error") {
      status.textContent = "failed";
    } else {
      status.textContent = `${Math.max(0, Math.min(100, attachment.progress || 0))}%`;
    }

    const remove = document.createElement("button");
    remove.className = "attachment-remove";
    remove.type = "button";
    remove.setAttribute("aria-label", `Remove ${attachment.name || "attachment"}`);
    remove.textContent = "×";
    remove.addEventListener("click", () => {
      removeAttachment(attachment.id);
    });

    chip.appendChild(name);
    chip.appendChild(status);
    chip.appendChild(remove);
    attachmentList.appendChild(chip);
  }
  updateComposerButtons();
}

function removeAttachment(attachmentId) {
  const index = attachedFiles.findIndex((attachment) => attachment.id === attachmentId);
  if (index < 0) {
    return;
  }
  const [attachment] = attachedFiles.splice(index, 1);
  attachment.cancelled = true;
  const waiter = uploadWaiters.get(attachment.id);
  if (waiter) {
    uploadWaiters.delete(attachment.id);
    waiter.reject(new Error("Upload cancelled."));
  }
  if (attachment.status === "uploading" && socket && socket.readyState === WebSocket.OPEN) {
    sendSocket({ type: "upload_cancel", uploadId: attachment.id });
  }
  renderAttachments();
}

function clearAttachments() {
  attachedFiles.length = 0;
  renderAttachments();
}

function handleUploadEvent(payload) {
  const type = String(payload?.type || "");
  const uploadId = String(payload?.uploadId || "");
  if (!uploadId || !type.startsWith("upload_")) {
    return false;
  }

  const waiter = uploadWaiters.get(uploadId);
  if (!waiter) {
    return true;
  }

  if (type === "upload_error") {
    uploadWaiters.delete(uploadId);
    waiter.reject(new Error(payload.message || "Upload failed."));
    return true;
  }

  if (type === waiter.expectedType) {
    uploadWaiters.delete(uploadId);
    waiter.resolve(payload);
  }
  return true;
}

function waitForUploadEvent(uploadId, expectedType) {
  return new Promise((resolve, reject) => {
    const timeout = window.setTimeout(() => {
      uploadWaiters.delete(uploadId);
      reject(new Error("Upload timed out."));
    }, 30000);
    uploadWaiters.set(uploadId, {
      expectedType,
      resolve: (payload) => {
        window.clearTimeout(timeout);
        resolve(payload);
      },
      reject: (error) => {
        window.clearTimeout(timeout);
        reject(error);
      },
    });
  });
}

function sendSocket(payload) {
  if (!socket || socket.readyState !== WebSocket.OPEN) {
    throw new Error("Web chat is not connected.");
  }
  socket.send(JSON.stringify(payload));
}

async function handleTldrawSketchSubmit(payload) {
  if (runState !== "idle") {
    throw new Error("Wait for the current run to finish before sending a sketch.");
  }
  if (!socket || socket.readyState !== WebSocket.OPEN) {
    throw new Error("Web chat is not connected.");
  }

  const imageBlob = payload?.imageBlob;
  if (!(imageBlob instanceof Blob)) {
    throw new Error("Sketch export did not include a PNG image.");
  }

  const imageFile = new File([imageBlob], payload.imageName || buildSketchUploadName("png"), { type: "image/png" });
  const snapshotBody = JSON.stringify(payload?.snapshot || {}, null, 2);
  const snapshotFile = new File([snapshotBody], payload.snapshotName || buildSketchUploadName("tldr.json"), {
    type: "application/json",
  });

  await uploadGeneratedFile(imageFile);
  await uploadGeneratedFile(snapshotFile);

  const note = String(payload?.note || "").trim();
  const sketchPrompt =
    note ||
    "请根据 tldraw 草图导出的 PNG 和 JSON 快照优化当前设计。优先理解红线、箭头、圈注、手写标注和布局批注，并把修改落实到 HTML 设计画布。";
  const existingPrompt = promptInput.value.trim();
  promptInput.value = existingPrompt ? `${existingPrompt}\n\n${sketchPrompt}` : sketchPrompt;
  promptInput.style.height = "";
  promptInput.style.height = `${Math.min(promptInput.scrollHeight, 220)}px`;
  await sendPrompt();
}

async function uploadGeneratedFile(file) {
  const attachment = {
    id: crypto.randomUUID(),
    name: file.name || "sketch-export",
    size: file.size,
    mimeType: file.type || "",
    status: "uploading",
    progress: 0,
    path: "",
  };
  attachedFiles.push(attachment);
  renderAttachments();

  try {
    const uploaded = await uploadFile(file, attachment);
    if (attachment.cancelled || !attachedFiles.includes(attachment)) {
      throw new Error("Sketch upload was cancelled.");
    }
    attachment.status = "uploaded";
    attachment.progress = 100;
    attachment.path = uploaded.path || "";
    attachment.mimeType = uploaded.mimeType || attachment.mimeType;
    renderAttachments();
    return attachment;
  } catch (error) {
    if (!attachment.cancelled && attachedFiles.includes(attachment)) {
      attachment.status = "error";
      attachment.error = error instanceof Error ? error.message : "Upload failed.";
      renderAttachments();
    }
    throw error;
  }
}

function buildSketchUploadName(extension) {
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  return `sketch-${stamp}.${extension}`;
}

async function uploadSelectedFiles(files) {
  for (const file of files) {
    const attachment = {
      id: crypto.randomUUID(),
      name: file.name || "attachment",
      size: file.size,
      mimeType: file.type || "",
      status: "uploading",
      progress: 0,
      path: "",
    };
    attachedFiles.push(attachment);
    renderAttachments();

    try {
      const uploaded = await uploadFile(file, attachment);
      if (attachment.cancelled || !attachedFiles.includes(attachment)) {
        continue;
      }
      attachment.status = "uploaded";
      attachment.progress = 100;
      attachment.path = uploaded.path || "";
      attachment.mimeType = uploaded.mimeType || attachment.mimeType;
    } catch (error) {
      if (!attachment.cancelled && attachedFiles.includes(attachment)) {
        attachment.status = "error";
        attachment.error = error instanceof Error ? error.message : "Upload failed.";
      }
    }
    renderAttachments();
  }
}

async function uploadFile(file, attachment) {
  sendSocket({
    type: "upload_start",
    uploadId: attachment.id,
    name: attachment.name,
    size: file.size,
    mimeType: attachment.mimeType,
  });
  await waitForUploadEvent(attachment.id, "upload_started");

  let offset = 0;
  while (offset < file.size) {
    if (attachment.cancelled) {
      throw new Error("Upload cancelled.");
    }
    const nextOffset = Math.min(offset + UPLOAD_CHUNK_SIZE, file.size);
    const chunk = file.slice(offset, nextOffset);
    const data = await blobToBase64(chunk);
    sendSocket({ type: "upload_chunk", uploadId: attachment.id, data });
    const received = await waitForUploadEvent(attachment.id, "upload_chunk_received");
    offset = nextOffset;
    attachment.progress = file.size > 0 ? Math.round(((received.received || offset) / file.size) * 100) : 100;
    renderAttachments();
  }

  sendSocket({ type: "upload_finish", uploadId: attachment.id });
  return waitForUploadEvent(attachment.id, "upload_complete");
}

function blobToBase64(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.addEventListener("load", () => {
      const result = String(reader.result || "");
      resolve(result.includes(",") ? result.split(",", 2)[1] : result);
    });
    reader.addEventListener("error", () => {
      reject(new Error("Could not read the selected file."));
    });
    reader.readAsDataURL(blob);
  });
}

function contentWithAttachments(content, attachments) {
  if (attachments.length === 0) {
    return content;
  }
  const prompt = content || "Please use the attached file(s).";
  const fileLines = attachments.map((attachment) => `- ${attachment.path}`);
  return `${prompt}\n\nAttached files:\n${fileLines.join("\n")}`;
}

async function sendPrompt() {
  if (runState !== "idle") {
    return;
  }
  const uploadedAttachments = attachedFiles.filter((attachment) => attachment.status === "uploaded" && attachment.path);
  const hasUploadingAttachment = attachedFiles.some((attachment) => attachment.status === "uploading");
  if (hasUploadingAttachment) {
    return;
  }

  const content = contentWithAttachments(promptInput.value.trim(), uploadedAttachments);
  if (!content || !socket || socket.readyState !== WebSocket.OPEN) {
    return;
  }
  const runId = crypto.randomUUID();
  sendSocket({ type: "chat", content, runId });
  setRunState("running", runId);
  addMessageCard("user", "You", content);
  appendHistory({ type: "user", role: "You", content, artifacts: [] });
  promptInput.value = "";
  promptInput.style.height = "";
  clearAttachments();
  activeProgressCard = null;
}

function stopCurrentTask() {
  if (runState !== "running" || !currentRunId) {
    return;
  }
  sendSocket({ type: "stop", runId: currentRunId });
  setRunState("stopping", currentRunId);
}

async function handleComposerSubmit() {
  if (runState === "running") {
    stopCurrentTask();
    return;
  }
  if (runState === "stopping") {
    return;
  }
  await sendPrompt();
}

composer.addEventListener("submit", (event) => {
  event.preventDefault();
  void handleComposerSubmit();
});

promptInput.addEventListener("input", () => {
  promptInput.style.height = "";
  promptInput.style.height = `${Math.min(promptInput.scrollHeight, 220)}px`;
  updateComposerButtons();
});

promptInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    void handleComposerSubmit();
  }
});

attachButton.addEventListener("click", () => {
  fileInput.click();
});

fileInput.addEventListener("change", () => {
  const files = Array.from(fileInput.files || []);
  fileInput.value = "";
  void uploadSelectedFiles(files);
});

newSessionButton.addEventListener("click", () => {
  createNewSession();
});

sessionHistoryButton.addEventListener("click", (event) => {
  event.stopPropagation();
  toggleSessionPopover();
});

document.addEventListener("click", (event) => {
  if (sessionPopover.hidden) {
    return;
  }
  const target = event.target;
  if (sessionPopover.contains(target) || sessionHistoryButton.contains(target)) {
    return;
  }
  closeSessionPopover();
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !sessionPopover.hidden) {
    closeSessionPopover();
  }
});

for (const tab of previewTabs) {
  tab.addEventListener("click", () => {
    activatePreviewTab(tab.dataset.previewTab);
  });
}

window.addEventListener("beforeunload", () => {
  disconnect();
});
