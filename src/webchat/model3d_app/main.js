import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { OBJLoader } from "three/examples/jsm/loaders/OBJLoader.js";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";

const SUPPORTED_EXTENSIONS = [".glb", ".gltf", ".obj", ".stl"];

function mount(element, options = {}) {
  const viewer = new CreativeClawModelViewer(element, options);
  viewer.load(options.src || "");
  return {
    resetCamera: () => viewer.resetCamera(),
    unmount: () => viewer.dispose(),
  };
}

class CreativeClawModelViewer {
  constructor(element, options) {
    this.element = element;
    this.name = options.name || "3D model";
    this.frameId = 0;
    this.model = null;
    this.disposed = false;
    this.initialCameraState = null;

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

    this.gltfLoader = new GLTFLoader();
    this.objLoader = new OBJLoader();
    this.stlLoader = new STLLoader();
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

  load(src) {
    if (!src) {
      this.showError("No model source was provided.");
      return;
    }
    const extension = extensionFromSource(src) || extensionFromSource(this.name);
    if (!SUPPORTED_EXTENSIONS.includes(extension)) {
      this.showError("Inline preview is not available for this 3D format.");
      return;
    }
    this.showStatus("Loading model...");

    if (extension === ".obj") {
      this.loadObj(src);
      return;
    }
    if (extension === ".stl") {
      this.loadStl(src);
      return;
    }
    this.loadGltf(src);
  }

  loadGltf(src) {
    this.gltfLoader.load(
      src,
      (gltf) => {
        if (this.disposed) {
          disposeObject(gltf.scene);
          return;
        }
        this.setModel(gltf.scene);
        this.showStatus("");
      },
      (event) => this.updateLoadingProgress(event),
      (error) => {
        console.warn(error);
        this.showError("Could not load this 3D model.");
      }
    );
  }

  loadObj(src) {
    this.objLoader.load(
      src,
      (object) => {
        if (this.disposed) {
          disposeObject(object);
          return;
        }
        applyFallbackMaterial(object);
        this.setModel(object);
        this.showStatus("");
      },
      (event) => this.updateLoadingProgress(event),
      (error) => {
        console.warn(error);
        this.showError("Could not load this OBJ model.");
      }
    );
  }

  loadStl(src) {
    this.stlLoader.load(
      src,
      (geometry) => {
        if (this.disposed) {
          geometry.dispose();
          return;
        }
        geometry.computeVertexNormals();
        const mesh = new THREE.Mesh(geometry, createFallbackMaterial());
        mesh.name = this.name;
        this.setModel(mesh);
        this.showStatus("");
      },
      (event) => this.updateLoadingProgress(event),
      (error) => {
        console.warn(error);
        this.showError("Could not load this STL model.");
      }
    );
  }

  updateLoadingProgress(event) {
    if (!event.total) {
      return;
    }
    const percent = Math.round((event.loaded / event.total) * 100);
    this.showStatus(`Loading model... ${percent}%`);
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

function extensionFromSource(source) {
  const cleaned = String(source || "").split("?")[0].split("#")[0].toLowerCase();
  const dotIndex = cleaned.lastIndexOf(".");
  return dotIndex >= 0 ? cleaned.slice(dotIndex) : "";
}

window.CreativeClaw3D = {
  mount,
  supportedExtensions: SUPPORTED_EXTENSIONS,
};
