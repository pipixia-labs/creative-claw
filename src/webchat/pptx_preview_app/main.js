import { init } from "pptx-preview";

const MIN_PREVIEW_WIDTH = 320;
const MAX_PREVIEW_WIDTH = 1180;
const HORIZONTAL_PADDING = 48;

function previewWidth(root) {
  const availableWidth = root.clientWidth || window.innerWidth - HORIZONTAL_PADDING;
  return Math.max(MIN_PREVIEW_WIDTH, Math.min(MAX_PREVIEW_WIDTH, Math.floor(availableWidth)));
}

function showMessage(root, message) {
  root.innerHTML = "";
  const element = document.createElement("div");
  element.className = "pptx-preview-message";
  element.textContent = message;
  root.appendChild(element);
}

async function renderPptxPreview() {
  const root = document.getElementById("pptx-preview-root");
  if (!root) {
    return;
  }

  const pptxUrl = root.dataset.pptxUrl || "";
  if (!pptxUrl) {
    showMessage(root, "PPTX preview source is missing.");
    return;
  }

  try {
    showMessage(root, "Loading PPTX preview...");
    const response = await fetch(pptxUrl, { cache: "no-cache" });
    if (!response.ok) {
      throw new Error(`PPTX fetch failed with HTTP ${response.status}`);
    }
    const fileBuffer = await response.arrayBuffer();
    root.innerHTML = "";
    const previewer = init(root, {
      width: previewWidth(root),
      mode: "list",
    });
    await previewer.preview(fileBuffer);
    document.body.classList.add("pptx-preview-loaded");
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    showMessage(root, `PPTX browser preview failed: ${detail}`);
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", renderPptxPreview, { once: true });
} else {
  renderPptxPreview();
}
