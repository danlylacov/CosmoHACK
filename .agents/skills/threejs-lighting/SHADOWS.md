# SHADOWS.md — Three.js Shadow Configuration

## Contents

- Shadow setup checklist
- DirectionalLight shadow camera
- PointLight shadows
- SpotLight shadows
- Shadow bias
- VSM shadows
- Performance

---

## Shadow Setup Checklist

Every shadow requires ALL of these:

```js
// 1. Renderer
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;

// 2. Light
directionalLight.castShadow = true;
directionalLight.shadow.mapSize.set(2048, 2048); // power of 2

// 3. Caster mesh
mesh.castShadow = true;

// 4. Receiver mesh
ground.receiveShadow = true;
```

Missing any one of these = no shadow.

---

## DirectionalLight Shadow Camera

DirectionalLight uses an `OrthographicCamera` for shadow projection. It MUST encompass your entire scene.

```js
const dir = new THREE.DirectionalLight(0xffffff, 1.5);
dir.position.set(10, 20, 10);
dir.castShadow = true;

// Tightly wrap the shadow frustum around your scene
dir.shadow.camera.near = 1;
dir.shadow.camera.far = 60;
dir.shadow.camera.left = -20;
dir.shadow.camera.right = 20;
dir.shadow.camera.top = 20;
dir.shadow.camera.bottom = -20;

dir.shadow.mapSize.set(2048, 2048);

// Debug — visualize the shadow camera
const helper = new THREE.CameraHelper(dir.shadow.camera);
scene.add(helper);
// Remove helper when done — it costs draw calls
```

**Rule:** Frustum too large → blurry shadows. Too small → objects outside frustum cast no shadow.

---

## Shadow Bias

Shadow acne (dark stripes on lit surfaces) vs peter-panning (shadow detaches from object):

```js
dir.shadow.bias = -0.001; // fix shadow acne (start here)
dir.shadow.normalBias = 0.02; // better for curved surfaces, reduces acne

// Tune: increase |bias| for acne, decrease for peter-panning
// normalBias is generally more reliable than bias for curved/organic geometry
```

---

## PointLight Shadows

```js
const point = new THREE.PointLight(0xff8800, 2, 20);
point.castShadow = true;
point.shadow.mapSize.set(1024, 1024);
point.shadow.camera.near = 0.5;
point.shadow.camera.far = 20; // match light distance

// PointLight shadow uses a cube map (6 renders per frame) — expensive
// Use sparingly — 1-2 max in a scene
```

---

## SpotLight Shadows

```js
const spot = new THREE.SpotLight(0xffffff, 3);
spot.castShadow = true;
spot.shadow.mapSize.set(1024, 1024);
spot.shadow.camera.near = 1;
spot.shadow.camera.far = 30;
spot.shadow.focus = 1; // shadow sharpness (0–1)

// SpotLight uses PerspectiveCamera for shadow — updates automatically with angle
```

---

## VSM (Variance Shadow Maps)

```js
renderer.shadowMap.type = THREE.VSMShadowMap;

dir.shadow.mapSize.set(2048, 2048);
dir.shadow.radius = 4; // blur radius for soft shadows

// VSM pros: very soft shadows, no bias tuning needed
// VSM cons: light bleeding on thin geometry, higher memory
```

---

## Shadow Performance

```js
// mapSize — larger = sharper but more memory and fill cost
// 512 = mobile, 1024 = default, 2048 = high quality, 4096 = very expensive

// Reduce shadow-casting lights
// Aim for 1 DirectionalLight with shadows in most scenes

// Freeze shadow map for static scenes
renderer.shadowMap.autoUpdate = false;
renderer.shadowMap.needsUpdate = true; // trigger one-time update

// Re-enable for dynamic scenes:
renderer.shadowMap.autoUpdate = true;

// Baked shadows — for fully static scenes, use aoMap + lightMap instead
// Zero runtime cost vs dynamic shadows
```

---

## Common Shadow Problems

| Problem                         | Likely Cause                                                | Fix                                      |
| ------------------------------- | ----------------------------------------------------------- | ---------------------------------------- | ---- | --- |
| No shadow at all                | Missing `castShadow`/`receiveShadow` or `shadowMap.enabled` | Check all 4 requirements                 |
| Shadow acne (stripes)           | `shadow.bias` too small                                     | Set `bias = -0.001`, `normalBias = 0.02` |
| Shadow detached (peter-panning) | `shadow.bias` too large                                     | Reduce `                                 | bias | `   |
| Shadow cut off                  | Frustum too small                                           | Expand `shadow.camera` bounds            |
| Shadow blurry                   | `mapSize` too small or frustum too large                    | Increase `mapSize`, tighten frustum      |
| PointLight shadow missing face  | `shadow.camera.far` too small                               | Match `far` to light `distance`          |
