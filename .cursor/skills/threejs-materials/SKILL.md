---
name: threejs-materials
description: "Configures Three.js materials — MeshStandardMaterial, MeshPhysicalMaterial, PBR pipeline, texture maps (color, normal, roughness, metalness, AO, emissive, environment). Use when the user asks about materials, textures, PBR, shading, opacity, transparency, or material properties. Trigger keywords: material, texture, PBR, roughness, metalness, normal map, emissive, transparent, MeshStandardMaterial."
---

# Three.js Materials

## MeshStandardMaterial (default PBR material)

```js
const mat = new THREE.MeshStandardMaterial({
  color: 0xffffff, // base color (hex or THREE.Color)
  roughness: 0.5, // 0 = mirror, 1 = fully diffuse
  metalness: 0.0, // 0 = dielectric, 1 = metal
  envMapIntensity: 1.0, // environment map strength
});
```

See [MAPS.md](MAPS.md) for complete texture map reference.

## MeshPhysicalMaterial (extended PBR)

```js
const mat = new THREE.MeshPhysicalMaterial({
  roughness: 0.1,
  metalness: 0.0,
  transmission: 1.0, // glass / transparency (0–1)
  thickness: 0.5, // refraction thickness
  ior: 1.5, // index of refraction (glass = 1.5)
  reflectivity: 0.5,
  iridescence: 1.0, // soap bubble iridescence
  iridescenceIOR: 1.3,
  sheen: 1.0, // fabric-like sheen
  sheenColor: 0xffffff,
  clearcoat: 1.0, // car paint clear coat
  clearcoatRoughness: 0.1,
});
// Note: transmission requires renderer.physicallyCorrectLights or specific setup
```

## Loading Textures

```js
const loader = new THREE.TextureLoader();
const texture = loader.load("/textures/diffuse.jpg", (tex) => {
  tex.colorSpace = THREE.SRGBColorSpace; // for color/albedo maps
});

// For non-color maps (normal, roughness, metalness, AO) — linear space
const normalMap = loader.load("/textures/normal.jpg");
// normalMap.colorSpace stays LinearSRGBColorSpace (default) — do NOT set sRGB

const mat = new THREE.MeshStandardMaterial({
  map: texture, // albedo / color
  normalMap: normalMap,
  roughnessMap: loader.load("/textures/roughness.jpg"),
  metalnessMap: loader.load("/textures/metalness.jpg"),
  aoMap: loader.load("/textures/ao.jpg"), // requires uv2
  aoMapIntensity: 1.0,
  emissiveMap: loader.load("/textures/emissive.jpg"),
  emissive: new THREE.Color(0xffffff),
  emissiveIntensity: 1.0,
});
```

## Texture Settings

```js
texture.wrapS = THREE.RepeatWrapping;
texture.wrapT = THREE.RepeatWrapping;
texture.repeat.set(4, 4); // tile 4x in each direction
texture.offset.set(0.5, 0); // offset UV
texture.rotation = Math.PI / 4;
texture.center.set(0.5, 0.5); // rotation pivot

// Filter — affects sharpness
texture.minFilter = THREE.LinearMipmapLinearFilter; // default, best quality
texture.magFilter = THREE.LinearFilter;
texture.anisotropy = renderer.capabilities.getMaxAnisotropy(); // max sharpness at angle
texture.generateMipmaps = true; // default true
```

## Environment Map

```js
import { RGBELoader } from "three/addons/loaders/RGBELoader.js";
import { PMREMGenerator } from "three";

const pmrem = new PMREMGenerator(renderer);
pmrem.compileEquirectangularShader();

new RGBELoader().load("/env/studio.hdr", (hdr) => {
  const envMap = pmrem.fromEquirectangular(hdr).texture;
  scene.background = envMap;
  scene.environment = envMap; // applies to ALL PBR materials
  hdr.dispose();
  pmrem.dispose();
});
```

## Transparency & Blending

```js
// Alpha via texture
mat.alphaMap = loader.load("/textures/alpha.jpg");
mat.transparent = true;
mat.alphaTest = 0.5; // discard below threshold (no depth sorting needed)

// Full transparency with blending
mat.transparent = true;
mat.opacity = 0.5;
mat.depthWrite = false; // prevents z-fighting for transparent objects
mat.side = THREE.DoubleSide; // render front and back

// Blending modes
mat.blending = THREE.NormalBlending;
mat.blending = THREE.AdditiveBlending; // glow, particles
mat.blending = THREE.MultiplyBlending;
```

## Other Materials

```js
new THREE.MeshBasicMaterial({ color: 0xff0000 }); // unlit, no shadows
new THREE.MeshLambertMaterial({ color: 0xff0000 }); // flat shading, fast
new THREE.MeshPhongMaterial({ color: 0xff0000, shininess: 100 }); // specular
new THREE.MeshToonMaterial({ color: 0xff0000 }); // cel shading
new THREE.MeshNormalMaterial(); // debug normals
new THREE.MeshDepthMaterial(); // debug depth
new THREE.LineBasicMaterial({ color: 0xffffff });
new THREE.LineDashedMaterial({ color: 0xffffff, dashSize: 0.1, gapSize: 0.05 });
new THREE.PointsMaterial({ size: 0.05, sizeAttenuation: true });
new THREE.SpriteMaterial({ map: texture });
```

## Material Disposal

```js
mat.dispose(); // always dispose when removing from scene
// dispose all textures separately:
mat.map?.dispose();
mat.normalMap?.dispose();
```

## Common Gotchas

- `aoMap` requires a second UV set: `geo.setAttribute('uv2', geo.attributes.uv)` or `uv1` in r152+
- Color/albedo textures: set `colorSpace = THREE.SRGBColorSpace`
- Non-color textures (normal, rough, metal, AO): leave as `LinearSRGBColorSpace`
- `transparent: true` triggers alpha sorting — use `alphaTest` instead for hard cutouts
- `MeshPhysicalMaterial` with `transmission` requires a background or `scene.environment`
- `envMapIntensity` only works on `MeshStandardMaterial` and `MeshPhysicalMaterial`
