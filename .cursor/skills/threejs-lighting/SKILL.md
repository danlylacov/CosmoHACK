---
name: threejs-lighting
description: "Configures Three.js lights — AmbientLight, DirectionalLight, PointLight, SpotLight, RectAreaLight, shadows, HDR environment lighting, and light probes. Use when the user asks about lighting, shadows, light setup, HDR, environment maps for lighting, or realistic rendering. Trigger keywords: light, lighting, shadow, DirectionalLight, PointLight, SpotLight, ambient, HDRI, environment."
---

# Three.js Lighting

## Light Types

```js
// Ambient — omnidirectional, no shadows
const ambient = new THREE.AmbientLight(0xffffff, 0.5); // color, intensity
scene.add(ambient);

// Hemisphere — sky/ground gradient
const hemi = new THREE.HemisphereLight(0x87ceeb, 0x8b7355, 0.5); // sky, ground, intensity
scene.add(hemi);

// Directional — parallel rays, simulates sun, supports shadows
const dir = new THREE.DirectionalLight(0xffffff, 1.5);
dir.position.set(5, 10, 5);
dir.castShadow = true;
scene.add(dir);

// Point — omnidirectional from a point, supports shadows
const point = new THREE.PointLight(0xff8800, 2, 20, 2); // color, intensity, distance, decay
point.position.set(0, 3, 0);
scene.add(point);

// Spot — cone of light, supports shadows
const spot = new THREE.SpotLight(0xffffff, 3, 30, Math.PI / 6, 0.3, 2);
// color, intensity, distance, angle, penumbra (softness), decay
spot.position.set(0, 10, 0);
spot.target.position.set(0, 0, 0);
scene.add(spot);
scene.add(spot.target);

// RectAreaLight — rectangular light source (like a panel), no shadows
const rectArea = new THREE.RectAreaLight(0xffffff, 5, 4, 2); // color, intensity, width, height
rectArea.position.set(0, 3, 5);
rectArea.lookAt(0, 0, 0);
scene.add(rectArea);
// RectAreaLight requires RectAreaLightUniformsLib
import { RectAreaLightUniformsLib } from "three/addons/lights/RectAreaLightUniformsLib.js";
RectAreaLightUniformsLib.init();
```

See [SHADOWS.md](SHADOWS.md) for complete shadow configuration.

## Shadow Setup

```js
// 1. Enable renderer shadows
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap; // recommended

// 2. Light must castShadow
dir.castShadow = true;

// 3. Shadow map resolution (powers of 2)
dir.shadow.mapSize.set(2048, 2048); // default 512

// 4. Shadow camera frustum (DirectionalLight uses OrthographicCamera)
dir.shadow.camera.near = 0.5;
dir.shadow.camera.far = 50;
dir.shadow.camera.left = -10;
dir.shadow.camera.right = 10;
dir.shadow.camera.top = 10;
dir.shadow.camera.bottom = -10;

// 5. Bias — prevents shadow acne (peter-panning if too large)
dir.shadow.bias = -0.001;
dir.shadow.normalBias = 0.02; // better for curved surfaces

// 6. Mesh must castShadow and/or receiveShadow
mesh.castShadow = true;
ground.receiveShadow = true;

// Debug shadow camera
const helper = new THREE.CameraHelper(dir.shadow.camera);
scene.add(helper);
```

## HDRI Environment Lighting

```js
import { RGBELoader } from "three/addons/loaders/RGBELoader.js";

const pmrem = new THREE.PMREMGenerator(renderer);
pmrem.compileEquirectangularShader();

new RGBELoader().load("/env/studio.hdr", (hdr) => {
  const envMap = pmrem.fromEquirectangular(hdr).texture;
  scene.background = envMap; // visible background
  scene.environment = envMap; // applied to all PBR materials
  hdr.dispose();
  pmrem.dispose();
});
```

## Light Intensity Scale

Three.js r155+ uses physical light units when `renderer.useLegacyLights = false` (default from r155):

```js
// Physical units (r155+ default)
dir.intensity = 3; // lux for directional
point.intensity = 100; // candela for point/spot

// Legacy mode (pre-r155 behavior)
renderer.useLegacyLights = true; // restore old behavior
```

## Light Helpers

```js
scene.add(new THREE.DirectionalLightHelper(dir, 1));
scene.add(new THREE.PointLightHelper(point, 0.3));
scene.add(new THREE.SpotLightHelper(spot));
scene.add(new THREE.HemisphereLightHelper(hemi, 1));
// Update helpers that track targets:
spotHelper.update();
```

## Common Gotchas

- `RectAreaLight` does NOT cast shadows
- `AmbientLight` and `HemisphereLight` do NOT cast shadows
- `SpotLight.target` must be added to the scene separately
- Shadow camera frustum must tightly wrap your scene — too large = blurry shadows
- `shadow.bias` fixes acne but too negative causes peter-panning (shadow detaches)
- Physical light units (r155+): multiply your old intensity values by ~π for similar results
- Always `CameraHelper` the shadow camera when debugging shadow issues
