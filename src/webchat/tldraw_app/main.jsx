import React, { useCallback, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  AssetRecordType,
  DefaultToolbar,
  DefaultToolbarContent,
  Tldraw,
  createShapeId,
  getSnapshot,
} from "tldraw";
import "tldraw/tldraw.css";
import "./styles.css";

const MAX_IMAGE_WIDTH = 920;
const IMAGE_GAP = 96;

function CreativeClawSketchCanvas({ artifacts = [], onSubmitSketch }) {
  const [editor, setEditor] = useState(null);
  const [status, setStatus] = useState("Draw or annotate, then send the sketch.");
  const [note, setNote] = useState("");
  const loadedSignatureRef = useRef("");

  const imageArtifacts = useMemo(
    () => artifacts.filter((artifact) => Boolean(artifact?.url) && !String(artifact?.mimeType || "").startsWith("video/")),
    [artifacts]
  );

  const handleMount = useCallback(
    (mountedEditor) => {
      setEditor(mountedEditor);
      void seedImageArtifacts(mountedEditor, imageArtifacts, loadedSignatureRef, setStatus);
    },
    [imageArtifacts]
  );

  const handleExportPng = useCallback(async () => {
    if (!editor) {
      return;
    }
    const imageBlob = await exportCurrentPageAsPng(editor);
    downloadBlob(imageBlob, buildSketchFileName("png"));
  }, [editor]);

  const handleSubmit = useCallback(async () => {
    if (!editor || !onSubmitSketch) {
      return;
    }
    setStatus("Exporting sketch...");
    try {
      const imageBlob = await exportCurrentPageAsPng(editor);
      const snapshot = getSnapshot(editor.store);
      await onSubmitSketch({
        imageBlob,
        snapshot,
        note,
        imageName: buildSketchFileName("png"),
        snapshotName: buildSketchFileName("tldr.json"),
      });
      setStatus("Sketch sent to the design agent.");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Could not export the sketch.");
    }
  }, [editor, note, onSubmitSketch]);

  return (
    <div className="cc-sketch-root">
      <Tldraw
        onMount={handleMount}
        components={{
          HelperButtons: null,
          SharePanel: null,
          Toolbar: CreativeClawToolbar,
        }}
      />
      <div className="cc-sketch-panel" aria-label="Sketch actions">
        <textarea
          className="cc-sketch-note"
          value={note}
          placeholder="Optional instruction for the design agent"
          rows={2}
          onChange={(event) => setNote(event.target.value)}
        />
        <div className="cc-sketch-actions">
          <button type="button" className="cc-sketch-button" onClick={handleExportPng}>
            Export PNG
          </button>
          <button type="button" className="cc-sketch-button cc-sketch-button-primary" onClick={handleSubmit}>
            Send sketch
          </button>
        </div>
        <div className="cc-sketch-status" aria-live="polite">
          {status}
        </div>
      </div>
    </div>
  );
}

function CreativeClawToolbar() {
  return (
    <DefaultToolbar orientation="vertical">
      <DefaultToolbarContent />
    </DefaultToolbar>
  );
}

async function seedImageArtifacts(editor, artifacts, loadedSignatureRef, setStatus) {
  const signature = artifacts.map((artifact) => `${artifact.url}|${artifact.name || ""}`).join("\n");
  if (!signature || loadedSignatureRef.current === signature) {
    return;
  }
  loadedSignatureRef.current = signature;
  setStatus("Loading generated images into the sketch canvas...");

  try {
    let cursorX = 0;
    const shapeIds = [];
    for (const artifact of artifacts) {
      const dimensions = await loadImageDimensions(artifact.url);
      const scale = Math.min(1, MAX_IMAGE_WIDTH / dimensions.width);
      const width = Math.round(dimensions.width * scale);
      const height = Math.round(dimensions.height * scale);
      const assetId = AssetRecordType.createId();
      const shapeId = createShapeId();

      editor.createAssets([
        {
          id: assetId,
          typeName: "asset",
          type: "image",
          props: {
            name: artifact.name || "generated-image",
            src: artifact.url,
            w: dimensions.width,
            h: dimensions.height,
            mimeType: artifact.mimeType || "image/png",
            isAnimated: String(artifact.mimeType || "").toLowerCase() === "image/gif",
          },
          meta: {
            creativeClawArtifactPath: artifact.path || "",
            creativeClawArtifactUrl: artifact.url,
          },
        },
      ]);
      editor.createShape({
        id: shapeId,
        type: "image",
        x: cursorX,
        y: 0,
        props: {
          assetId,
          altText: artifact.name || "generated image",
          crop: null,
          flipX: false,
          flipY: false,
          w: width,
          h: height,
          playing: true,
          url: artifact.url,
        },
        meta: {
          creativeClawArtifactPath: artifact.path || "",
          creativeClawArtifactUrl: artifact.url,
        },
      });
      shapeIds.push(shapeId);
      cursorX += width + IMAGE_GAP;
    }

    if (shapeIds.length > 0) {
      editor.select(...shapeIds);
      editor.zoomToSelection({ animation: { duration: 220 } });
      editor.selectNone();
    }
    setStatus("Draw or annotate, then send the sketch.");
  } catch (error) {
    loadedSignatureRef.current = "";
    setStatus(error instanceof Error ? error.message : "Could not load images into tldraw.");
  }
}

function loadImageDimensions(src) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.addEventListener(
      "load",
      () => {
        resolve({
          width: image.naturalWidth || 1024,
          height: image.naturalHeight || 768,
        });
      },
      { once: true }
    );
    image.addEventListener(
      "error",
      () => {
        reject(new Error("Could not load one image artifact into the sketch canvas."));
      },
      { once: true }
    );
    image.decoding = "async";
    image.src = src;
  });
}

async function exportCurrentPageAsPng(editor) {
  const shapes = editor.getCurrentPageShapesSorted();
  if (shapes.length === 0) {
    throw new Error("Sketch canvas is empty.");
  }

  const exported = await editor.toImage(shapes, {
    background: true,
    format: "png",
    padding: 48,
    pixelRatio: 2,
  });
  const imageBlob = exported instanceof Blob ? exported : exported?.blob;
  if (!(imageBlob instanceof Blob)) {
    throw new Error("Could not export the sketch as PNG.");
  }
  return imageBlob;
}

function downloadBlob(blob, name) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = name;
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function buildSketchFileName(extension) {
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  return `sketch-${stamp}.${extension}`;
}

function mount(element, options = {}) {
  const root = createRoot(element);
  root.render(<CreativeClawSketchCanvas {...options} />);
  return () => root.unmount();
}

window.CreativeClawTldraw = {
  mount,
};
