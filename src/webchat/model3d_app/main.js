import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { FBXLoader } from "three/examples/jsm/loaders/FBXLoader.js";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { MTLLoader } from "three/examples/jsm/loaders/MTLLoader.js";
import { OBJLoader } from "three/examples/jsm/loaders/OBJLoader.js";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";
import { USDLoader } from "three/examples/jsm/loaders/USDLoader.js";

const SUPPORTED_EXTENSIONS = [".fbx", ".glb", ".gltf", ".obj", ".stl", ".usd", ".usda", ".usdc", ".usdz"];

function mount(element, options = {}) {
  const viewer = new CreativeClawModelViewer(element, options);
  viewer.load(options.src || "", options.packageManifestUrl || "");
  return {
    resetCamera: () => viewer.resetCamera(),
    unmount: () => viewer.dispose(),
  };
}

class CreativeClawModelViewer {
  constructor(element, options) {
    this.element = element;
    this.name = options.name || "3D model";
    this.sizeBytes = Number(options.sizeBytes || 0);
    this.frameId = 0;
    this.model = null;
    this.disposed = false;
    this.initialCameraState = null;
    this.objectUrls = [];

    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0xf3f5ef);

    this.camera = new THREE.PerspectiveCamera(45, 1, 0.01, 1000);
    this.camera.position.set(2.5, 1.8, 3.4);

    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.05;
    this.renderer.domElement.className = "model3d-canvas";
    this.renderer.domElement.setAttribute("aria-label", `${this.name} preview`);

    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;
    this.controls.screenSpacePanning = true;

    this.status = document.createElement("div");
    this.status.className = "model3d-status";
    this.status.textContent = "Loading model...";

    this.element.classList.add("model3d-viewer-mounted");
    this.element.appendChild(this.renderer.domElement);
    this.element.appendChild(this.status);
    this.addLights();
    this.addGround();

    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(this.element);
    this.resize();
    this.animate();
  }

  addLights() {
    const hemi = new THREE.HemisphereLight(0xffffff, 0x6f7a72, 1.25);
    this.scene.add(hemi);

    const key = new THREE.DirectionalLight(0xffffff, 2.2);
    key.position.set(4, 6, 5);
    this.scene.add(key);

    const fill = new THREE.DirectionalLight(0xb8d8ff, 0.85);
    fill.position.set(-5, 3, -4);
    this.scene.add(fill);
  }

  addGround() {
    const grid = new THREE.GridHelper(10, 20, 0x8a968d, 0xc7cec5);
    grid.material.transparent = true;
    grid.material.opacity = 0.34;
    grid.position.y = -0.01;
    this.scene.add(grid);
  }

  load(src, packageManifestUrl = "") {
    if (packageManifestUrl) {
      this.loadPackage(packageManifestUrl);
      return;
    }
    if (!src) {
      this.showError("No model source was provided.");
      return;
    }
    const extension = extensionFromSource(src) || extensionFromSource(this.name);
    this.loadByExtension(src, extension);
  }

  async loadPackage(manifestUrl) {
    this.showStatus("Inspecting model package...");
    try {
      const response = await fetch(manifestUrl, { cache: "no-store" });
      if (!response.ok) {
        throw new Error(`Package manifest request failed with status ${response.status}.`);
      }
      const manifest = await response.json();
      if (this.disposed) {
        return;
      }
      const modelUrl = String(manifest.modelUrl || "");
      const modelEntry = String(manifest.modelEntry || "");
      const fileUrl = String(manifest.fileUrl || "");
      if (!modelUrl || !modelEntry || !fileUrl) {
        throw new Error("Package manifest did not include a previewable model.");
      }
      const extension = extensionFromSource(modelEntry);
      const packageContext = {
        fileUrl,
        modelDirectory: String(manifest.modelDirectory || ""),
        modelSizeBytes: Number(manifest.modelSizeBytes || 0),
      };
      if (extension === ".gltf") {
        await this.loadPackagedGltf(modelUrl, packageContext);
        return;
      }
      const manager = createPackageLoadingManager(packageContext);
      this.loadByExtension(modelUrl, extension, manager, packageContext);
    } catch (error) {
      console.warn(error);
      this.showError("Could not inspect this 3D model package.");
    }
  }

  async loadPackagedGltf(modelUrl, packageContext) {
    this.showStatus("Loading model package...");
    const response = await fetch(modelUrl, { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`Packaged glTF request failed with status ${response.status}.`);
    }
    const gltf = await response.json();
    rewritePackagedGltfUris(gltf, packageContext);
    const objectUrl = URL.createObjectURL(
      new Blob([JSON.stringify(gltf)], { type: "model/gltf+json" })
    );
    this.objectUrls.push(objectUrl);
    this.loadByExtension(objectUrl, ".gltf");
  }

  loadByExtension(src, extension, manager = null, packageContext = null) {
    if (!SUPPORTED_EXTENSIONS.includes(extension)) {
      this.showError("Inline preview is not available for this 3D format.");
      return;
    }
    this.showStatus(`Preparing ${formatExtensionLabel(extension)} preview...`);

    if (extension === ".fbx") {
      this.loadFbx(src, manager);
      return;
    }
    if (extension === ".obj") {
      this.loadObj(src, manager, packageContext);
      return;
    }
    if (extension === ".stl") {
      this.loadStl(src, manager);
      return;
    }
    if (isUsdExtension(extension)) {
      this.loadUsd(src, manager);
      return;
    }
    this.loadGltf(src, manager);
  }

  loadGltf(src, manager = null) {
    const loader = new GLTFLoader(manager || undefined);
    loader.load(
      src,
      (gltf) => {
        if (this.disposed) {
          disposeObject(gltf.scene);
          return;
        }
        this.showStatus("Building scene...");
        this.setModel(gltf.scene);
        this.showStatus("");
      },
      (event) => this.updateLoadingProgress(event, "Downloading GLTF/GLB"),
      (error) => {
        console.warn(error);
        this.showError("Could not load this GLTF/GLB model.");
      }
    );
  }

  loadFbx(src, manager = null) {
    const loader = new FBXLoader(manager || undefined);
    loader.load(
      src,
      (object) => {
        if (this.disposed) {
          disposeObject(object);
          return;
        }
        this.showStatus("Building scene...");
        applyFallbackMaterial(object);
        this.setModel(object);
        this.showStatus("");
      },
      (event) => this.updateLoadingProgress(event, "Downloading FBX"),
      (error) => {
        console.warn(error);
        this.showError("Could not load this FBX model.");
      }
    );
  }

  async loadObj(src, manager = null, packageContext = null) {
    try {
      const objText = await this.fetchTextWithProgress(
        src,
        "Downloading OBJ",
        Number(packageContext?.modelSizeBytes || this.sizeBytes || 0)
      );
      if (this.disposed) {
        return;
      }

      this.showStatus("Parsing OBJ geometry...");
      const loader = new OBJLoader(manager || undefined);
      const materialCreator = await this.loadObjMaterials(objText, src, manager, packageContext);
      if (materialCreator) {
        loader.setMaterials(materialCreator);
      }
      const object = loader.parse(objText);
      if (this.disposed) {
        disposeObject(object);
        return;
      }
      applyFallbackMaterial(object);
      this.setModel(object);
      this.showStatus("");
    } catch (error) {
      console.warn(error);
      this.showError("Could not load this OBJ model.");
    }
  }

  async loadObjMaterials(objText, objSrc, manager = null, packageContext = null) {
    const materialName = firstObjMaterialLibrary(objText);
    if (!materialName) {
      return null;
    }
    try {
      const materialUrl = packageContext
        ? packageEntryUrl(packageContext, materialName)
        : new URL(materialName, objSrc).href;
      const mtlText = await this.fetchTextWithProgress(materialUrl, "Loading OBJ materials");
      const loader = new MTLLoader(manager || undefined);
      const basePath = packageContext ? "" : directoryUrlForSource(materialUrl);
      const materialCreator = loader.parse(mtlText, basePath);
      materialCreator.preload();
      enhanceMtlMaterialsForPbr(materialCreator);
      return materialCreator;
    } catch (error) {
      console.warn(error);
      return null;
    }
  }

  loadStl(src, manager = null) {
    const loader = new STLLoader(manager || undefined);
    loader.load(
      src,
      (geometry) => {
        if (this.disposed) {
          geometry.dispose();
          return;
        }
        geometry.computeVertexNormals();
        const mesh = new THREE.Mesh(geometry, createFallbackMaterial());
        mesh.name = this.name;
        this.showStatus("Building scene...");
        this.setModel(mesh);
        this.showStatus("");
      },
      (event) => this.updateLoadingProgress(event, "Downloading STL"),
      (error) => {
        console.warn(error);
        this.showError("Could not load this STL model.");
      }
    );
  }

  loadUsd(src, manager = null) {
    const loader = new USDLoader(manager || undefined);
    loader.load(
      src,
      (object) => {
        if (this.disposed) {
          disposeObject(object);
          return;
        }
        this.showStatus("Building scene...");
        applyFallbackMaterial(object);
        this.setModel(object);
        this.showStatus("");
      },
      (event) => this.updateLoadingProgress(event, "Downloading USD/USDZ"),
      (error) => {
        console.warn(error);
        this.showError("Could not load this USD/USDZ model.");
      }
    );
  }

  updateLoadingProgress(event, stage = "Loading model") {
    this.showStatus(progressStatusText(stage, event.loaded, event.total || this.sizeBytes));
  }

  async fetchTextWithProgress(url, stage, totalFallback = 0) {
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`${stage} request failed with status ${response.status}.`);
    }
    const total = Number(response.headers.get("Content-Length") || totalFallback || 0);
    if (!response.body?.getReader) {
      this.showStatus(stage);
      return response.text();
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let loaded = 0;
    let result = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }
      loaded += value.byteLength;
      result += decoder.decode(value, { stream: true });
      this.showStatus(progressStatusText(stage, loaded, total));
    }
    result += decoder.decode();
    return result;
  }

  setModel(model) {
    if (this.model) {
      this.scene.remove(this.model);
      disposeObject(this.model);
    }
    this.model = model;
    this.scene.add(model);

    const bounds = new THREE.Box3().setFromObject(model);
    if (bounds.isEmpty()) {
      this.resetCamera();
      return;
    }

    const center = bounds.getCenter(new THREE.Vector3());
    const size = bounds.getSize(new THREE.Vector3());
    model.position.sub(center);

    const maxDim = Math.max(size.x, size.y, size.z, 1);
    const fov = THREE.MathUtils.degToRad(this.camera.fov);
    const distance = Math.max(maxDim / (2 * Math.tan(fov / 2)), maxDim) * 1.55;

    this.camera.near = Math.max(distance / 100, 0.01);
    this.camera.far = Math.max(distance * 100, 1000);
    this.camera.position.set(distance * 0.8, distance * 0.55, distance);
    this.camera.updateProjectionMatrix();

    this.controls.target.set(0, 0, 0);
    this.controls.minDistance = distance * 0.08;
    this.controls.maxDistance = distance * 8;
    this.controls.update();

    this.initialCameraState = {
      position: this.camera.position.clone(),
      target: this.controls.target.clone(),
    };
  }

  resetCamera() {
    if (this.initialCameraState) {
      this.camera.position.copy(this.initialCameraState.position);
      this.controls.target.copy(this.initialCameraState.target);
    } else {
      this.camera.position.set(2.5, 1.8, 3.4);
      this.controls.target.set(0, 0, 0);
    }
    this.controls.update();
  }

  showStatus(message) {
    this.status.textContent = message;
    this.status.hidden = !message;
    this.status.classList.remove("error");
  }

  showError(message) {
    this.status.textContent = message;
    this.status.hidden = false;
    this.status.classList.add("error");
  }

  resize() {
    const rect = this.element.getBoundingClientRect();
    const width = Math.max(Math.floor(rect.width), 1);
    const height = Math.max(Math.floor(rect.height), 1);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(width, height, false);
  }

  animate() {
    if (this.disposed) {
      return;
    }
    this.frameId = window.requestAnimationFrame(() => this.animate());
    this.controls.update();
    this.renderer.render(this.scene, this.camera);
  }

  dispose() {
    this.disposed = true;
    if (this.frameId) {
      window.cancelAnimationFrame(this.frameId);
    }
    this.resizeObserver.disconnect();
    this.controls.dispose();
    if (this.model) {
      this.scene.remove(this.model);
      disposeObject(this.model);
    }
    this.renderer.dispose();
    for (const objectUrl of this.objectUrls) {
      URL.revokeObjectURL(objectUrl);
    }
    this.objectUrls = [];
    this.element.replaceChildren();
    this.element.classList.remove("model3d-viewer-mounted");
  }
}

function disposeObject(object) {
  object.traverse((child) => {
    if (child.geometry) {
      child.geometry.dispose();
    }
    if (child.material) {
      const materials = Array.isArray(child.material) ? child.material : [child.material];
      for (const material of materials) {
        for (const value of Object.values(material)) {
          if (value && typeof value.dispose === "function" && value.isTexture) {
            value.dispose();
          }
        }
        material.dispose();
      }
    }
  });
}

function applyFallbackMaterial(object) {
  object.traverse((child) => {
    if (!child.isMesh || child.material) {
      return;
    }
    child.material = createFallbackMaterial();
  });
}

function createFallbackMaterial() {
  return new THREE.MeshStandardMaterial({
    color: 0xd8ded6,
    roughness: 0.82,
    metalness: 0.05,
    side: THREE.DoubleSide,
  });
}

function enhanceMtlMaterialsForPbr(materialCreator) {
  const materialsInfo = materialCreator?.materialsInfo || {};
  for (const [name, info] of Object.entries(materialsInfo)) {
    const sourceMaterial = materialCreator.materials?.[name];
    if (!sourceMaterial || !hasPbrMtlChannels(info)) {
      continue;
    }

    const metalnessMap = loadMtlTexture(materialCreator, info.map_pm || info.map_metallic || "");
    const roughnessMap = loadMtlTexture(materialCreator, info.map_pr || info.map_roughness || "");
    const bumpValue = info.norm || info.map_bump || info.bump || "";
    const standardMaterial = new THREE.MeshStandardMaterial({
      name: sourceMaterial.name || name,
      side: sourceMaterial.side,
      color: sourceMaterial.color ? sourceMaterial.color.clone() : new THREE.Color(0xd8ded6),
      map: sourceMaterial.map || null,
      emissive: sourceMaterial.emissive ? sourceMaterial.emissive.clone() : new THREE.Color(0x000000),
      emissiveMap: sourceMaterial.emissiveMap || null,
      alphaMap: sourceMaterial.alphaMap || null,
      transparent: sourceMaterial.transparent,
      opacity: sourceMaterial.opacity,
      displacementMap: sourceMaterial.displacementMap || null,
      displacementScale: sourceMaterial.displacementScale,
      displacementBias: sourceMaterial.displacementBias,
      roughness: parseMtlNumber(info.pr, roughnessMap ? 1 : roughnessFromShininess(sourceMaterial.shininess)),
      metalness: parseMtlNumber(info.pm, metalnessMap ? 1 : 0),
      roughnessMap,
      metalnessMap,
    });

    if (sourceMaterial.normalMap) {
      standardMaterial.normalMap = sourceMaterial.normalMap;
      standardMaterial.normalScale = sourceMaterial.normalScale;
    } else if (sourceMaterial.bumpMap && looksLikeNormalTexture(bumpValue)) {
      standardMaterial.normalMap = sourceMaterial.bumpMap;
    } else {
      standardMaterial.bumpMap = sourceMaterial.bumpMap || null;
      standardMaterial.bumpScale = sourceMaterial.bumpScale;
    }

    materialCreator.materials[name] = standardMaterial;
  }
}

function hasPbrMtlChannels(info) {
  return Boolean(
    info?.map_pm ||
      info?.map_pr ||
      info?.map_metallic ||
      info?.map_roughness ||
      info?.pm ||
      info?.pr
  );
}

function loadMtlTexture(materialCreator, value) {
  if (!value) {
    return null;
  }
  const texParams = materialCreator.getTextureParams(String(value), {});
  if (!texParams.url) {
    return null;
  }
  const texture = new THREE.TextureLoader(materialCreator.manager).load(
    resolveMtlTextureUrl(materialCreator.baseUrl, texParams.url)
  );
  texture.repeat.copy(texParams.scale);
  texture.offset.copy(texParams.offset);
  texture.wrapS = materialCreator.wrap;
  texture.wrapT = materialCreator.wrap;
  return texture;
}

function resolveMtlTextureUrl(baseUrl, url) {
  const value = String(url || "");
  if (!value || isExternalResourceUrl(value)) {
    return value;
  }
  if (!baseUrl) {
    return value;
  }
  try {
    return new URL(value, baseUrl).href;
  } catch {
    return `${baseUrl}${value}`;
  }
}

function parseMtlNumber(value, fallback) {
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function roughnessFromShininess(shininess) {
  const value = Number(shininess);
  if (!Number.isFinite(value)) {
    return 0.82;
  }
  return THREE.MathUtils.clamp(1 - value / 1000, 0.08, 1);
}

function looksLikeNormalTexture(value) {
  return /(^|[._/\-])norm(al)?([._/\-]|$)/i.test(String(value || ""));
}

function extensionFromSource(source) {
  const cleaned = String(source || "").split("?")[0].split("#")[0].toLowerCase();
  const dotIndex = cleaned.lastIndexOf(".");
  return dotIndex >= 0 ? cleaned.slice(dotIndex) : "";
}

function isUsdExtension(extension) {
  return [".usd", ".usda", ".usdc", ".usdz"].includes(extension);
}

function formatExtensionLabel(extension) {
  if (extension === ".glb" || extension === ".gltf") {
    return "GLTF/GLB";
  }
  if (isUsdExtension(extension)) {
    return "USD/USDZ";
  }
  return extension.replace(".", "").toUpperCase() || "3D";
}

function progressStatusText(stage, loaded, total) {
  const loadedBytes = Number(loaded || 0);
  const totalBytes = Number(total || 0);
  if (loadedBytes > 0 && totalBytes > 0) {
    const percent = Math.min(100, Math.round((loadedBytes / totalBytes) * 100));
    return `${stage}... ${percent}% (${formatBytes(loadedBytes)} / ${formatBytes(totalBytes)})`;
  }
  if (loadedBytes > 0) {
    return `${stage}... ${formatBytes(loadedBytes)}`;
  }
  return `${stage}...`;
}

function formatBytes(value) {
  const bytes = Number(value || 0);
  if (!Number.isFinite(bytes) || bytes <= 0) {
    return "0 B";
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

function createPackageLoadingManager({ fileUrl, modelDirectory }) {
  const manager = new THREE.LoadingManager();
  manager.setURLModifier((url) => {
    if (isExternalResourceUrl(url)) {
      return url;
    }
    const entry = joinPackageEntry(modelDirectory, url);
    return absoluteResourceUrl(`${fileUrl}?entry=${encodeURIComponent(entry)}`);
  });
  return manager;
}

function rewritePackagedGltfUris(gltf, packageContext) {
  for (const buffer of gltf.buffers || []) {
    if (buffer?.uri && !isExternalResourceUrl(buffer.uri)) {
      buffer.uri = packageEntryUrl(packageContext, buffer.uri);
    }
  }
  for (const image of gltf.images || []) {
    if (image?.uri && !isExternalResourceUrl(image.uri)) {
      image.uri = packageEntryUrl(packageContext, image.uri);
    }
  }
}

function packageEntryUrl({ fileUrl, modelDirectory }, uri) {
  const entry = joinPackageEntry(modelDirectory, uri);
  return absoluteResourceUrl(`${fileUrl}?entry=${encodeURIComponent(entry)}`);
}

function firstObjMaterialLibrary(objText) {
  for (const line of String(objText || "").split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) {
      continue;
    }
    const match = /^mtllib\s+(.+)$/i.exec(trimmed);
    if (match?.[1]) {
      return match[1].trim();
    }
  }
  return "";
}

function directoryUrlForSource(source) {
  return new URL(".", source).href;
}

function absoluteResourceUrl(url) {
  return new URL(String(url || ""), window.location.href).href;
}

function isExternalResourceUrl(url) {
  const value = String(url || "");
  return (
    value.startsWith("http://") ||
    value.startsWith("https://") ||
    value.startsWith("data:") ||
    value.startsWith("blob:") ||
    value.startsWith("/")
  );
}

function joinPackageEntry(baseDirectory, url) {
  const cleanUrl = String(url || "").split("?")[0].split("#")[0].replace(/\\/g, "/");
  const segments = [];
  for (const part of `${baseDirectory || ""}/${cleanUrl}`.split("/")) {
    if (!part || part === ".") {
      continue;
    }
    if (part === "..") {
      segments.pop();
      continue;
    }
    segments.push(part);
  }
  return segments.join("/");
}

window.CreativeClaw3D = {
  mount,
  supportedExtensions: SUPPORTED_EXTENSIONS,
};
