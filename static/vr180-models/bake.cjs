// Offline conversion of the pinned upstream rest-pose models; no runtime glTF loader.
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');

const sourceHashes = {
  'oculus-touch-v3': {
    left: '5a3015792dac074b31607e63eeb75743eac479540a36739e5f94b65335a7f88a',
    right: '18a2e651328b684d3aeeef9f16c083af1eea9c1f98f78f9da94960432c8a021b',
  },
  'meta-quest-touch-plus': {
    left: '41a35b80221398d37029cbfaa9e39084ccd43e0206be72a5e8460f52e7882cb4',
    right: '5126d638fb1d03e05d16d8eec3b31b9588df88eaa8b42cc21b857fac3bd44916',
  },
  'meta-quest-touch-plus-v2': {
    left: '2df6f94439d23e9bedda4c60b55d2c45c477e2590b9338ad4f452a2b81fe27c7',
    right: '380b1a6fb82fecaf76f8a3c202aadaa441d58da66020c692b90adc0e9490a672',
  },
};
const identity = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];

function multiply(a, b) {
  return Array.from({ length: 16 }, (_, i) => {
    const row = i % 4, column = Math.floor(i / 4);
    return a[row] * b[column * 4] + a[row + 4] * b[column * 4 + 1]
      + a[row + 8] * b[column * 4 + 2] + a[row + 12] * b[column * 4 + 3];
  });
}

function transform(node) {
  const scale = node.scale || [1, 1, 1];
  assert.ok(scale.every(value => value > 0 && Math.abs(value - scale[0]) < 1e-7), 'Pinned assets use uniform positive scale.');
  if (node.matrix) return node.matrix;
  const [x, y, z, w] = node.rotation || [0, 0, 0, 1];
  const [tx, ty, tz] = node.translation || [0, 0, 0];
  return [1 - 2 * (y * y + z * z), 2 * (x * y + z * w), 2 * (x * z - y * w), 0,
    2 * (x * y - z * w), 1 - 2 * (x * x + z * z), 2 * (y * z + x * w), 0,
    2 * (x * z + y * w), 2 * (y * z - x * w), 1 - 2 * (x * x + y * y), 0,
    tx, ty, tz, 1].map((value, i) => i < 12 ? value * scale[Math.floor(i / 4)] : value);
}

function bake(bytes, handedness, profile = 'oculus-touch-v3') {
  assert.equal(crypto.createHash('sha256').update(bytes).digest('hex'), sourceHashes[profile][handedness]);
  assert.equal(bytes.readUInt32LE(0), 0x46546c67);
  assert.equal(bytes.readUInt32LE(4), 2);
  const jsonLength = bytes.readUInt32LE(12);
  const gltf = JSON.parse(bytes.toString('utf8', 20, 20 + jsonLength));
  const bin = bytes.subarray(28 + jsonLength);
  const vertices = [], indices = [];

  function accessor(index, expectedType) {
    const a = gltf.accessors[index], view = gltf.bufferViews[a.bufferView];
    assert.equal(a.componentType, expectedType);
    assert.equal(a.sparse, undefined);
    const width = { SCALAR: 1, VEC2: 2, VEC3: 3 }[a.type];
    const size = expectedType === 5126 ? 4 : 2;
    const stride = view.byteStride || width * size;
    const start = (view.byteOffset || 0) + (a.byteOffset || 0);
    return Array.from({ length: a.count }, (_, i) => Array.from({ length: width }, (_, k) =>
      expectedType === 5126 ? bin.readFloatLE(start + i * stride + k * size)
        : bin.readUInt16LE(start + i * stride + k * size)));
  }

  function visit(index, parent, parentScale = 1) {
    const node = gltf.nodes[index], matrix = multiply(parent, transform(node));
    const scale = parentScale * (node.scale?.[0] || 1);
    if (node.mesh !== undefined) {
      for (const p of gltf.meshes[node.mesh].primitives) {
        assert.equal(p.mode ?? 4, 4);
        assert.equal(p.material, 0);
        const points = accessor(p.attributes.POSITION, 5126);
        const normals = accessor(p.attributes.NORMAL, 5126);
        const uvs = accessor(p.attributes.TEXCOORD_0, 5126);
        const base = vertices.length / 8;
        for (let i = 0; i < points.length; i++) {
          for (const [value, translation] of [[points[i], true], [normals[i], false]]) {
            for (let axis = 0; axis < 3; axis++) {
              vertices.push((matrix[axis] * value[0] + matrix[axis + 4] * value[1]
                + matrix[axis + 8] * value[2]) / (translation ? 1 : scale) + (translation ? matrix[axis + 12] : 0));
            }
          }
          vertices.push(...uvs[i]);
        }
        for (const [value] of accessor(p.indices, 5123)) {
          assert.ok(value < points.length);
          indices.push(base + value);
        }
      }
    }
    for (const child of node.children || []) visit(child, matrix, scale);
  }
  for (const node of gltf.scenes[gltf.scene].nodes) visit(node, identity);
  assert.ok(vertices.every(Number.isFinite));
  assert.ok(vertices.length / 8 < 65536);
  assert.equal(indices.length % 3, 0);
  const mesh = Buffer.alloc(12 + vertices.length * 4 + indices.length * 2);
  mesh.write('Q2M1');
  mesh.writeUInt32LE(vertices.length / 8, 4);
  mesh.writeUInt32LE(indices.length, 8);
  vertices.forEach((v, i) => mesh.writeFloatLE(v, 12 + i * 4));
  indices.forEach((v, i) => mesh.writeUInt16LE(v, 12 + vertices.length * 4 + i * 2));
  const image = gltf.bufferViews[gltf.images[0].bufferView];
  return { mesh, texture: bin.subarray(image.byteOffset, image.byteOffset + image.byteLength) };
}

if (require.main === module) {
  for (const profile of Object.keys(sourceHashes)) {
    const directory = profile === 'oculus-touch-v3' ? __dirname : path.join(__dirname, profile);
    let texture;
    for (const hand of ['left', 'right']) {
      const result = bake(fs.readFileSync(path.join(directory, 'upstream', `${hand}.glb`)), hand, profile);
      fs.writeFileSync(path.join(directory, `${hand}.bin`), result.mesh);
      if (profile === 'oculus-touch-v3') {
        if (texture) assert.deepEqual(result.texture, texture);
        texture = result.texture;
      } else {
        fs.writeFileSync(path.join(directory, `${hand}.png`), result.texture);
      }
    }
    if (texture) fs.writeFileSync(path.join(directory, 'color.png'), texture);
  }
}
module.exports = { bake, sourceHashes };
