import React, { useCallback, useMemo, useRef } from "react";
import { createRoot } from "react-dom/client";
import {
  AssetRecordType,
  DefaultColorStyle,
  DefaultContextMenu,
  DefaultContextMenuContent,
  DefaultStylePanel,
  DefaultToolbar,
  DefaultToolbarContent,
  Tldraw,
  TldrawUiButtonIcon,
  TldrawUiMenuGroup,
  TldrawUiMenuItem,
  TldrawUiPopover,
  TldrawUiPopoverContent,
  TldrawUiPopoverTrigger,
  TldrawUiToolbarButton,
  createShapeId,
  getColorValue,
  useEditor,
  useRelevantStyles,
  useToasts,
  useValue,
} from "tldraw";
import "tldraw/tldraw.css";
import "./styles.css";

const MAX_IMAGE_WIDTH = 920;
const IMAGE_GAP = 96;

function CreativeClawSketchCanvas({ artifacts = [], onSubmitSketch }) {
  const loadedSignatureRef = useRef("");

  const imageArtifacts = useMemo(
    () => artifacts.filter((artifact) => Boolean(artifact?.url) && !String(artifact?.mimeType || "").startsWith("video/")),
    [artifacts]
  );

  const handleMount = useCallback(
    (mountedEditor) => {
      void seedImageArtifacts(mountedEditor, imageArtifacts, loadedSignatureRef);
    },
    [imageArtifacts]
  );

  const tldrawComponents = useMemo(
    () => ({
      ContextMenu: (props) => <CreativeClawContextMenu {...props} onSubmitSketch={onSubmitSketch} />,
      HelperButtons: null,
      SharePanel: null,
      StylePanel: null,
      Toolbar: CreativeClawToolbar,
    }),
    [onSubmitSketch]
  );

  return (
    <div className="cc-sketch-root">
      <Tldraw onMount={handleMount} components={tldrawComponents} />
    </div>
  );
}

function CreativeClawContextMenu({ onSubmitSketch, ...props }) {
  const editor = useEditor();
  const { addToast } = useToasts();
  const hasSelection = useValue(
    "creativeClawHasSelection",
    () => editor.getSelectedShapeIds().length > 0,
    [editor]
  );

  const handleSelect = useCallback(async () => {
    if (!hasSelection || !onSubmitSketch) {
      return;
    }
    try {
      const referencedArtifact = selectedArtifactReference(editor);
      if (referencedArtifact) {
        await onSubmitSketch({
          artifact: referencedArtifact,
        });
        return;
      }

      const imageBlob = await exportSelectedShapesAsPng(editor);
      await onSubmitSketch({
        imageBlob,
        imageName: buildSketchFileName("selection.png"),
      });
    } catch (error) {
      addToast({
        id: "creative-claw-selection-attach-failed",
        title: "发送失败",
        description: error instanceof Error ? error.message : "Could not export the selected sketch items as PNG.",
        severity: "error",
      });
    }
  }, [addToast, editor, hasSelection, onSubmitSketch]);

  return (
    <DefaultContextMenu {...props}>
      {hasSelection ? (
        <TldrawUiMenuGroup id="creative-claw">
          <TldrawUiMenuItem
            id="creative-claw-attach-selection"
            label="发送到对话"
            readonlyOk
            onSelect={handleSelect}
          />
        </TldrawUiMenuGroup>
      ) : null}
      <DefaultContextMenuContent />
    </DefaultContextMenu>
  );
}

function selectedArtifactReference(editor) {
  const selectedIds = editor.getSelectedShapeIds();
  if (selectedIds.length !== 1) {
    return null;
  }

  const shape = editor.getShape(selectedIds[0]);
  if (!shape || shape.type !== "image") {
    return null;
  }

  const path = String(shape.meta?.creativeClawArtifactPath || "").trim();
  if (!path) {
    return null;
  }

  const asset = shape.props?.assetId && typeof editor.getAsset === "function" ? editor.getAsset(shape.props.assetId) : null;
  return {
    path,
    url: String(shape.meta?.creativeClawArtifactUrl || shape.props?.url || "").trim(),
    name: artifactNameFromPath(path, asset?.props?.name || shape.props?.altText || ""),
    mimeType: String(asset?.props?.mimeType || "image/png"),
  };
}

function artifactNameFromPath(path, fallbackName) {
  const fallback = String(fallbackName || "").trim();
  if (fallback) {
    return fallback;
  }
  return String(path || "")
    .split("/")
    .filter(Boolean)
    .pop() || "artifact";
}

function CreativeClawToolbar() {
  return (
    <DefaultToolbar orientation="vertical">
      <CreativeClawStyleToolbarButton />
      <DefaultToolbarContent />
    </DefaultToolbar>
  );
}

function CreativeClawStyleToolbarButton() {
  const editor = useEditor();
  const relevantStyles = useRelevantStyles();
  const color = relevantStyles?.get(DefaultColorStyle);
  const currentColor = useValue(
    "creativeClawStyleButtonColor",
    () => {
      const colors = editor.getCurrentTheme().colors[editor.getColorMode()];
      return color?.type === "shared" ? getColorValue(colors, color.value, "solid") : getColorValue(colors, "black", "solid");
    },
    [color, editor]
  );
  const disabled = useValue(
    "creativeClawStyleButtonDisabled",
    () => editor.isInAny("hand", "zoom", "eraser", "laser"),
    [editor]
  );

  const handleOpenChange = useCallback(
    (isOpen) => {
      if (!isOpen) {
        editor.updateInstanceState({ isChangingStyle: false });
      }
    },
    [editor]
  );

  return (
    <TldrawUiPopover id="creative-claw-style-panel" className="cc-sketch-style-popover" onOpenChange={handleOpenChange}>
      <TldrawUiPopoverTrigger>
        <TldrawUiToolbarButton
          type="tool"
          tooltip="样式"
          disabled={disabled}
          style={{
            color: disabled ? "var(--tl-color-muted-1)" : currentColor,
          }}
        >
          <TldrawUiButtonIcon icon={color?.type === "mixed" ? "mixed" : "blob"} />
        </TldrawUiToolbarButton>
      </TldrawUiPopoverTrigger>
      <TldrawUiPopoverContent side="right" align="start" sideOffset={10}>
        <DefaultStylePanel isMobile />
      </TldrawUiPopoverContent>
    </TldrawUiPopover>
  );
}

async function seedImageArtifacts(editor, artifacts, loadedSignatureRef) {
  const signature = artifacts.map((artifact) => `${artifact.url}|${artifact.name || ""}`).join("\n");
  if (!signature || loadedSignatureRef.current === signature) {
    return;
  }
  loadedSignatureRef.current = signature;

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
  } catch (error) {
    loadedSignatureRef.current = "";
    console.warn(error instanceof Error ? error.message : "Could not load images into tldraw.");
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

async function exportSelectedShapesAsPng(editor) {
  const selectedIds = editor.getSelectedShapeIds();
  if (selectedIds.length === 0) {
    throw new Error("Select one or more sketch items first.");
  }

  const exported = await editor.toImage(selectedIds, {
    background: true,
    format: "png",
    padding: 48,
    pixelRatio: 1,
  });
  const imageBlob = exported instanceof Blob ? exported : exported?.blob;
  if (!(imageBlob instanceof Blob)) {
    throw new Error("Could not export the selected sketch items as PNG.");
  }
  return imageBlob;
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
