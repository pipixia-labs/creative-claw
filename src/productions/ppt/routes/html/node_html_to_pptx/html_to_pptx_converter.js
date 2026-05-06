"use strict";

const fs = require("fs");
const path = require("path");
const { fileURLToPath, pathToFileURL } = require("url");
const pptxgen = require("pptxgenjs");
const { chromium } = require("playwright");

const PX_PER_IN = 96;

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, "utf8"));
}

function writeJson(filePath, payload) {
  fs.writeFileSync(filePath, JSON.stringify(payload, null, 2), "utf8");
}

function safeNumber(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function buildTextOptions(el) {
  const style = el.style || {};
  const options = {
    x: el.position.x,
    y: el.position.y,
    w: Math.max(0.01, el.position.w),
    h: Math.max(0.01, el.position.h),
    margin: 0,
    fit: "shrink",
    valign: "top",
    fontFace: style.fontFace || "Aptos",
    fontSize: Math.max(6, style.fontSize || 14),
    color: style.color || "172033",
    bold: Boolean(style.bold),
    italic: Boolean(style.italic),
    underline: Boolean(style.underline),
    align: style.align || "left",
  };
  if (style.transparency !== undefined) {
    options.transparency = style.transparency;
  }
  if (el.bullet) {
    options.bullet = { type: "ul" };
    options.margin = [0, 0, 0, 10];
    options.hanging = 2;
  }
  return options;
}

function imageOptionsFromSource(src) {
  const cleanSrc = String(src || "");
  if (cleanSrc.startsWith("data:")) {
    return { data: cleanSrc };
  }
  if (cleanSrc.startsWith("file://")) {
    return { path: fileURLToPath(cleanSrc) };
  }
  return { path: cleanSrc };
}

function addElementToSlide(pptx, slide, el) {
  if (el.type === "background") {
    slide.background = { color: el.color || "FFFFFF" };
    return;
  }
  if (el.type === "shape") {
    const shapeType = el.radius && el.radius > 0 ? pptx.ShapeType.roundRect : pptx.ShapeType.rect;
    const options = {
      x: el.position.x,
      y: el.position.y,
      w: Math.max(0.01, el.position.w),
      h: Math.max(0.01, el.position.h),
      fill: el.fill ? { color: el.fill, transparency: el.fillTransparency } : { color: "FFFFFF", transparency: 100 },
      line: el.line || { color: el.fill || "FFFFFF", transparency: 100 },
    };
    slide.addShape(shapeType, options);
    return;
  }
  if (el.type === "line") {
    slide.addShape(pptx.ShapeType.line, {
      x: el.x1,
      y: el.y1,
      w: el.x2 - el.x1,
      h: el.y2 - el.y1,
      line: { color: el.color || "D9DEE8", width: el.width || 1 },
    });
    return;
  }
  if (el.type === "image") {
    const imageOptions = {
      ...imageOptionsFromSource(el.src),
      x: el.position.x,
      y: el.position.y,
      w: Math.max(0.01, el.position.w),
      h: Math.max(0.01, el.position.h),
    };
    slide.addImage(imageOptions);
    return;
  }
  if (el.type === "text") {
    slide.addText(el.text || "", buildTextOptions(el));
  }
}

async function extractSlide(page, manifest, pageSpec, slideWidthIn, slideHeightIn) {
  const viewportWidth = safeNumber(manifest.viewportWidth, 1280);
  const viewportHeight = safeNumber(manifest.viewportHeight, 720);
  return await page.evaluate(
    ({ viewportWidth, viewportHeight, slideWidthIn, slideHeightIn }) => {
      const PX_PER_IN = 96;
      const PT_PER_PX = 0.75;

      const normalizeHexColor = (value, fallback = null) => {
        if (!value || value === "transparent" || value === "rgba(0, 0, 0, 0)") return fallback;
        const rgb = String(value).match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/i);
        if (rgb) {
          return rgb
            .slice(1, 4)
            .map((part) => Number.parseInt(part, 10).toString(16).padStart(2, "0"))
            .join("")
            .toUpperCase();
        }
        const hex = String(value).trim().match(/^#([0-9a-f]{3}|[0-9a-f]{6})$/i);
        if (!hex) return fallback;
        if (hex[1].length === 3) {
          return hex[1]
            .split("")
            .map((ch) => ch + ch)
            .join("")
            .toUpperCase();
        }
        return hex[1].toUpperCase();
      };

      const alphaTransparency = (value) => {
        const match = String(value || "").match(/rgba\(\d+,\s*\d+,\s*\d+,\s*([\d.]+)\)/i);
        if (!match) return undefined;
        const alpha = Number.parseFloat(match[1]);
        if (!Number.isFinite(alpha)) return undefined;
        return Math.max(0, Math.min(100, Math.round((1 - alpha) * 100)));
      };

      const pxToPt = (value) => {
        const px = Number.parseFloat(value || "0");
        if (!Number.isFinite(px)) return 0;
        return px * PT_PER_PX;
      };

      const toSlidePosition = (rect, bodyRect) => ({
        x: ((rect.left - bodyRect.left) / viewportWidth) * slideWidthIn,
        y: ((rect.top - bodyRect.top) / viewportHeight) * slideHeightIn,
        w: (rect.width / viewportWidth) * slideWidthIn,
        h: (rect.height / viewportHeight) * slideHeightIn,
      });

      const isVisibleBox = (rect) => rect.width > 0.5 && rect.height > 0.5;
      const mapTextAlign = (value) => {
        const align = String(value || "").toLowerCase();
        return ["center", "right", "justify"].includes(align) ? align : "left";
      };
      const unsupportedCssErrors = (computed) => {
        const errors = [];
        const bg = computed.backgroundImage || "";
        if (bg.includes("gradient")) errors.push("Gradient CSS is not supported by the editable PPTX converter.");
        if ((computed.filter || "none") !== "none" || (computed.backdropFilter || "none") !== "none") {
          errors.push("CSS filter/backdrop-filter is not supported by the editable PPTX converter.");
        }
        return errors;
      };

      const body = document.body;
      const bodyRect = body.getBoundingClientRect();
      const bodyStyle = window.getComputedStyle(body);
      const elements = [
        {
          type: "background",
          order: 0,
          zIndex: -100000,
          color: normalizeHexColor(bodyStyle.backgroundColor, "FFFFFF"),
        },
      ];
      const warnings = [];
      const errors = [];

      const widthOverflow = Math.max(0, body.scrollWidth - viewportWidth - 1);
      const heightOverflow = Math.max(0, body.scrollHeight - viewportHeight - 1);
      if (widthOverflow > 0 || heightOverflow > 0) {
        errors.push(
          `HTML content overflows viewport by ${widthOverflow.toFixed(0)}px horizontally and ${heightOverflow.toFixed(0)}px vertically.`
        );
      }

      const textTags = new Set(["H1", "H2", "H3", "H4", "H5", "H6", "P", "LI", "LABEL"]);
      const shapeTags = new Set(["DIV", "SECTION", "ARTICLE", "ASIDE", "HEADER", "FOOTER", "MAIN"]);
      const unsupportedTags = new Set(["SCRIPT", "STYLE", "CANVAS", "VIDEO", "AUDIO", "IFRAME", "OBJECT", "EMBED"]);

      Array.from(document.querySelectorAll("*")).forEach((node, index) => {
        if (unsupportedTags.has(node.tagName)) {
          if (node.tagName !== "STYLE") {
            errors.push(`Unsupported tag <${node.tagName.toLowerCase()}> cannot be converted to editable PPTX.`);
          }
          return;
        }
        const computed = window.getComputedStyle(node);
        errors.push(...unsupportedCssErrors(computed));
        const rect = node.getBoundingClientRect();
        if (!isVisibleBox(rect)) return;
        const position = toSlidePosition(rect, bodyRect);
        const zIndex = Number.parseInt(computed.zIndex, 10);
        const base = {
          order: index + 1,
          zIndex: Number.isFinite(zIndex) ? zIndex : 0,
          position,
        };

        if (node.tagName === "IMG") {
          elements.push({
            ...base,
            type: "image",
            src: node.currentSrc || node.src || "",
          });
          return;
        }

        if (shapeTags.has(node.tagName)) {
          const bg = normalizeHexColor(computed.backgroundColor, null);
          const bgTransparency = alphaTransparency(computed.backgroundColor);
          const borderWidths = [
            Number.parseFloat(computed.borderTopWidth) || 0,
            Number.parseFloat(computed.borderRightWidth) || 0,
            Number.parseFloat(computed.borderBottomWidth) || 0,
            Number.parseFloat(computed.borderLeftWidth) || 0,
          ];
          const hasBorder = borderWidths.some((width) => width > 0);
          const uniformBorder = hasBorder && borderWidths.every((width) => Math.abs(width - borderWidths[0]) < 0.1);
          const isThinLine = (rect.height <= 4 || rect.width <= 4) && (bg || hasBorder);

          if (isThinLine) {
            const isHorizontal = rect.width >= rect.height;
            elements.push({
              ...base,
              type: "line",
              x1: position.x,
              y1: position.y,
              x2: position.x + (isHorizontal ? position.w : 0),
              y2: position.y + (isHorizontal ? 0 : position.h),
              width: Math.max(0.5, pxToPt(isHorizontal ? rect.height : rect.width)),
              color: bg || normalizeHexColor(computed.borderColor, "D9DEE8"),
            });
            return;
          }

          if (bg || hasBorder) {
            const line = uniformBorder
              ? {
                  color: normalizeHexColor(computed.borderColor, bg || "D9DEE8"),
                  width: Math.max(0.25, pxToPt(computed.borderTopWidth)),
                }
              : { color: bg || "FFFFFF", transparency: 100 };
            elements.push({
              ...base,
              type: "shape",
              fill: bg,
              fillTransparency: bgTransparency,
              line,
              radius: pxToPt(computed.borderTopLeftRadius) / 72,
            });
          }
        }

        if (textTags.has(node.tagName)) {
          const text = (node.innerText || node.textContent || "").replace(/\s+\n/g, "\n").trim();
          if (!text) return;
          const color = normalizeHexColor(computed.color, "172033");
          const weight = Number.parseInt(computed.fontWeight, 10);
          elements.push({
            ...base,
            type: "text",
            text,
            bullet: node.tagName === "LI",
            style: {
              fontFace: (computed.fontFamily || "Aptos").split(",")[0].replace(/['"]/g, "").trim() || "Aptos",
              fontSize: Math.max(6, pxToPt(computed.fontSize) || 14),
              color,
              transparency: alphaTransparency(computed.color),
              bold: computed.fontWeight === "bold" || (Number.isFinite(weight) && weight >= 600),
              italic: computed.fontStyle === "italic",
              underline: (computed.textDecorationLine || "").includes("underline"),
              align: mapTextAlign(computed.textAlign),
            },
          });
        }
      });

      return { elements, warnings, errors };
    },
    { viewportWidth, viewportHeight, slideWidthIn, slideHeightIn }
  );
}

async function convert(manifestPath) {
  const manifest = readJson(manifestPath);
  const outputPath = path.resolve(manifest.outputPath);
  const reportPath = path.resolve(manifest.reportPath || `${outputPath}.report.json`);
  const viewportWidth = safeNumber(manifest.viewportWidth, 1280);
  const viewportHeight = safeNumber(manifest.viewportHeight, 720);
  const slideWidthIn = viewportWidth / PX_PER_IN;
  const slideHeightIn = viewportHeight / PX_PER_IN;
  const report = {
    ok: false,
    engine: "node_playwright_pptxgenjs",
    outputPath,
    warnings: [],
    errors: [],
    pages: [],
  };

  const pptx = new pptxgen();
  pptx.author = "Creative Claw";
  pptx.subject = "Editable PPTX exported from HTML route";
  pptx.title = manifest.title || "Creative Claw PPT";
  pptx.company = "Creative Claw";
  pptx.defineLayout({ name: "CREATIVE_CLAW_HTML", width: slideWidthIn, height: slideHeightIn });
  pptx.layout = "CREATIVE_CLAW_HTML";

  let browser;
  try {
    browser = await chromium.launch({ headless: true });
    for (const pageSpec of manifest.pages || []) {
      const pageReport = {
        slideNumber: pageSpec.slideNumber,
        status: "pending",
        warnings: [],
        errors: [],
        elementCount: 0,
      };
      report.pages.push(pageReport);

      const page = await browser.newPage({ viewport: { width: viewportWidth, height: viewportHeight }, deviceScaleFactor: 1 });
      try {
        const pageUrl = pathToFileURL(path.resolve(pageSpec.htmlPath)).href;
        await page.goto(pageUrl, { waitUntil: "networkidle" });
        const slideData = await extractSlide(page, manifest, pageSpec, slideWidthIn, slideHeightIn);
        pageReport.warnings.push(...Array.from(new Set(slideData.warnings || [])));
        pageReport.errors.push(...(slideData.errors || []));
        if (pageReport.errors.length > 0) {
          pageReport.status = "failed";
          continue;
        }
        const slide = pptx.addSlide();
        const elements = (slideData.elements || []).sort((a, b) => {
          if ((a.zIndex || 0) !== (b.zIndex || 0)) return (a.zIndex || 0) - (b.zIndex || 0);
          return (a.order || 0) - (b.order || 0);
        });
        for (const el of elements) {
          addElementToSlide(pptx, slide, el);
        }
        pageReport.elementCount = elements.length;
        pageReport.status = "html_to_pptx";
      } catch (err) {
        pageReport.status = "failed";
        pageReport.errors.push(`${err.name || "Error"}: ${err.message || String(err)}`);
      } finally {
        await page.close();
      }
    }

    const pageErrors = report.pages.flatMap((page) => page.errors.map((message) => `slide ${page.slideNumber}: ${message}`));
    if (pageErrors.length > 0) {
      report.errors.push(...pageErrors);
      writeJson(reportPath, report);
      process.exitCode = 2;
      return;
    }
    fs.mkdirSync(path.dirname(outputPath), { recursive: true });
    await pptx.writeFile({ fileName: outputPath });
    report.ok = true;
    report.warnings = report.pages.flatMap((page) => page.warnings.map((message) => `slide ${page.slideNumber}: ${message}`));
    writeJson(reportPath, report);
  } catch (err) {
    report.errors.push(`${err.name || "Error"}: ${err.message || String(err)}`);
    writeJson(reportPath, report);
    process.exitCode = 1;
  } finally {
    if (browser) {
      await browser.close();
    }
  }
}

const manifestPath = process.argv[2];
if (!manifestPath) {
  console.error("Usage: node html_to_pptx_converter.js <manifest.json>");
  process.exit(64);
}

convert(manifestPath).catch((err) => {
  console.error(err && err.stack ? err.stack : String(err));
  process.exit(1);
});
