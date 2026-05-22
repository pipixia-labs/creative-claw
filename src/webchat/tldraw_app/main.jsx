import React, { memo, useCallback, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  AssetRecordType,
  DefaultColorStyle,
  HTMLContainer,
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
  useEditorComponents,
  useImageOrVideoAsset,
  usePrefersReducedMotion,
  useRelevantStyles,
  useToasts,
  useValue,
  VideoShapeUtil,
} from "tldraw";
import "tldraw/tldraw.css";
import "./styles.css";

const MAX_MEDIA_WIDTH = 920;
const MEDIA_GAP = 96;
const VIDEO_METADATA_TIMEOUT_MS = 4000;

function CreativeClawSketchCanvas({ artifacts = [], onSubmitSketch }) {
  const loadedSignatureRef = useRef("");

  const mediaArtifacts = useMemo(
    () => artifacts.filter((artifact) => Boolean(artifact?.url)),
    [artifacts]
  );

  const handleMount = useCallback(
    (mountedEditor) => {
      void seedMediaArtifacts(mountedEditor, mediaArtifacts, loadedSignatureRef);
    },
    [mediaArtifacts]
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
      <Tldraw onMount={handleMount} components={tldrawComponents} shapeUtils={CREATIVE_CLAW_SHAPE_UTILS} />
    </div>
  );
}

class CreativeClawVideoShapeUtil extends VideoShapeUtil {
  component(shape) {
    return <CreativeClawVideoShape shape={shape} />;
  }
}

const CREATIVE_CLAW_SHAPE_UTILS = [CreativeClawVideoShapeUtil];

const CreativeClawVideoShape = memo(function CreativeClawVideoShape({ shape }) {
  const prefersReducedMotion = usePrefersReducedMotion();
  const { Spinner } = useEditorComponents();
  const { asset, url } = useImageOrVideoAsset({
    shapeId: shape.id,
    assetId: shape.props.assetId,
    width: shape.props.w,
  });
  const [isLoaded, setIsLoaded] = useState(false);

  return (
    <HTMLContainer
      id={shape.id}
      style={{
        color: "var(--tl-color-text-3)",
        backgroundColor: asset ? "transparent" : "var(--tl-color-low)",
        border: asset ? "none" : "1px solid var(--tl-color-low-border)",
      }}
    >
      <div className="tl-counter-scaled">
        <div className="tl-video-container">
          {!asset ? (
            <div className="cc-video-placeholder">Video unavailable</div>
          ) : Spinner && !asset.props.src ? (
            <Spinner />
          ) : url ? (
            <>
              <video
                key={url}
                className="tl-video cc-video"
                width="100%"
                height="100%"
                draggable={false}
                playsInline
                autoPlay={shape.props.autoplay && !prefersReducedMotion}
                loop
                disableRemotePlayback
                disablePictureInPicture
                controls
                style={isLoaded ? { pointerEvents: "all" } : { display: "none" }}
                onLoadedData={() => setIsLoaded(true)}
                aria-label={shape.props.altText}
              >
                <source src={url} />
              </video>
              {!isLoaded && Spinner ? <Spinner /> : null}
            </>
          ) : null}
        </div>
      </div>
    </HTMLContainer>
  );
});

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
  if (!shape || !["image", "video"].includes(shape.type)) {
    return null;
  }

  const path = String(shape.meta?.creativeClawArtifactPath || "").trim();
  if (!path) {
    return null;
  }

  const asset = shape.props?.assetId && typeof editor.getAsset === "function" ? editor.getAsset(shape.props.assetId) : null;
  const fallbackMimeType = shape.type === "video" ? "video/mp4" : "image/png";
  return {
    path,
    url: String(shape.meta?.creativeClawArtifactUrl || shape.props?.url || "").trim(),
    name: artifactNameFromPath(path, asset?.props?.name || shape.props?.altText || ""),
    mimeType: String(asset?.props?.mimeType || fallbackMimeType),
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

async function seedMediaArtifacts(editor, artifacts, loadedSignatureRef) {
  const signature = artifacts.map((artifact) => `${artifact.url}|${artifact.name || ""}|${artifact.mimeType || ""}`).join("\n");
  if (!signature || loadedSignatureRef.current === signature) {
    return;
  }
  loadedSignatureRef.current = signature;

  try {
    let cursorX = 0;
    const shapeIds = [];
    for (const artifact of artifacts) {
      const isVideo = isVideoArtifact(artifact);
      const mediaType = isVideo ? "video" : "image";
      const dimensions = await loadMediaDimensions(artifact, mediaType);
      const scale = Math.min(1, MAX_MEDIA_WIDTH / dimensions.width);
      const width = Math.round(dimensions.width * scale);
      const height = Math.round(dimensions.height * scale);
      const assetId = AssetRecordType.createId();
      const shapeId = createShapeId();
      const name = artifact.name || (isVideo ? "generated-video" : "generated-image");
      const mimeType = artifact.mimeType || (isVideo ? "video/mp4" : "image/png");
      const shapeProps = isVideo ? {
        assetId,
        altText: name,
        autoplay: false,
        h: height,
        playing: false,
        time: 0,
        url: artifact.url,
        w: width,
      } : {
        assetId,
        altText: name,
        crop: null,
        flipX: false,
        flipY: false,
        w: width,
        h: height,
        playing: true,
        url: artifact.url,
      };

      editor.createAssets([
        {
          id: assetId,
          typeName: "asset",
          type: mediaType,
          props: {
            name,
            src: artifact.url,
            w: dimensions.width,
            h: dimensions.height,
            mimeType,
            isAnimated: isVideo || String(mimeType).toLowerCase() === "image/gif",
          },
          meta: {
            creativeClawArtifactPath: artifact.path || "",
            creativeClawArtifactUrl: artifact.url,
          },
        },
      ]);
      editor.createShape({
        id: shapeId,
        type: mediaType,
        x: cursorX,
        y: 0,
        props: shapeProps,
        meta: {
          creativeClawArtifactPath: artifact.path || "",
          creativeClawArtifactUrl: artifact.url,
        },
      });
      shapeIds.push(shapeId);
      cursorX += width + MEDIA_GAP;
    }

    if (shapeIds.length > 0) {
      editor.select(...shapeIds);
      editor.zoomToSelection({ animation: { duration: 220 } });
      editor.selectNone();
    }
  } catch (error) {
    loadedSignatureRef.current = "";
    console.warn(error instanceof Error ? error.message : "Could not load media into tldraw.");
  }
}

function isVideoArtifact(artifact) {
  return String(artifact?.mimeType || "").toLowerCase().startsWith("video/");
}

async function loadMediaDimensions(artifact, mediaType) {
  if (mediaType !== "video") {
    return loadImageDimensions(artifact.url);
  }

  try {
    return await loadVideoDimensions(artifact.url);
  } catch (error) {
    console.warn(error instanceof Error ? error.message : "Could not load video metadata into tldraw.");
    return { width: 1280, height: 720 };
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

function loadVideoDimensions(src) {
  return new Promise((resolve, reject) => {
    const video = document.createElement("video");
    let timeoutId;
    let isSettled = false;
    const settle = (callback) => {
      if (isSettled) {
        return;
      }
      isSettled = true;
      window.clearTimeout(timeoutId);
      try {
        callback();
      } finally {
        video.removeAttribute("src");
        video.load();
      }
    };
    timeoutId = window.setTimeout(() => {
      settle(() => reject(new Error("Timed out loading video metadata into the sketch canvas.")));
    }, VIDEO_METADATA_TIMEOUT_MS);
    video.addEventListener(
      "loadedmetadata",
      () => {
        const width = video.videoWidth || 1280;
        const height = video.videoHeight || 720;
        settle(() => {
          resolve({
            width,
            height,
          });
        });
      },
      { once: true }
    );
    video.addEventListener(
      "error",
      () => {
        settle(() => reject(new Error("Could not load one video artifact into the sketch canvas.")));
      },
      { once: true }
    );
    video.muted = true;
    video.playsInline = true;
    video.preload = "metadata";
    video.src = src;
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
