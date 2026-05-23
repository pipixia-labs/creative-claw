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
const model3dPreview = document.getElementById("model3d-preview");

const STORAGE_KEY = "creative_claw_webchat_session_id";
const HISTORY_KEY_PREFIX = "creative_claw_webchat_history:";
const SESSION_INDEX_KEY = "creative_claw_webchat_sessions";
const HIDDEN_PROGRESS_TITLES = new Set(["Starting", "Finalize Result"]);
const PROGRESS_STEP_LIMIT = 16;
const PREVIEW_TABS = ["tldraw", "html", "ppt", "model3d"];
const AUTO_PREVIEW_PRIORITY = ["model3d", "ppt", "html", "tldraw"];
const INTERACTIVE_PPT_HTML_KIND = "interactive_ppt_html";
const INLINE_3D_EXTENSIONS = new Set([".fbx", ".glb", ".gltf", ".obj", ".stl", ".usd", ".usda", ".usdc", ".usdz"]);
const MODEL_3D_EXTENSIONS = new Set([".fbx", ".glb", ".gltf", ".obj", ".stl", ".usd", ".usda", ".usdc", ".usdz"]);
const MODEL3D_PREVIEW_EXTENSION_PRIORITY = new Map([
  [".glb", 0],
  [".gltf", 1],
  [".zip", 2],
  [".obj", 3],
  [".fbx", 4],
  [".usdz", 5],
  [".usd", 6],
  [".usda", 7],
  [".usdc", 8],
  [".stl", 9],
]);
const MODEL3D_AUTO_PREVIEW_LIMIT_BYTES = 150 * 1024 * 1024;
const UPLOAD_CHUNK_SIZE = 512 * 1024;
const QUESTION_FORM_STREAM_MARKER = "<cc-question-form";
const ASSISTANT_DELTA_KIND_THINKING_PLACEHOLDER = "thinking_placeholder";
const MEDIA_CANVAS_MIN_ZOOM = 0.45;
const MEDIA_CANVAS_MAX_ZOOM = 2.4;
const HTML_PREVIEW_MIN_ZOOM = 0.1;
const HTML_PREVIEW_MAX_ZOOM = 4;
const HTML_PREVIEW_ZOOM_STEP = 0.1;
const HTML_PREVIEW_MAX_STAGE_SIZE = 40000;
const QUESTION_FORM_REVEAL_STEP_MS = 85;

let sessionId = ensureSessionId();
let socket = null;
let activeProgressCard = null;
let progressCardsByGroup = new Map();
let activeAssistantStream = null;
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
let tldrawCanvasSignature = "";
let tldrawShouldFitSelectedArtifact = false;
let model3dViewerController = null;
const largeModelPreviewApprovals = new Set();
let designSystemsCache = null;
let designSystemsPromise = null;

connect();
restoreHistory();
activatePreviewTab(activePreviewTab);
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
  const leftCreatedTime = Date.parse(left.createdAt || left.updatedAt || "") || 0;
  const rightCreatedTime = Date.parse(right.createdAt || right.updatedAt || "") || 0;
  if (leftCreatedTime !== rightCreatedTime) {
    return rightCreatedTime - leftCreatedTime;
  }
  const leftUpdatedTime = Date.parse(left.updatedAt || "") || 0;
  const rightUpdatedTime = Date.parse(right.updatedAt || "") || 0;
  return rightUpdatedTime - leftUpdatedTime;
}

function buildSessionSummary(foundSessionId, items = loadHistory(foundSessionId), indexed = {}) {
  const firstUser = items.find((item) => item.type === "user" && item.content);
  const firstItem = items.find((item) => item.createdAt);
  const lastItem = [...items].reverse().find((item) => item.createdAt);
  const latestArtifacts = latestSessionArtifacts(items);
  const createdAt = firstItem?.createdAt || indexed.createdAt || indexed.updatedAt || "";
  return {
    id: foundSessionId,
    title: compactText(sessionTitleText(firstUser?.content) || indexed.title || "New session", 42),
    createdAt,
    updatedAt: lastItem?.createdAt || indexed.updatedAt || createdAt,
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
    if (tab === "ppt") labels.add("PPT/PDF");
    if (tab === "model3d") labels.add("3D");
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
    meta.textContent = `${formatSessionTime(summary.createdAt || summary.updatedAt)} · ${summary.count || 0} messages`;
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
  progressCardsByGroup.clear();
  activeAssistantStream = null;
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
    upsertProgressCard(payload.content || "", payload.metadata || {}, { runId: payload.runId || "" });
    return;
  }

  if (payload.type === "assistant_delta") {
    if (shouldIgnoreStaleRunEvent(payload)) {
      return;
    }
    completeProgressCards({ runId: payload.runId || currentRunId });
    activeProgressCard = null;
    appendAssistantDelta(payload);
    return;
  }

  if (payload.type === "assistant_message") {
    if (shouldIgnoreStaleRunEvent(payload)) {
      return;
    }
    completeProgressCards({ runId: payload.runId || currentRunId });
    activeProgressCard = null;
    if (finalizeAssistantStream(payload)) {
      return;
    }
    addMessageCard("assistant", "CreativeClaw", payload.content || "", payload.artifacts || [], true, {
      revealQuestionForms: isQuestionFormStreamContent(payload.content || ""),
    });
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
    completeProgressCards({ runId: payload.runId || currentRunId, status: "failed" });
    activeAssistantStream = null;
    addMessageCard("error", "CreativeClaw", payload.content || payload.message || "Unknown error.");
    appendHistory({
      type: "error",
      role: "CreativeClaw",
      content: payload.content || payload.message || "Unknown error.",
      artifacts: [],
    });
  }

  activeProgressCard = null;
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
    completeProgressCards({ runId: payload.runId, status: "cancelled" });
    addMessageCard("system", "CreativeClaw", "Task stopped.");
    activeAssistantStream = null;
  } else if (payload.reason === "completed") {
    completeProgressCards({ runId: payload.runId });
  } else {
    completeProgressCards({ runId: payload.runId, status: "failed" });
  }
  setRunState("idle");
}

function shouldIgnoreStaleRunEvent(payload) {
  return Boolean(payload?.runId && currentRunId && payload.runId !== currentRunId);
}

function renderEmptyStateIfNeeded() {
  renderEmptyState();
}

function renderEmptyState() {
  removeEmptyState();
}

function clearTimeline() {
  timeline.innerHTML = "";
  activeProgressCard = null;
  progressCardsByGroup.clear();
  activeAssistantStream = null;
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
  if (isWorkspaceRelativePath(value)) {
    return `/workspace/${value}`;
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

function normalizeMarkdownResourceUrl(rawUrl, options = {}) {
  const safeUrl = sanitizeUrl(rawUrl, options);
  if (!safeUrl) {
    return "";
  }
  if (isWorkspaceRelativePath(safeUrl) || safeUrl.startsWith("workspace/") || safeUrl.includes("/workspace/")) {
    return normalizeWorkspaceUrl(safeUrl);
  }
  return safeUrl;
}

function isWorkspaceRelativePath(value) {
  return /^(generated|inbox)\//.test(String(value || "").trim());
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

function addMessageCard(type, role, content, artifacts = [], scroll = true, options = {}) {
  removeEmptyState();
  const fragment = messageTemplate.content.cloneNode(true);
  const root = fragment.querySelector(".message-card");
  root.classList.add(type);
  fragment.querySelector(".message-role").textContent = role;
  renderMessageContent(fragment.querySelector(".message-body"), content || "", options);
  const artifactGrid = fragment.querySelector(".artifact-grid");
  renderArtifacts(artifactGrid, artifacts);
  timeline.appendChild(fragment);
  if (type === "assistant") {
    previewArtifactSet(artifacts);
  }
  if (scroll) {
    scrollToBottom();
  }
  return root;
}

function appendAssistantDelta(payload) {
  const streamKey = assistantStreamKey(payload);
  const delta = String(payload.delta || payload.content || "");
  if (!delta) {
    return;
  }
  const deltaKind = String(payload.metadata?.assistant_delta_kind || "").trim();
  const isThinkingPlaceholder = deltaKind === ASSISTANT_DELTA_KIND_THINKING_PLACEHOLDER;
  if (!activeAssistantStream || activeAssistantStream.key !== streamKey) {
    activeAssistantStream = {
      key: streamKey,
      content: "",
      hasThinkingPlaceholder: false,
      isStructuredForm: false,
      card: addMessageCard("assistant", "CreativeClaw", "", [], true),
    };
  }
  if (isThinkingPlaceholder) {
    if (!activeAssistantStream.content || activeAssistantStream.hasThinkingPlaceholder) {
      activeAssistantStream.content = delta;
      activeAssistantStream.hasThinkingPlaceholder = true;
    }
  } else {
    if (activeAssistantStream.hasThinkingPlaceholder) {
      activeAssistantStream.content = "";
      activeAssistantStream.hasThinkingPlaceholder = false;
    }
    activeAssistantStream.content += delta;
  }
  activeAssistantStream.isStructuredForm =
    activeAssistantStream.isStructuredForm || isQuestionFormStreamContent(activeAssistantStream.content);
  const displayContent = activeAssistantStream.isStructuredForm
    ? pendingQuestionFormStreamText(activeAssistantStream.content)
    : activeAssistantStream.content;
  updateMessageCard(activeAssistantStream.card, displayContent, []);
  scrollToBottom();
}

function finalizeAssistantStream(payload) {
  if (!activeAssistantStream || activeAssistantStream.key !== assistantStreamKey(payload)) {
    return false;
  }
  const fallbackContent = activeAssistantStream.hasThinkingPlaceholder ? "" : activeAssistantStream.content;
  const content = String(payload.content || fallbackContent || "");
  const artifacts = payload.artifacts || [];
  updateMessageCard(activeAssistantStream.card, content, artifacts, {
    revealQuestionForms: isQuestionFormStreamContent(content),
  });
  previewArtifactSet(artifacts);
  appendHistory({
    type: "assistant",
    role: "CreativeClaw",
    content,
    artifacts,
  });
  activeAssistantStream = null;
  scrollToBottom();
  return true;
}

function updateMessageCard(card, content, artifacts = [], options = {}) {
  if (!card) {
    return;
  }
  renderMessageContent(card.querySelector(".message-body"), content || "", options);
  renderArtifacts(card.querySelector(".artifact-grid"), artifacts);
}

function assistantStreamKey(payload) {
  return String(payload?.runId || payload?.metadata?.session_id || "default");
}

function isQuestionFormStreamContent(content) {
  return String(content || "").toLowerCase().includes(QUESTION_FORM_STREAM_MARKER);
}

function pendingQuestionFormStreamText(content) {
  const step = Math.floor(String(content || "").length / 96) % 3;
  return `正在准备需求确认表单${".".repeat(step + 1)}`;
}

function renderMessageContent(container, content, options = {}) {
  container.innerHTML = "";
  const blocks = splitQuestionFormBlocks(content);
  for (const block of blocks) {
    if (block.type === "markdown") {
      const html = renderMarkdown(block.content);
      if (html) {
        const wrapper = document.createElement("div");
        wrapper.innerHTML = html;
        container.append(...Array.from(wrapper.childNodes));
      }
      continue;
    }
    const form = parseQuestionForm(block.content);
    if (!form) {
      const pre = document.createElement("pre");
      const code = document.createElement("code");
      code.textContent = block.raw;
      pre.appendChild(code);
      container.appendChild(pre);
      continue;
    }
    container.appendChild(
      renderQuestionForm(form, {
        reveal: Boolean(options.revealQuestionForms),
      })
    );
  }
}

function splitQuestionFormBlocks(content) {
  const text = String(content || "");
  const blocks = [];
  const openRe = /<cc-question-form(?:\s+[^>]*)?>/gi;
  let cursor = 0;
  let match = openRe.exec(text);
  while (match) {
    if (match.index > cursor) {
      blocks.push({ type: "markdown", content: text.slice(cursor, match.index) });
    }
    const closeIndex = text.toLowerCase().indexOf("</cc-question-form>", openRe.lastIndex);
    if (closeIndex < 0) {
      blocks.push({ type: "markdown", content: text.slice(match.index) });
      cursor = text.length;
      break;
    }
    const body = text.slice(openRe.lastIndex, closeIndex).trim();
    const raw = text.slice(match.index, closeIndex + "</cc-question-form>".length);
    blocks.push({ type: "question_form", content: body, raw });
    cursor = closeIndex + "</cc-question-form>".length;
    openRe.lastIndex = cursor;
    match = openRe.exec(text);
  }
  if (cursor < text.length) {
    blocks.push({ type: "markdown", content: text.slice(cursor) });
  }
  return blocks.length ? blocks : [{ type: "markdown", content: text }];
}

function parseQuestionForm(rawJson) {
  try {
    const form = JSON.parse(stripMarkdownFence(rawJson));
    if (!form || typeof form !== "object" || !Array.isArray(form.questions)) {
      return null;
    }
    return {
      id: String(form.id || "design-brief"),
      version: String(form.version || "design-brief-form-v1"),
      title: String(form.title || "确认需求"),
      description: String(form.description || ""),
      submitLabel: String(form.submitLabel || "确认并继续"),
      questions: form.questions
        .filter((question) => question && typeof question === "object")
        .map((question) => ({
          id: String(question.id || ""),
          label: String(question.label || ""),
          type: String(question.type || ""),
          presentation: String(question.presentation || ""),
          resource: String(question.resource || ""),
          required: Boolean(question.required),
          placeholder: String(question.placeholder || ""),
          maxSelections: Number.isFinite(Number(question.maxSelections)) ? Number(question.maxSelections) : null,
          allowOther: Boolean(question.allowOther),
          min: Number.isFinite(Number(question.min)) ? Number(question.min) : 0,
          max: Number.isFinite(Number(question.max)) ? Number(question.max) : 10,
          default: Number.isFinite(Number(question.default)) ? Number(question.default) : null,
          options: Array.isArray(question.options)
            ? question.options.map((option) => ({
                value: String(option?.value || ""),
                label: String(option?.label || ""),
                description: String(option?.description || ""),
                previewUrl: String(option?.previewUrl || ""),
                darkPreviewUrl: String(option?.darkPreviewUrl || ""),
                showcaseUrl: String(option?.showcaseUrl || ""),
                swatches: Array.isArray(option?.swatches) ? option.swatches.map((color) => String(color || "")) : [],
              }))
            : [],
        }))
        .filter((question) => question.id && question.label),
    };
  } catch {
    return null;
  }
}

function stripMarkdownFence(value) {
  const text = String(value || "").trim();
  if (!text.startsWith("```")) {
    return text;
  }
  const lines = text.split(/\r?\n/);
  if (lines.length >= 2 && lines[0].trim().startsWith("```") && lines[lines.length - 1].trim() === "```") {
    return lines.slice(1, -1).join("\n").trim();
  }
  return text;
}

function renderQuestionForm(form, options = {}) {
  const root = document.createElement("form");
  root.className = "cc-question-form";
  root.dataset.formId = form.id;
  root.dataset.formVersion = form.version;

  if (form.description) {
    const header = document.createElement("div");
    header.className = "cc-question-form-head";
    const description = document.createElement("div");
    description.className = "cc-question-form-description";
    description.textContent = form.description;
    header.appendChild(description);
    root.appendChild(header);
  }

  for (const question of form.questions) {
    root.appendChild(renderQuestionField(form, question));
  }

  const footer = document.createElement("div");
  footer.className = "cc-question-form-footer";
  const status = document.createElement("span");
  status.className = "cc-question-form-status";
  status.textContent = "提交后会继续生成设计产物。";
  const submit = document.createElement("button");
  submit.className = "cc-question-form-submit";
  submit.type = "submit";
  submit.textContent = form.submitLabel || "确认并继续";
  footer.appendChild(status);
  footer.appendChild(submit);
  root.appendChild(footer);

  root.addEventListener("submit", (event) => {
    event.preventDefault();
    const result = collectQuestionFormAnswers(root, form);
    status.textContent = result.error || "";
    root.classList.toggle("has-error", Boolean(result.error));
    if (result.error) {
      return;
    }
    submitQuestionForm(root, form, result.answers);
  });
  if (options.reveal) {
    revealQuestionForm(root);
  }
  return root;
}

function revealQuestionForm(root) {
  const items = Array.from(root.children).filter((item) =>
    item.matches(".cc-question-form-head, .cc-question-field, .cc-question-form-footer")
  );
  if (!items.length) {
    return;
  }
  root.classList.add("cc-question-form-revealing");
  items.forEach((item) => {
    item.classList.add("cc-question-form-reveal-item");
    item.hidden = true;
  });
  items.forEach((item, index) => {
    window.setTimeout(() => {
      if (!document.contains(item)) {
        return;
      }
      item.hidden = false;
      window.requestAnimationFrame(() => {
        item.classList.add("visible");
      });
    }, index * QUESTION_FORM_REVEAL_STEP_MS);
  });
  window.setTimeout(() => {
    if (!document.contains(root)) {
      return;
    }
    root.classList.remove("cc-question-form-revealing");
  }, items.length * QUESTION_FORM_REVEAL_STEP_MS + 180);
}

function renderQuestionField(form, question) {
  const field = document.createElement("fieldset");
  field.className = "cc-question-field";
  field.dataset.questionId = question.id;
  field.dataset.questionType = question.type;

  const legend = document.createElement("legend");
  legend.className = "cc-question-label";
  legend.textContent = question.required ? `${question.label} *` : question.label;
  field.appendChild(legend);

  if (isDesignSystemQuestion(question)) {
    renderDesignSystemPicker(field, form, question);
    return field;
  }

  if (question.type === "single_choice" || question.type === "multi_choice") {
    const options = document.createElement("div");
    options.className = "cc-question-options";
    for (const option of question.options || []) {
      if (!option.value) continue;
      const label = document.createElement("label");
      label.className = "cc-question-option";
      const input = document.createElement("input");
      input.type = question.type === "single_choice" ? "radio" : "checkbox";
      input.name = `${form.id}-${question.id}`;
      input.value = option.value;
      input.addEventListener("change", () => syncChoiceState(field, question, input));
      const optionBody = document.createElement("span");
      optionBody.className = "cc-question-option-body";
      const optionLabel = document.createElement("span");
      optionLabel.className = "cc-question-option-label";
      optionLabel.textContent = option.label || option.value;
      optionBody.appendChild(optionLabel);
      if (option.description) {
        const optionDescription = document.createElement("span");
        optionDescription.className = "cc-question-option-description";
        optionDescription.textContent = option.description;
        optionBody.appendChild(optionDescription);
      }
      label.appendChild(input);
      label.appendChild(optionBody);
      options.appendChild(label);
    }
    if (question.allowOther) {
      const other = document.createElement("input");
      other.className = "cc-question-other";
      other.type = "text";
      other.name = `${form.id}-${question.id}-other`;
      other.placeholder = question.placeholder || "Other...";
      other.addEventListener("input", () => syncChoiceState(field, question, other));
      options.appendChild(other);
    }
    field.appendChild(options);
    return field;
  }

  if (question.type === "range") {
    const min = Number.isFinite(question.min) ? question.min : 0;
    const max = Number.isFinite(question.max) ? question.max : 10;
    const fallback = Math.round((min + max) / 2);
    const value = question.default === null ? fallback : Math.min(Math.max(question.default, min), max);
    const rangeWrap = document.createElement("div");
    rangeWrap.className = "cc-question-range";

    const minLabel = document.createElement("span");
    minLabel.className = "cc-question-range-bound";
    minLabel.textContent = String(min);
    const input = document.createElement("input");
    input.className = "cc-question-range-input";
    input.type = "range";
    input.id = `${form.id}-${question.id}`;
    input.name = question.id;
    input.min = String(min);
    input.max = String(max);
    input.value = String(value);
    const maxLabel = document.createElement("span");
    maxLabel.className = "cc-question-range-bound";
    maxLabel.textContent = String(max);
    const current = document.createElement("output");
    current.className = "cc-question-range-value";
    current.htmlFor = input.id;
    current.textContent = String(value);
    input.addEventListener("input", () => {
      current.textContent = input.value;
    });
    rangeWrap.appendChild(minLabel);
    rangeWrap.appendChild(input);
    rangeWrap.appendChild(maxLabel);
    rangeWrap.appendChild(current);
    field.appendChild(rangeWrap);
    return field;
  }

  if (question.type === "long_text") {
    const textarea = document.createElement("textarea");
    textarea.className = "cc-question-textarea";
    textarea.name = question.id;
    textarea.placeholder = question.placeholder || "";
    textarea.rows = 3;
    field.appendChild(textarea);
    return field;
  }

  const input = document.createElement("input");
  input.className = "cc-question-input";
  input.type = "text";
  input.name = question.id;
  input.placeholder = question.placeholder || "";
  field.appendChild(input);
  return field;
}

function isDesignSystemQuestion(question) {
  return question.presentation === "design_system_picker" || question.resource === "design_systems";
}

function renderDesignSystemPicker(field, form, question) {
  field.classList.add("cc-design-system-field");
  const recommendations = getDesignSystemRecommendations(question);

  const picker = document.createElement("div");
  picker.className = "cc-design-system-picker";

  const hidden = document.createElement("input");
  hidden.type = "hidden";
  hidden.name = `${form.id}-${question.id}`;
  hidden.dataset.designSystemValue = "true";
  picker.appendChild(hidden);

  const controlRow = document.createElement("div");
  controlRow.className = "cc-design-system-controls";

  const search = document.createElement("input");
  search.className = "cc-design-system-search";
  search.type = "search";
  search.placeholder = "搜索 Claude、Stripe、Apple...";
  controlRow.appendChild(search);
  picker.appendChild(controlRow);

  const other = document.createElement("input");
  other.className = "cc-question-other cc-design-system-other";
  other.type = "text";
  other.name = `${form.id}-${question.id}-other`;
  other.placeholder = question.placeholder || "Other...";
  other.addEventListener("input", () => {
    if (other.value.trim()) selectDesignSystem(picker, hidden, "other");
  });
  picker.appendChild(other);

  const status = document.createElement("div");
  status.className = "cc-design-system-status";
  status.textContent = "正在加载设计系统...";
  picker.appendChild(status);

  const grid = document.createElement("div");
  grid.className = "cc-design-system-grid";
  picker.appendChild(grid);

  const decide = document.createElement("button");
  decide.type = "button";
  decide.className = "cc-design-system-quick";
  decide.textContent = "为我决定";
  decide.addEventListener("click", () => selectDesignSystem(picker, hidden, "decide_for_me"));
  picker.appendChild(decide);

  const render = (systems) => {
    const visibleSystems = getVisibleDesignSystems(systems, recommendations);
    const query = search.value.trim().toLowerCase();
    const filtered = visibleSystems.filter((system) => {
      if (!query) return true;
      return [system.id, system.title, system.summary, system.recommendationReason]
        .map((value) => String(value || "").toLowerCase())
        .some((value) => value.includes(query));
    });
    status.textContent = filtered.length ? "" : "没有匹配的设计系统。";
    grid.replaceChildren(...filtered.map((system) => renderDesignSystemCard(picker, hidden, system)));
  };

  loadDesignSystems()
    .then((systems) => render(systems))
    .catch(() => {
      status.textContent = "设计系统加载失败，可以使用为我决定或 Other。";
    });
  search.addEventListener("input", () => {
    if (designSystemsCache) render(designSystemsCache);
  });

  field.appendChild(picker);
}

function getDesignSystemRecommendations(question) {
  return (question.options || [])
    .filter((option) => option.value && option.value !== "decide_for_me" && option.value !== "other")
    .slice(0, 6)
    .map((option) => ({
      id: option.value,
      label: option.label || option.value,
      reason: option.description || "",
    }));
}

function getVisibleDesignSystems(systems, recommendations) {
  if (!recommendations.length) {
    return systems.slice(0, 18);
  }
  const systemById = new Map(systems.map((system) => [system.id, system]));
  return recommendations
    .map((recommendation) => {
      const system = systemById.get(recommendation.id);
      if (!system) return null;
      return {
        ...system,
        title: system.title || recommendation.label,
        recommendationReason: recommendation.reason,
      };
    })
    .filter(Boolean);
}

function renderDesignSystemCard(picker, hidden, system) {
  const card = document.createElement("button");
  card.type = "button";
  card.className = "cc-design-system-card";
  card.dataset.designSystemId = system.id;
  card.addEventListener("click", () => selectDesignSystem(picker, hidden, system.id));

  const swatches = document.createElement("span");
  swatches.className = "cc-design-system-swatches";
  for (const color of system.swatches || []) {
    const swatch = document.createElement("span");
    swatch.style.background = color;
    swatches.appendChild(swatch);
  }
  card.appendChild(swatches);

  const copy = document.createElement("span");
  copy.className = "cc-design-system-copy";
  const title = document.createElement("span");
  title.className = "cc-design-system-title";
  title.textContent = system.title || system.id;
  const summary = document.createElement("span");
  summary.className = "cc-design-system-summary";
  summary.textContent = system.recommendationReason || system.summary || system.id;
  copy.appendChild(title);
  copy.appendChild(summary);
  card.appendChild(copy);

  const preview = document.createElement("span");
  preview.className = "cc-design-system-preview-link";
  preview.textContent = "预览";
  preview.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    openDesignSystemPreview(system);
  });
  card.appendChild(preview);

  return card;
}

function selectDesignSystem(picker, hidden, value) {
  hidden.value = value;
  for (const item of picker.querySelectorAll(".cc-design-system-card, .cc-design-system-quick")) {
    item.classList.toggle("selected", item.dataset.designSystemId === value || (value === "decide_for_me" && item.classList.contains("cc-design-system-quick")));
  }
  if (value !== "other") {
    const other = picker.querySelector(".cc-design-system-other");
    if (other) other.value = "";
  }
}

async function loadDesignSystems() {
  if (designSystemsCache) return designSystemsCache;
  if (!designSystemsPromise) {
    designSystemsPromise = fetch("/api/design-systems")
      .then((response) => {
        if (!response.ok) throw new Error("design systems unavailable");
        return response.json();
      })
      .then((payload) => {
        designSystemsCache = Array.isArray(payload.designSystems) ? payload.designSystems : [];
        return designSystemsCache;
      });
  }
  return designSystemsPromise;
}

function openDesignSystemPreview(system) {
  const existing = document.querySelector(".cc-design-system-modal");
  if (existing) existing.remove();

  const modal = document.createElement("div");
  modal.className = "cc-design-system-modal";
  modal.addEventListener("click", (event) => {
    if (event.target === modal) modal.remove();
  });

  const panel = document.createElement("div");
  panel.className = "cc-design-system-modal-panel";
  const head = document.createElement("div");
  head.className = "cc-design-system-modal-head";
  const title = document.createElement("div");
  title.className = "cc-design-system-modal-title";
  title.textContent = system.title || system.id;
  const close = document.createElement("button");
  close.type = "button";
  close.className = "cc-design-system-modal-close";
  close.textContent = "关闭";
  close.addEventListener("click", () => modal.remove());
  head.appendChild(title);
  head.appendChild(close);

  const frame = document.createElement("iframe");
  frame.className = "cc-design-system-modal-frame";
  frame.title = `${system.title || system.id} preview`;
  frame.src = system.showcaseUrl || system.previewUrl;
  frame.setAttribute("sandbox", "allow-scripts allow-same-origin");

  panel.appendChild(head);
  panel.appendChild(frame);
  modal.appendChild(panel);
  document.body.appendChild(modal);
}

function collectQuestionFormAnswers(root, form) {
  const answers = {};
  for (const question of form.questions) {
    if (isDesignSystemQuestion(question)) {
      const selected = root.querySelector(`input[data-design-system-value][name="${cssEscape(`${form.id}-${question.id}`)}"]`);
      const selectedValue = String(selected?.value || "").trim();
      const other = root.querySelector(`input[name="${cssEscape(`${form.id}-${question.id}-other`)}"]`);
      const otherValue = String(other?.value || "").trim();
      if (selectedValue) answers[question.id] = selectedValue;
      if (otherValue) {
        answers[question.id] = "other";
        answers[`${question.id}_other`] = otherValue;
      }
    } else if (question.type === "single_choice") {
      const selected = root.querySelector(`input[name="${cssEscape(`${form.id}-${question.id}`)}"]:checked`);
      if (selected) answers[question.id] = selected.value;
      const other = root.querySelector(`input[name="${cssEscape(`${form.id}-${question.id}-other`)}"]`);
      const otherValue = String(other?.value || "").trim();
      if (otherValue) {
        answers[question.id] = "other";
        answers[`${question.id}_other`] = otherValue;
      }
    } else if (question.type === "multi_choice") {
      const selected = Array.from(root.querySelectorAll(`input[name="${cssEscape(`${form.id}-${question.id}`)}"]:checked`)).map(
        (item) => item.value
      );
      const other = root.querySelector(`input[name="${cssEscape(`${form.id}-${question.id}-other`)}"]`);
      const otherValue = String(other?.value || "").trim();
      const selectionCount = selected.length + (otherValue ? 1 : 0);
      if (question.maxSelections && selectionCount > question.maxSelections) {
        return { error: `${question.label} 最多选择 ${question.maxSelections} 项。`, answers: {} };
      }
      if (selected.length > 0) answers[question.id] = selected;
      if (otherValue) {
        answers[question.id] = [...selected, "other"];
        answers[`${question.id}_other`] = otherValue;
      }
    } else if (question.type === "range") {
      const input = root.querySelector(`[name="${cssEscape(question.id)}"]`);
      if (input) answers[question.id] = Number(input.value);
    } else {
      const input = root.querySelector(`[name="${cssEscape(question.id)}"]`);
      const value = String(input?.value || "").trim();
      if (value) answers[question.id] = value;
    }
    const value = answers[question.id];
    const empty = Array.isArray(value) ? value.length === 0 : !String(value || "").trim();
    if (question.required && empty) {
      return { error: `请填写：${question.label}`, answers: {} };
    }
  }
  return { error: "", answers };
}

function syncChoiceState(field, question, source = null) {
  if (!field || question.type !== "multi_choice") {
    return;
  }
  const choiceName = field.querySelector("input[type='checkbox']")?.name;
  if (!choiceName) {
    return;
  }
  const inputs = Array.from(field.querySelectorAll(`input[name="${cssEscape(choiceName)}"]`));
  const decideInput = inputs.find((input) => input.value === "decide_for_me");
  if (!decideInput) {
    return;
  }
  if (source === decideInput && decideInput.checked) {
    for (const input of inputs) {
      if (input !== decideInput) input.checked = false;
    }
    const other = field.querySelector(".cc-question-other");
    if (other) other.value = "";
    return;
  }
  if (source !== decideInput) {
    decideInput.checked = false;
  }
}

function cssEscape(value) {
  if (window.CSS && typeof window.CSS.escape === "function") {
    return window.CSS.escape(value);
  }
  return String(value).replace(/["\\]/g, "\\$&");
}

function submitQuestionForm(root, form, answers) {
  if (runState !== "idle") {
    const status = root.querySelector(".cc-question-form-status");
    if (status) status.textContent = "当前任务还在运行，稍后再提交。";
    return;
  }
  const content = `[cc-form-answers id="${form.id}" version="${form.version}"]\n${JSON.stringify(answers, null, 2)}\n[/cc-form-answers]`;
  const displayContent = "已提交需求确认表单";
  const runId = crypto.randomUUID();
  sendSocket({
    type: "chat",
    content,
    runId,
    attachments: [],
  });
  setRunState("running", runId);
  addMessageCard("user", "You", displayContent);
  appendHistory({ type: "user", role: "You", content: displayContent, artifacts: [] });
  root.classList.add("submitted");
  for (const element of root.querySelectorAll("input, textarea, button")) {
    element.disabled = true;
  }
  const status = root.querySelector(".cc-question-form-status");
  if (status) status.textContent = "已提交，正在继续处理。";
  activeProgressCard = null;
  activeAssistantStream = null;
}

function upsertProgressCard(content, metadata, options = {}) {
  removeEmptyState();
  const groupKey = progressGroupKey(metadata, options.runId || currentRunId || "");
  let card = progressCardsByGroup.get(groupKey);
  if (!card || !card.isConnected) {
    const fragment = progressTemplate.content.cloneNode(true);
    timeline.appendChild(fragment);
    card = timeline.lastElementChild;
    initializeProgressCard(card);
    progressCardsByGroup.set(groupKey, card);
  }
  activeProgressCard = card;
  if (options.runId) {
    card.dataset.runId = String(options.runId);
  }
  card.dataset.activityGroupId = groupKey;
  card.classList.remove("completed", "cancelled", "failed");
  card.dataset.status = "running";
  const userTitle = String(metadata.user_title || metadata.stage_title || "").trim();
  const userDetail = String(metadata.user_detail || "").trim();
  const rawTitle = userTitle;
  const titleEl = card.querySelector(".progress-title");
  if (HIDDEN_PROGRESS_TITLES.has(rawTitle)) {
    titleEl.hidden = true;
    titleEl.textContent = "";
  } else {
    titleEl.hidden = false;
    titleEl.textContent = rawTitle || "Working";
  }
  const progressDetail = userDetail || summarizeProgressContent(content, rawTitle);
  card.querySelector(".progress-summary").textContent = progressDetail;
  recordProgressStep(card, {
    title: rawTitle && !HIDDEN_PROGRESS_TITLES.has(rawTitle) ? rawTitle : "Activity",
    detail: progressDetail,
    stage: String(metadata.stage || ""),
    sequence: Number(metadata.activity_sequence || 0) || null,
  });
  renderProgressCardBody(card);
  const activityStatus = String(metadata.activity_status || "running").trim();
  if (activityStatus === "completed" || activityStatus === "cancelled" || activityStatus === "failed") {
    completeProgressCard(card, activityStatus);
  }
  scrollToBottom();
}

function progressGroupKey(metadata, runId = "") {
  const explicit = String(metadata?.activity_group_id || "").trim();
  if (explicit) {
    return explicit;
  }
  const session = String(metadata?.session_id || "").trim();
  const turn = String(metadata?.turn_index || "").trim();
  if (session && turn) {
    return `${session}:turn:${turn}`;
  }
  const normalizedRunId = String(runId || "").trim();
  if (normalizedRunId) {
    return `run:${normalizedRunId}`;
  }
  return session || "active";
}

function recordProgressStep(card, step) {
  const steps = progressSteps(card);
  const normalizedStep = {
    title: String(step.title || "Activity").trim() || "Activity",
    detail: String(step.detail || "").trim(),
    stage: String(step.stage || "").trim(),
    sequence: Number(step.sequence || 0) || null,
  };
  if (!normalizedStep.detail) {
    return;
  }
  const last = steps[steps.length - 1];
  if (
    last &&
    last.title === normalizedStep.title &&
    last.detail === normalizedStep.detail &&
    last.stage === normalizedStep.stage
  ) {
    return;
  }
  steps.push(normalizedStep);
  if (steps.length > PROGRESS_STEP_LIMIT) {
    steps.splice(0, steps.length - PROGRESS_STEP_LIMIT);
  }
  card._progressSteps = steps;
}

function progressSteps(card) {
  if (!Array.isArray(card._progressSteps)) {
    card._progressSteps = [];
  }
  return card._progressSteps;
}

function renderProgressCardBody(card, statusText = "") {
  const bodyEl = card.querySelector(".progress-body");
  if (!bodyEl) {
    return;
  }
  const steps = progressSteps(card);
  const body = progressDetailsMarkdown(steps, statusText);
  bodyEl.innerHTML = renderMarkdown(body);
}

function progressDetailsMarkdown(steps, statusText = "") {
  const visibleSteps = Array.isArray(steps) ? steps.filter((step) => step.detail) : [];
  const normalizedStatus = String(statusText || "").trim();
  if (visibleSteps.length === 0) {
    return normalizedStatus || "";
  }
  if (visibleSteps.length === 1 && !normalizedStatus) {
    return visibleSteps[0].detail;
  }
  const lines = visibleSteps.map((step, index) => `${index + 1}. **${step.title}** ${step.detail}`);
  if (normalizedStatus) {
    lines.push("", normalizedStatus);
  }
  return lines.join("\n");
}

function completeProgressCards({ runId = "", status = "completed" } = {}) {
  const cards = Array.from(timeline.querySelectorAll(".progress-card:not(.completed):not(.cancelled):not(.failed)"));
  const normalizedRunId = String(runId || "").trim();
  for (const card of cards) {
    const cardRunId = String(card.dataset.runId || "").trim();
    if (normalizedRunId && cardRunId && cardRunId !== normalizedRunId) {
      continue;
    }
    completeProgressCard(card, status);
  }
  if (activeProgressCard && activeProgressCard.dataset.status !== "running") {
    activeProgressCard = null;
  }
}

function completeProgressCard(card, status = "completed") {
  const normalizedStatus = status === "cancelled" || status === "failed" ? status : "completed";
  card.classList.remove("completed", "cancelled", "failed");
  card.classList.add(normalizedStatus);
  card.dataset.status = normalizedStatus;

  const titleEl = card.querySelector(".progress-title");
  const summaryEl = card.querySelector(".progress-summary");
  const bodyEl = card.querySelector(".progress-body");
  const currentTitle = String(titleEl?.textContent || "").trim();
  const statusText = {
    completed: "Completed.",
    cancelled: "Cancelled.",
    failed: "Failed.",
  }[normalizedStatus];

  if (titleEl && (titleEl.hidden || !currentTitle)) {
    titleEl.hidden = false;
    titleEl.textContent = statusText.replace(/\.$/, "");
    if (summaryEl) {
      summaryEl.textContent = "";
    }
  } else if (summaryEl) {
    summaryEl.textContent = statusText;
  }

  if (bodyEl) {
    renderProgressCardBody(card, statusText);
  }
  setProgressExpanded(card, false);
}

function initializeProgressCard(card) {
  const toggle = card.querySelector(".progress-toggle");
  const body = card.querySelector(".progress-body");
  const bodyId = `progress-body-${++progressBodyCounter}`;
  body.id = bodyId;
  toggle.setAttribute("aria-controls", bodyId);
  toggle.addEventListener("click", () => {
    setProgressExpanded(card, toggle.getAttribute("aria-expanded") !== "true", { keepVisible: true });
  });
  setProgressExpanded(card, false);
}

function setProgressExpanded(card, expanded, options = {}) {
  const toggle = card.querySelector(".progress-toggle");
  const body = card.querySelector(".progress-body");
  card.classList.toggle("expanded", expanded);
  card.classList.toggle("collapsed", !expanded);
  card.dataset.expanded = expanded ? "true" : "false";
  toggle.setAttribute("aria-expanded", expanded ? "true" : "false");
  body.hidden = !expanded;
  if (options.keepVisible) {
    keepProgressCardVisible(card);
  }
}

function keepProgressCardVisible(card) {
  window.requestAnimationFrame(() => {
    if (!card.isConnected) {
      return;
    }

    const timelineRect = timeline.getBoundingClientRect();
    const cardRect = card.getBoundingClientRect();
    const margin = 12;
    const visibleTop = timelineRect.top + margin;
    const visibleBottom = timelineRect.bottom - margin;
    const visibleHeight = Math.max(0, visibleBottom - visibleTop);

    if (cardRect.height > visibleHeight && cardRect.top !== visibleTop) {
      timeline.scrollTop += cardRect.top - visibleTop;
      return;
    }
    if (cardRect.bottom > visibleBottom) {
      timeline.scrollTop += cardRect.bottom - visibleBottom;
      return;
    }
    if (cardRect.top < visibleTop) {
      timeline.scrollTop -= visibleTop - cardRect.top;
    }
  });
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
    const hasModel = is3DArtifact(artifact);
    const anchor = document.createElement("a");
    anchor.className = `artifact-card${previewTab ? " previewable" : ""}${hasMedia ? " has-media" : " file-artifact"}${hasModel ? " model-artifact" : ""}`;
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
    } else if (hasModel) {
      const badge = document.createElement("div");
      badge.className = "artifact-file-icon";
      badge.textContent = model3DArtifactPreviewable(artifact) ? "3D" : artifactExtension(artifact).replace(".", "").toUpperCase();
      anchor.appendChild(badge);
    }

    const name = document.createElement("div");
    name.className = "artifact-name";
    name.textContent = artifact.name || "artifact";
    anchor.appendChild(name);

    const meta = document.createElement("div");
    meta.className = "artifact-meta";
    meta.textContent = artifactMetaText(artifact);
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
  unmountModel3dViewer();
  resetMediaCanvasState();
  if (!options.keepActiveTab) {
    activePreviewTab = "tldraw";
  }
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
  mergedGroups.model3d = groups.model3d.length > 0 ? groups.model3d : previewArtifactsByTab.model3d;

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
  const nextTab = AUTO_PREVIEW_PRIORITY.find((tabName) => groups[tabName].length > 0);
  if (nextTab) {
    activatePreviewTab(nextTab);
  } else {
    renderPreviewView(activePreviewTab);
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
  groups.model3d = sortModel3DPreviewArtifacts(groups.model3d);
  return groups;
}

function selectPreviewArtifact(artifact) {
  const tabName = previewTabForArtifact(artifact);
  if (!tabName) {
    return;
  }
  addUniqueArtifact(previewArtifactsByTab[tabName], artifact);
  selectedPreviewByTab[tabName] = artifact;
  if (tabName === "tldraw") {
    tldrawShouldFitSelectedArtifact = true;
  }
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
  renderPreviewView(tabName);
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
    return;
  }
  if (tabName === "model3d") {
    renderModel3dPreview();
  }
}

function renderTldrawPreview() {
  const artifacts = previewArtifactsByTab.tldraw;
  const signature = tldrawPreviewArtifactsSignature(artifacts);

  if (window.CreativeClawTldraw?.mount) {
    if (tldrawCanvasUnmount && tldrawCanvasSignature === signature) {
      selectMountedTldrawArtifact(selectedPreviewByTab.tldraw, { fit: tldrawShouldFitSelectedArtifact });
      tldrawShouldFitSelectedArtifact = false;
      return;
    }

    unmountTldrawCanvas();
    tldrawPreview.innerHTML = "";
    const shell = document.createElement("div");
    shell.className = "tldraw-sketch-shell";
    const host = document.createElement("div");
    host.className = "tldraw-sketch-host";
    shell.appendChild(host);
    tldrawPreview.appendChild(shell);
    tldrawCanvasSignature = signature;
    const fitSelectedArtifact = tldrawShouldFitSelectedArtifact;
    tldrawShouldFitSelectedArtifact = false;
    tldrawCanvasUnmount = window.CreativeClawTldraw.mount(host, {
      artifacts,
      selectedArtifact: selectedPreviewByTab.tldraw,
      fitSelectedArtifact,
      sessionId,
      onSubmitSketch: handleTldrawSketchSubmit,
    });
    return;
  }

  unmountTldrawCanvas();
  tldrawPreview.innerHTML = "";
  if (!artifacts.length) {
    tldrawPreview.appendChild(previewEmpty("No visual board preview"));
    return;
  }

  renderMediaCanvasPreview(artifacts);
}

function unmountTldrawCanvas() {
  if (typeof tldrawCanvasUnmount === "function") {
    tldrawCanvasUnmount();
  }
  tldrawCanvasUnmount = null;
  tldrawCanvasSignature = "";
  tldrawShouldFitSelectedArtifact = false;
}

function tldrawPreviewArtifactsSignature(artifacts) {
  return artifacts.map((artifact) => artifactKey(artifact)).join("|");
}

function selectMountedTldrawArtifact(artifact, options = {}) {
  window.dispatchEvent(new CustomEvent("creative-claw-select-artifact", {
    detail: { artifact, fit: Boolean(options.fit) },
  }));
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
    pptPreview.appendChild(previewEmpty("No PPT/PDF preview"));
    return;
  }

  pptPreview.appendChild(buildPreviewToolbar(artifact, [], { showMeta: false }));

  if (isInteractiveHtmlDeckArtifact(artifact)) {
    const iframe = document.createElement("iframe");
    iframe.className = "ppt-preview-frame ppt-html-deck-frame";
    iframe.src = artifact.url;
    iframe.title = artifact.name || "HTML deck preview";
    iframe.tabIndex = 0;
    iframe.setAttribute("sandbox", "allow-scripts allow-same-origin allow-forms allow-popups allow-downloads");
    iframe.setAttribute("allow", "fullscreen");
    iframe.setAttribute("allowfullscreen", "");
    pptPreview.appendChild(iframe);
    return;
  }

  if (isPdfArtifact(artifact) || isPptxArtifact(artifact)) {
    const iframe = document.createElement("iframe");
    iframe.className = "ppt-preview-frame";
    iframe.src = previewUrlForArtifact(artifact);
    iframe.title = artifact.name || "PPT/PDF preview";
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

function renderModel3dPreview() {
  unmountModel3dViewer();
  model3dPreview.innerHTML = "";
  const artifact = selectedPreviewByTab.model3d;
  if (!artifact) {
    model3dPreview.appendChild(previewEmpty("No 3D preview"));
    return;
  }

  const canPreviewInline = model3DArtifactPreviewable(artifact);
  const artifactPreviewKey = artifactKey(artifact);
  const requiresManualPreview =
    canPreviewInline &&
    model3DRequiresManualPreview(artifact) &&
    !largeModelPreviewApprovals.has(artifactPreviewKey);
  const resetButton = document.createElement("button");
  resetButton.className = "preview-action";
  resetButton.type = "button";
  resetButton.textContent = "Reset";
  resetButton.addEventListener("click", () => {
    model3dViewerController?.resetCamera?.();
  });
  model3dPreview.appendChild(buildPreviewToolbar(artifact, canPreviewInline && !requiresManualPreview ? [resetButton] : []));

  if (!canPreviewInline) {
    model3dPreview.appendChild(buildUnsupportedModelCard(artifact));
    return;
  }

  if (requiresManualPreview) {
    model3dPreview.appendChild(buildLargeModelPreviewCard(artifact));
    return;
  }

  const host = document.createElement("div");
  host.className = "model3d-viewer-host";
  model3dPreview.appendChild(host);

  if (!window.CreativeClaw3D?.mount) {
    host.appendChild(previewEmpty("3D viewer unavailable"));
    return;
  }

  model3dViewerController = window.CreativeClaw3D.mount(host, {
    src: artifact.url,
    packageManifestUrl: modelPackageManifestUrlForArtifact(artifact),
    name: artifact.name || "3D model",
    sizeBytes: artifact.sizeBytes || 0,
  });
}

function unmountModel3dViewer() {
  if (model3dViewerController?.unmount) {
    model3dViewerController.unmount();
  }
  model3dViewerController = null;
}

function buildUnsupportedModelCard(artifact) {
  const card = document.createElement("div");
  card.className = "document-preview-card model3d-unsupported-card";

  const icon = document.createElement("div");
  icon.className = "document-preview-icon model3d-document-icon";
  icon.textContent = artifactExtension(artifact).replace(".", "").toUpperCase() || "3D";

  const copy = document.createElement("div");
  copy.className = "document-preview-copy";
  const title = document.createElement("div");
  title.className = "document-preview-title";
  title.textContent = "Inline 3D preview is not available for this format.";
  const meta = document.createElement("div");
  meta.className = "document-preview-meta";
  meta.textContent = artifactMetaText(artifact) || artifact.name || "";
  copy.appendChild(title);
  copy.appendChild(meta);

  card.appendChild(icon);
  card.appendChild(copy);
  return card;
}

function buildLargeModelPreviewCard(artifact) {
  const card = document.createElement("div");
  card.className = "document-preview-card model3d-unsupported-card";

  const icon = document.createElement("div");
  icon.className = "document-preview-icon model3d-document-icon";
  icon.textContent = "3D";

  const copy = document.createElement("div");
  copy.className = "document-preview-copy";
  const title = document.createElement("div");
  title.className = "document-preview-title";
  title.textContent = "This model is large, so preview is paused.";
  const meta = document.createElement("div");
  meta.className = "document-preview-meta";
  meta.textContent = `${formatFileSize(artifact.sizeBytes)} · Previewing large local models can make the browser slow.`;
  const action = document.createElement("button");
  action.className = "preview-action";
  action.type = "button";
  action.textContent = "Preview anyway";
  action.addEventListener("click", () => {
    largeModelPreviewApprovals.add(artifactKey(artifact));
    renderModel3dPreview();
  });
  copy.appendChild(title);
  copy.appendChild(meta);
  copy.appendChild(action);

  card.appendChild(icon);
  card.appendChild(copy);
  return card;
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
    path.textContent = artifactMetaText(artifact);
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
  if (isInteractiveHtmlDeckArtifact(artifact)) {
    return "ppt";
  }
  if (isHtmlArtifact(artifact)) {
    return "html";
  }
  if (is3DArtifact(artifact)) {
    return "model3d";
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

function isInteractiveHtmlDeckArtifact(artifact) {
  if (!isHtmlArtifact(artifact)) {
    return false;
  }
  const kind = String(artifact?.artifactKind || artifact?.kind || "").trim();
  if (kind === INTERACTIVE_PPT_HTML_KIND) {
    return true;
  }
  const pathText = String(artifact?.path || artifact?.url || "").toLowerCase();
  return pathText.includes("ppt_private_skill_step_");
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

function is3DArtifact(artifact) {
  const extension = artifactExtension(artifact);
  const mimeType = artifactMimeType(artifact);
  return Boolean(artifact?.is3D) || MODEL_3D_EXTENSIONS.has(extension) || mimeType.startsWith("model/") || isLikely3DZipArtifact(artifact);
}

function model3DArtifactPreviewable(artifact) {
  return inline3DArtifactSupported(artifact) || isLikely3DZipArtifact(artifact);
}

function model3DRequiresManualPreview(artifact) {
  const sizeBytes = Number(artifact?.sizeBytes || 0);
  return Number.isFinite(sizeBytes) && sizeBytes > MODEL3D_AUTO_PREVIEW_LIMIT_BYTES;
}

function inline3DArtifactSupported(artifact) {
  const extension = artifactExtension(artifact);
  const mimeType = artifactMimeType(artifact);
  return (
    INLINE_3D_EXTENSIONS.has(extension) ||
    mimeType === "model/gltf-binary" ||
    mimeType === "model/gltf+json" ||
    mimeType === "model/obj" ||
    mimeType === "model/stl" ||
    mimeType === "model/vnd.usd" ||
    mimeType === "model/vnd.usdz+zip"
  );
}

function modelPackageManifestUrlForArtifact(artifact) {
  if (!isLikely3DZipArtifact(artifact)) {
    return "";
  }
  const path = String(artifact?.path || "").trim();
  if (path) {
    return `/workspace-3d-package/manifest/${encodeWorkspacePath(path)}`;
  }
  const url = String(artifact?.url || "");
  if (url.startsWith("/workspace/")) {
    return url.replace("/workspace/", "/workspace-3d-package/manifest/");
  }
  return "";
}

function sortModel3DPreviewArtifacts(artifacts) {
  return [...artifacts].sort((left, right) => {
    const leftKey = model3DPreviewSortKey(left);
    const rightKey = model3DPreviewSortKey(right);
    return leftKey.priority - rightKey.priority || leftKey.name.localeCompare(rightKey.name);
  });
}

function model3DPreviewSortKey(artifact) {
  const extension = artifactExtension(artifact);
  const priority = MODEL3D_PREVIEW_EXTENSION_PRIORITY.get(extension) ?? 99;
  return {
    priority,
    name: artifactSourceText(artifact).toLowerCase(),
  };
}

function isLikely3DZipArtifact(artifact) {
  if (artifactExtension(artifact) !== ".zip") {
    return false;
  }
  return /(^|[._/-])(3d|hy3d|seed3d|hyper3d|hitem3d|model|mesh)([._/-]|$)/i.test(artifactSourceText(artifact));
}

function artifactMimeType(artifact) {
  return String(artifact?.mimeType || "").toLowerCase();
}

function artifactExtension(artifact) {
  const source = artifactSourceText(artifact).split("?")[0].split("#")[0];
  const dotIndex = source.lastIndexOf(".");
  return dotIndex >= 0 ? source.slice(dotIndex).toLowerCase() : "";
}

function artifactSourceText(artifact) {
  return String(artifact?.name || artifact?.path || artifact?.url || "");
}

function artifactMetaText(artifact) {
  const source = artifact?.path || artifact?.mimeType || "";
  const size = formatFileSize(artifact?.sizeBytes);
  return [source, size].filter(Boolean).join(" · ");
}

function formatFileSize(value) {
  const bytes = Number(value || 0);
  if (!Number.isFinite(bytes) || bytes <= 0) {
    return "";
  }
  const units = ["B", "KB", "MB", "GB"];
  let size = bytes;
  let unitIndex = 0;
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }
  const precision = unitIndex === 0 || size >= 10 ? 0 : 1;
  return `${size.toFixed(precision)} ${units[unitIndex]}`;
}

function encodeWorkspacePath(path) {
  return String(path || "")
    .split("/")
    .map((segment) => encodeURIComponent(segment))
    .join("/");
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
    ".fbx": "application/octet-stream",
    ".glb": "model/gltf-binary",
    ".gltf": "model/gltf+json",
    ".obj": "model/obj",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".stl": "model/stl",
    ".svg": "image/svg+xml",
    ".usd": "model/vnd.usd",
    ".usda": "model/vnd.usd",
    ".usdc": "model/vnd.usd",
    ".usdz": "model/vnd.usdz+zip",
    ".webm": "video/webm",
    ".webp": "image/webp",
    ".zip": "application/zip",
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
      const safeUrl = normalizeMarkdownResourceUrl(rawUrl, { allowMailto: false });
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
      const safeUrl = normalizeMarkdownResourceUrl(rawUrl, { allowMailto: true });
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
    throw new Error("Wait for the current run to finish before attaching a sketch.");
  }
  if (!socket || socket.readyState !== WebSocket.OPEN) {
    throw new Error("Web chat is not connected.");
  }

  if (payload?.artifact) {
    const attached = attachExistingWorkspaceArtifact(payload.artifact, {
      description: "Referenced tldraw image artifact.",
    });
    promptInput.focus();
    updateComposerButtons();
    return attached;
  }

  const imageBlob = payload?.imageBlob;
  if (!(imageBlob instanceof Blob)) {
    throw new Error("Sketch export did not include a PNG image.");
  }

  const imageFile = new File([imageBlob], payload.imageName || buildSketchUploadName("png"), { type: "image/png" });
  const uploaded = await uploadGeneratedFile(imageFile, {
    description: "Selected tldraw canvas export.",
  });

  promptInput.focus();
  updateComposerButtons();
  return uploaded;
}

function attachExistingWorkspaceArtifact(artifact, options = {}) {
  const path = workspacePathFromReferenceArtifact(artifact);
  if (!path) {
    throw new Error("Selected tldraw image does not reference a workspace file.");
  }

  const existing = attachedFiles.find((attachment) => attachment.status === "uploaded" && attachment.path === path);
  if (existing) {
    renderAttachments();
    return existing;
  }

  const extension = artifactExtension({ path, url: artifact?.url || "" });
  const attachment = {
    id: crypto.randomUUID(),
    name: String(artifact?.name || path.split("/").filter(Boolean).pop() || "attachment"),
    size: Number(artifact?.size || 0),
    mimeType: String(artifact?.mimeType || mimeTypeForExtension(extension) || ""),
    description: String(options.description || artifact?.description || "").trim(),
    status: "uploaded",
    progress: 100,
    path,
  };
  attachedFiles.push(attachment);
  renderAttachments();
  return attachment;
}

function workspacePathFromReferenceArtifact(artifact) {
  const rawPath = String(artifact?.path || "").trim();
  if (rawPath.startsWith("/workspace/")) {
    return workspacePathFromUrl(rawPath);
  }
  if (rawPath.startsWith("workspace/")) {
    return rawPath.slice("workspace/".length);
  }
  if (rawPath && !rawPath.startsWith("/") && !rawPath.includes("://")) {
    return rawPath;
  }

  const rawUrl = String(artifact?.url || "").trim();
  if (!rawUrl) {
    return "";
  }
  const normalizedUrl = normalizeWorkspaceUrl(rawUrl);
  if (normalizedUrl.startsWith("/workspace/")) {
    return workspacePathFromUrl(normalizedUrl);
  }
  if (normalizedUrl.startsWith("workspace/")) {
    return normalizedUrl.slice("workspace/".length);
  }
  return "";
}

async function uploadGeneratedFile(file, options = {}) {
  const attachment = {
    id: crypto.randomUUID(),
    name: file.name || "sketch-export",
    size: file.size,
    mimeType: file.type || "",
    description: String(options.description || "").trim(),
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
  const fileLines = attachments.map((attachment) => `- ${attachment.name || attachment.path}`);
  return `${prompt}\n\nAttached files:\n${fileLines.join("\n")}`;
}

function serializeAttachmentsForRuntime(attachments) {
  return attachments.map((attachment) => ({
    name: attachment.name || "attachment",
    path: attachment.path,
    mimeType: attachment.mimeType || "",
    size: attachment.size || 0,
    description: attachment.description || "",
  }));
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

  const content = promptInput.value.trim() || (uploadedAttachments.length ? "Please use the attached file(s)." : "");
  if (!content || !socket || socket.readyState !== WebSocket.OPEN) {
    return;
  }
  const displayContent = contentWithAttachments(content, uploadedAttachments);
  const runId = crypto.randomUUID();
  sendSocket({
    type: "chat",
    content,
    runId,
    attachments: serializeAttachmentsForRuntime(uploadedAttachments),
  });
  setRunState("running", runId);
  addMessageCard("user", "You", displayContent);
  appendHistory({ type: "user", role: "You", content: displayContent, artifacts: [] });
  promptInput.value = "";
  resizePromptInput();
  clearAttachments();
  activeProgressCard = null;
  activeAssistantStream = null;
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

function resizePromptInput() {
  const style = window.getComputedStyle(promptInput);
  const lineHeight = Number.parseFloat(style.lineHeight) || 21;
  const minHeight = Math.ceil(lineHeight * 2);
  const maxHeight = Math.ceil(lineHeight * 10);
  promptInput.style.height = `${minHeight}px`;
  const nextHeight = Math.min(promptInput.scrollHeight, maxHeight);
  promptInput.style.height = `${nextHeight}px`;
  const isScrollable = promptInput.scrollHeight > maxHeight;
  promptInput.style.overflowY = isScrollable ? "auto" : "hidden";
  promptInput.classList.toggle("is-scrollable", isScrollable);
}

promptInput.addEventListener("input", () => {
  resizePromptInput();
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

document.addEventListener("copy", preserveChatTextSelectionClipboard, true);
document.addEventListener("cut", preserveChatTextSelectionClipboard, true);

for (const tab of previewTabs) {
  tab.addEventListener("click", () => {
    activatePreviewTab(tab.dataset.previewTab);
  });
}

window.addEventListener("beforeunload", () => {
  disconnect();
});

function preserveChatTextSelectionClipboard(event) {
  if (event.defaultPrevented || !selectionIntersectsChatPanel()) {
    return;
  }

  event.stopImmediatePropagation();
}

function selectionIntersectsChatPanel() {
  const selection = window.getSelection?.();
  if (!selection || selection.isCollapsed || selection.rangeCount === 0) {
    return false;
  }
  return selectionIntersectsElement(selection, document.querySelector(".chat-panel"));
}

function selectionIntersectsElement(selection, element) {
  if (!element) {
    return false;
  }

  for (let index = 0; index < selection.rangeCount; index += 1) {
    const range = selection.getRangeAt(index);
    if (range.collapsed) {
      continue;
    }
    if (typeof range.intersectsNode === "function") {
      try {
        if (range.intersectsNode(element)) {
          return true;
        }
      } catch {
        // Some browser selection ranges can throw for detached nodes.
      }
    }
    if (nodeInsideElement(range.commonAncestorContainer, element)) {
      return true;
    }
    if (nodeInsideElement(range.startContainer, element) || nodeInsideElement(range.endContainer, element)) {
      return true;
    }
  }
  return false;
}

function nodeInsideElement(node, element) {
  if (!node || !element) {
    return false;
  }
  const candidate = node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement;
  return Boolean(candidate && element.contains(candidate));
}
