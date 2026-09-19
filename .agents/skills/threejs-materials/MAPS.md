# MAPS.md — Three.js Texture Map Reference

## Contents

- Complete map list for MeshStandardMaterial
- Color space rules
- UV channel requirements
- Normal map tangent space
- Environment maps
- Texture atlas

---

## Complete Map List — MeshStandardMaterial

| Property          | Type                | Color Space | UV Channel | Description                        |
| ----------------- | ------------------- | ----------- | ---------- | ---------------------------------- |
| `map`             | Texture             | sRGB        | uv         | Albedo / base color                |
| `normalMap`       | Texture             | Linear      | uv         | Surface normal perturbation        |
| `roughnessMap`    | Texture             | Linear      | uv         | Roughness (uses green channel)     |
| `metalnessMap`    | Texture             | Linear      | uv         | Metalness (uses blue channel)      |
| `aoMap`           | Texture             | Linear      | uv2/uv1    | Ambient occlusion                  |
| `emissiveMap`     | Texture             | sRGB        | uv         | Emissive color                     |
| `displacementMap` | Texture             | Linear      | uv         | Vertex displacement                |
| `alphaMap`        | Texture             | Linear      | uv         | Alpha / opacity (uses red channel) |
| `envMap`          | CubeTexture/Texture | sRGB        | —          | Environment reflection             |
| `lightMap`        | Texture             | Linear      | uv2/uv1    | Baked lightmap                     |
| `bumpMap`         | Texture             | Linear      | uv         | Bump map (alternative to normal)   |

---

## Color Space Rules

```js
// Color/albedo maps — human-visible color data
texture.colorSpace = THREE.SRGBColorSpace;

// All other maps — linear data, NOT perceptual
// normalMap, roughnessMap, metalnessMap, aoMap, displacementMap, alphaMap, bumpMap
// Leave these at default (LinearSRGBColorSpace) — do NOT set sRGB on them

// Wrong (breaks rendering):
normalMap.colorSpace = THREE.SRGBColorSpace; // ❌ distorts normals
```

---

## aoMap UV Channel

`aoMap` requires a second UV set. In Three.js r152+, use `uv1`:

```js
// r152+
geo.setAttribute("uv1", geo.attributes.uv); // copy primary UV to uv1

// Older Three.js (pre-r152)
geo.setAttribute("uv2", geo.attributes.uv); // copy to uv2
```

Same applies to `lightMap`.

---

## Normal Map Types

```js
// Default — tangent space (most common, works with any mesh orientation)
mat.normalMapType = THREE.TangentSpaceNormalMap;

// Object space — normals in object's local space (less common)
mat.normalMapType = THREE.ObjectSpaceNormalMap;

// Scale the normal effect
mat.normalScale = new THREE.Vector2(1, 1); // default
mat.normalScale.set(2, 2); // exaggerate
mat.normalScale.set(0.3, 0.3); // soften
```

---

## displacementMap

Physically moves vertices — geometry must have enough segments.

```js
mat.displacementMap = displacementTexture;
mat.displacementScale = 0.5; // how much to displace (world units)
mat.displacementBias = -0.25; // center offset

// Geometry MUST have subdivisions
const geo = new THREE.PlaneGeometry(10, 10, 256, 256); // 256x256 segments
```

---

## Environment Map

```js
// From HDRI (most realistic)
import { RGBELoader } from "three/addons/loaders/RGBELoader.js";
const pmrem = new THREE.PMREMGenerator(renderer);
const hdr = await new RGBELoader().loadAsync("/env/studio.hdr");
const envMap = pmrem.fromEquirectangular(hdr).texture;

// Apply globally to all PBR materials
scene.environment = envMap;

// Per-material override
mat.envMap = envMap;
mat.envMapIntensity = 1.5;

// From CubeTexture
mat.envMap = cubeTexture;
mat.envMapRotation.y = Math.PI / 2; // rotate env map
```

---

## Texture Atlas (multiple objects, one texture)

```js
// One large texture containing multiple objects' textures
// Set UV repeat/offset per object's material
const atlas = new THREE.TextureLoader().load("/atlas.jpg");

const matA = new THREE.MeshStandardMaterial({ map: atlas });
matA.map.offset.set(0, 0);
matA.map.repeat.set(0.5, 0.5);

const matB = new THREE.MeshStandardMaterial({ map: atlas });
// Share the same texture object — one GPU upload
matB.map = atlas;
matB.map = atlas.clone(); // clone if different repeat/offset
matB.map.offset.set(0.5, 0);
matB.map.repeat.set(0.5, 0.5);
```

---

## MeshPhysicalMaterial Additional Maps

```js
const mat = new THREE.MeshPhysicalMaterial();

mat.transmissionMap = loader.load("/t/transmission.jpg"); // glass area
mat.thicknessMap = loader.load("/t/thickness.jpg"); // refraction depth
mat.clearcoatMap = loader.load("/t/clearcoat.jpg"); // clearcoat intensity
mat.clearcoatNormalMap = loader.load("/t/clearcoat-normal.jpg"); // clearcoat normals
mat.sheenColorMap = loader.load("/t/sheen.jpg"); // fabric sheen color
mat.iridescenceMap = loader.load("/t/iridescence.jpg"); // soap bubble iridescence
mat.specularColorMap = loader.load("/t/specular.jpg"); // F0 specular color
mat.specularIntensityMap = loader.load("/t/specular-intensity.jpg");
```
