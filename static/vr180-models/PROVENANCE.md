# Quest 3 Touch Plus and Quest 2 Touch controller models

The ringless Quest 3 Touch Plus assets use the official Immersive Web Input
Profiles [meta-quest-touch-plus](https://github.com/immersive-web/webxr-input-profiles/tree/f4992299601614adbfefd398dc8e281556bb7444/packages/assets/profiles/meta-quest-touch-plus) and
[`meta-quest-touch-plus-v2`](https://github.com/immersive-web/webxr-input-profiles/tree/f4992299601614adbfefd398dc8e281556bb7444/packages/assets/profiles/meta-quest-touch-plus-v2) models. Quest 2 retains
its official [`oculus-touch-v3`](https://github.com/immersive-web/webxr-input-profiles/tree/f4992299601614adbfefd398dc8e281556bb7444/packages/assets/profiles/oculus-touch-v3) model with tracking rings.
All files are pinned to commit `f4992299601614adbfefd398dc8e281556bb7444` and retrieved on
2026-11-21. They retain the upstream grip, trigger, squeeze button, thumbstick
and face buttons, in meters relative to WebXR `gripSpace`. The pointing beam
uses `targetRaySpace` separately.

## Source and license

Each profile's `upstream/left.glb`, `upstream/right.glb` and
`upstream/profile.json` are unmodified copies from
`packages/assets/profiles/<profile>/` at that commit. Quest 2's original files
remain in the top-level `upstream/` directory. The official registry profiles
for [Touch Plus](https://github.com/immersive-web/webxr-input-profiles/blob/f4992299601614adbfefd398dc8e281556bb7444/packages/registry/profiles/meta/meta-quest-touch-plus.json)
and [Touch Plus v2](https://github.com/immersive-web/webxr-input-profiles/blob/f4992299601614adbfefd398dc8e281556bb7444/packages/registry/profiles/meta/meta-quest-touch-plus-v2.json)
confirm those identifiers; v2 falls back to Touch Plus, and both list
`oculus-touch-v3` as a compatibility fallback. That fallback describes input
compatibility, so the renderer selects the primary profile to avoid displaying
a Quest 2 tracking ring on Quest 3.

The assets use the [upstream MIT license](https://github.com/immersive-web/webxr-input-profiles/blob/f4992299601614adbfefd398dc8e281556bb7444/packages/assets/LICENSE.md),
copied verbatim into `LICENSE.md`, with the notice
`Copyright (c) 2019 Amazon`. The [upstream trademark notice](https://github.com/immersive-web/webxr-input-profiles/blob/f4992299601614adbfefd398dc8e281556bb7444/packages/assets/README.md#trademarks)
reserves manufacturers' trademarks; depiction does not imply endorsement.

## Reproducible local conversion

Run `node static/vr180-models/bake.cjs` from the repository root. The offline
baker verifies the original GLB SHA-256 values before converting each default
scene hierarchy into one indexed mesh per hand. It applies upstream rotation,
translation and uniform scale, preserves physical size and UVs, and extracts
base-color PNGs without modification. Original GLBs remain bundled for offline
regeneration. The runtime needs no glTF loader or third-party requests.

Each `.bin` retains the existing ASCII `Q2M1` format marker followed by
little-endian uint32 vertex and index counts, interleaved float32
position/normal/UV values (32 bytes per vertex), then uint16 triangle indices.
The marker is a file-format version, not the device identifier.

Touch Plus has 4,470 triangles per hand, 3,342 left vertices and 3,340 right
vertices. Its bounds are approximately 6.8 by 6.0 by 12.3 cm. Each hand has its
own official color atlas; using a shared left/right texture would mislabel the
face buttons. Quest 2 has 5,831 triangles per hand and retains its shared atlas.
Buttons remain in the upstream rest pose; metallic/roughness maps and input
animations are omitted.

| Profile | Runtime assets before HTTP compression |
| --- | ---: |
| `oculus-touch-v3` | 1,006,820 bytes |
| `meta-quest-touch-plus` | 412,181 bytes |
| `meta-quest-touch-plus-v2` | 1,147,212 bytes |

## Runtime behavior

Each controller family loads locally once per VR session when its primary
profile first appears. Loading a Touch Plus family does not fetch Quest 2
assets. Recognized controllers stay hidden during loading, avoiding a flash
of procedural blocks. A failed load, unsupported device or missing grip pose
retains the procedural fallback. Hand tracking is excluded. Model buffers,
textures, in-flight requests and decoded bitmaps are released at session end.
Visibility still expires after three seconds of controller inactivity.

The caller supplies a single optional beam matrix and positive hit distance;
the renderer uses the hardware target-ray origin for that beam and renders it
in both eyes. Panel and cursor share the panel transform. No implicit beam is
added by model rendering.

Automated tests verify both profile versions, profile selection, separate
left/right textures, delayed loading, conversion, physical bounds, cleanup and
fallback. Rendered browser checks are separate from physical-headset acceptance.
Grip alignment, final visual fidelity and frame timing require a Quest 3 check.

## SHA-256 checksums

| File | SHA-256 |
| --- | --- |
| `upstream/left.glb` | `5a3015792dac074b31607e63eeb75743eac479540a36739e5f94b65335a7f88a` |
| `upstream/right.glb` | `18a2e651328b684d3aeeef9f16c083af1eea9c1f98f78f9da94960432c8a021b` |
| `upstream/profile.json` | `c8aa11c7aa0c30dd0703c5c2a2de69a2d8189f02f82f1598ffa32a100923ddbf` |
| `left.bin` | `2374dab4d6fe2c53eaf16212bb84cbc8c2ecd4b98d25ffd9a72d2fbcadb8d272` |
| `right.bin` | `9526d4aa8aac41310a03ce9531cd0823a4185c23bea812b8625f085db52691a1` |
| `color.png` | `3d63c42f09c6a7fd10c19658547d878c679df3d39fa59137510cb9819cf6741a` |
| `meta-quest-touch-plus/upstream/left.glb` | `41a35b80221398d37029cbfaa9e39084ccd43e0206be72a5e8460f52e7882cb4` |
| `meta-quest-touch-plus/upstream/right.glb` | `5126d638fb1d03e05d16d8eec3b31b9588df88eaa8b42cc21b857fac3bd44916` |
| `meta-quest-touch-plus/upstream/profile.json` | `51813881124cfe860cbea8816da374824619046463dccadc159df3119043a328` |
| `meta-quest-touch-plus/left.bin` | `cb55bbf9b8c085171365a25e8e796cd0a06c77a38f02294331b2767205d2891b` |
| `meta-quest-touch-plus/right.bin` | `684c5f8de839299cc241c6da594a28305654f4018a433ac07377be8899531c81` |
| `meta-quest-touch-plus/left.png` | `94503df522002c65fcf3e24595adfa498857beea40255627894d40fdc139e071` |
| `meta-quest-touch-plus/right.png` | `a565866f3f38e2dc8c96e6771455ffb7093df4d82d494f99d4308073997f573d` |
| `meta-quest-touch-plus-v2/upstream/left.glb` | `2df6f94439d23e9bedda4c60b55d2c45c477e2590b9338ad4f452a2b81fe27c7` |
| `meta-quest-touch-plus-v2/upstream/right.glb` | `380b1a6fb82fecaf76f8a3c202aadaa441d58da66020c692b90adc0e9490a672` |
| `meta-quest-touch-plus-v2/upstream/profile.json` | `b0949d266051702127b8ecc559ddbbf4c66351cede1ce35105bcbbd9b993f792` |
| `meta-quest-touch-plus-v2/left.bin` | `bb42cc189e93529c7bb4e61ab3219ac0702250d67d18f76c1bc1553069400b21` |
| `meta-quest-touch-plus-v2/right.bin` | `dd7234d72077577a7c111cb10a6b5209f4d230e14ea86f0411180f0bec5e1d93` |
| `meta-quest-touch-plus-v2/left.png` | `18ac87b3ce735bd74b53dbacc82c11e84a481af6fd3521db6bafe7114dd3fe99` |
| `meta-quest-touch-plus-v2/right.png` | `53fa2af6ced1ddb855b3b861aa4b0ccbecb353239c83f8382f81f3a8b6d90144` |
