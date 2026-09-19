---
name: threejs-core
description: "Sets up Three.js renderer, scene, WebGL context, animation loop, and resize handling. Use when the user asks to create a 3D scene, set up Three.js from scratch, initialize WebGL, create a render loop, or configure a renderer. Trigger keywords: three.js, threejs, WebGL, 3D scene, renderer, requestAnimationFrame."
---

# Three.js Core Setup

## Canonical Scene Setup

```js
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

// Renderer — always set pixelRatio, tone mapping, and shadow map
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2)); // cap at 2
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1;
renderer.outputColorSpace = THREE.SRGBColorSpace; // r152+
document.body.appendChild(renderer.domElement);

// Scene
const scene = new THREE.Scene();

// Camera
const camera = new THREE.PerspectiveCamera(
  75, // fov
  window.innerWidth / window.innerHeight, // aspect
  0.1, // near
  1000, // far
);
camera.position.set(0, 2, 5);
scene.add(camera);

// Controls — always call controls.update() in the loop if enableDamping: true
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.05;

// Resize — always update aspect + projection matrix + renderer size
window.addEventListener("resize", () => {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
});

// Render loop
function animate() {
  requestAnimationFrame(animate);
  controls.update(); // required when enableDamping: true
  renderer.render(scene, camera);
}
animate();
```

## Renderer Options

```js
const renderer = new THREE.WebGLRenderer({
  antialias: true, // smooth edges (performance cost on mobile)
  alpha: true, // transparent canvas background
  canvas: myCanvas, // attach to existing <canvas> element
  powerPreference: "high-performance", // request discrete GPU
  logarithmicDepthBuffer: true, // fix z-fighting on large scenes
});
```

## Tone Mapping Options

```js
renderer.toneMapping = THREE.NoToneMapping; // raw linear
renderer.toneMapping = THREE.LinearToneMapping; // simple linear
renderer.toneMapping = THREE.ReinhardToneMapping; // classic photographic
renderer.toneMapping = THREE.CineonToneMapping; // filmic
renderer.toneMapping = THREE.ACESFilmicToneMapping; // recommended default
renderer.toneMapping = THREE.AgXToneMapping; // r158+ neutral
renderer.toneMapping = THREE.NeutralToneMapping; // r165+ khronos neutral
```

## Shadow Map Types

```js
renderer.shadowMap.type = THREE.BasicShadowMap; // fastest, aliased
renderer.shadowMap.type = THREE.PCFShadowMap; // smoothed
renderer.shadowMap.type = THREE.PCFSoftShadowMap; // recommended
renderer.shadowMap.type = THREE.VSMShadowMap; // variance, soft but bleeds
```

## Fixed Canvas (not full-screen)

```js
const W = 800,
  H = 600;
renderer.setSize(W, H);
camera.aspect = W / H;
camera.updateProjectionMatrix();
// No resize listener needed
```

## Clock (delta time)

```js
const clock = new THREE.Clock();

function animate() {
  requestAnimationFrame(animate);
  const delta = clock.getDelta(); // seconds since last frame
  const elapsed = clock.getElapsedTime(); // total time
  mesh.rotation.y += delta * 0.5; // frame-rate independent rotation
  renderer.render(scene, camera);
}
```

## Common Gotchas

- `BoxBufferGeometry` → removed in r160, use `BoxGeometry`
- `sRGBEncoding` → removed in r152, use `outputColorSpace = THREE.SRGBColorSpace`
- `renderer.gammaOutput` → removed in r136, replaced by `outputColorSpace`
- Always `scene.add(camera)` if you want audio listeners or child objects on camera
- Never call `renderer.render()` outside the loop (causes double frames)
- Always clamp `devicePixelRatio` to avoid rendering 3x or 4x pixels on high-DPI screens

## Imports (r160+)

```js
// Correct
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

// Old — still works but deprecated path
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
```

All addons live under `three/addons/` in r160+.
