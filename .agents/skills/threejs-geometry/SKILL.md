---
name: threejs-geometry
description: "Creates and manipulates Three.js geometry — built-in primitives, BufferGeometry, custom geometry with attributes, normals, UVs, indexed and non-indexed meshes. Use when the user asks about geometry, shapes, BufferGeometry, custom vertices, normals, UVs, or mesh topology. Trigger keywords: geometry, BufferGeometry, vertices, normals, UVs, mesh topology, custom shape."
---

# Three.js Geometry

## Built-in Geometries

```js
// Common primitives — all accept (width, height, depth, widthSegments, ...)
new THREE.BoxGeometry(1, 1, 1);
new THREE.SphereGeometry(0.5, 32, 16);
new THREE.PlaneGeometry(10, 10, 100, 100); // high segments for displacement
new THREE.CylinderGeometry(0.5, 0.5, 2, 32);
new THREE.ConeGeometry(0.5, 2, 32);
new THREE.TorusGeometry(1, 0.4, 16, 100);
new THREE.TorusKnotGeometry(1, 0.4, 128, 16);
new THREE.CircleGeometry(1, 32);
new THREE.RingGeometry(0.5, 1, 32);
new THREE.DodecahedronGeometry(1);
new THREE.IcosahedronGeometry(1, 4); // last param = subdivisions
new THREE.OctahedronGeometry(1);
new THREE.TetrahedronGeometry(1);
new THREE.CapsuleGeometry(0.5, 1, 4, 8); // r143+
new THREE.TubeGeometry(path, 64, 0.2, 8, false); // needs THREE.CatmullRomCurve3
new THREE.LatheGeometry(points, 12);
new THREE.ShapeGeometry(shape);
new THREE.ExtrudeGeometry(shape, { depth: 1, bevelEnabled: false });
```

## Accessing Attributes

```js
const geo = new THREE.BoxGeometry(1, 1, 1);
const positions = geo.attributes.position; // Float32BufferAttribute
const normals = geo.attributes.normal;
const uvs = geo.attributes.uv;
const index = geo.index; // null if non-indexed

// Read a vertex
const x = positions.getX(i);
const y = positions.getY(i);

// Modify a vertex (requires needsUpdate)
positions.setXYZ(i, x + 0.1, y, z);
positions.needsUpdate = true;
geo.computeVertexNormals(); // recompute after vertex changes
```

## Custom BufferGeometry

```js
const geo = new THREE.BufferGeometry();

// Triangle (non-indexed)
const vertices = new Float32Array([
  // x      y      z
  -0.5, -0.5, 0, 0.5, -0.5, 0, 0.0, 0.5, 0,
]);
geo.setAttribute("position", new THREE.BufferAttribute(vertices, 3));

// Custom normals
const normals = new Float32Array([0, 0, 1, 0, 0, 1, 0, 0, 1]);
geo.setAttribute("normal", new THREE.BufferAttribute(normals, 3));

// Custom UVs
const uvs = new Float32Array([0, 0, 1, 0, 0.5, 1]);
geo.setAttribute("uv", new THREE.BufferAttribute(uvs, 2));

geo.computeVertexNormals(); // or set manually above
```

## Indexed BufferGeometry

```js
const geo = new THREE.BufferGeometry();

// 4 unique vertices for a quad
const positions = new Float32Array([
  -0.5,
  -0.5,
  0, // 0
  0.5,
  -0.5,
  0, // 1
  0.5,
  0.5,
  0, // 2
  -0.5,
  0.5,
  0, // 3
]);
geo.setAttribute("position", new THREE.BufferAttribute(positions, 3));

// Two triangles sharing vertices
const indices = new Uint16Array([0, 1, 2, 0, 2, 3]);
geo.setIndex(new THREE.BufferAttribute(indices, 1));
geo.computeVertexNormals();
```

## Merging Geometries

```js
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";

const merged = mergeGeometries([geo1, geo2, geo3]);
const mesh = new THREE.Mesh(merged, material);
// One draw call for all geometries — great for static scenes
```

## Bounding Box / Sphere

```js
geo.computeBoundingBox();
geo.computeBoundingSphere();

const box = geo.boundingBox; // THREE.Box3
const center = new THREE.Vector3();
box.getCenter(center);

const sphere = geo.boundingSphere; // THREE.Sphere
```

## Geometry Disposal

```js
// Always dispose geometry when removing from scene
geo.dispose();
mat.dispose();
texture.dispose();
```

## Dynamic / Morphing Geometry

```js
// For frequent updates — set usage hint
geo.setAttribute(
  "position",
  new THREE.BufferAttribute(verts, 3).setUsage(THREE.DynamicDrawUsage),
);
// Then after updates:
geo.attributes.position.needsUpdate = true;
```

## Common Gotchas

- `BoxBufferGeometry`, `SphereBufferGeometry` etc → **removed in r160**, use non-Buffer versions
- After modifying `position` attribute: call `geo.computeVertexNormals()` to fix lighting
- `geo.index` is `null` on non-indexed geometry — check before accessing
- Always `dispose()` geometry you're removing to prevent GPU memory leaks
- `setUsage(THREE.DynamicDrawUsage)` before first render, not after
