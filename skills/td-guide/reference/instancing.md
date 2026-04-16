# Instancing

Instancing draws N copies of one mesh in a single GPU call, with per-instance position/rotation/scale/color pulled from a data source. Use it any time you'd otherwise have more than ~20 Geo COMPs. The performance ceiling is enormous (tens of thousands of instances at 60 FPS on a mid-range GPU).

You are instancing **on a Geometry COMP**, not on a SOP. The SOP inside the Geo is the mesh template.

## Data sources

A Geometry COMP's `instanceop` parameter accepts:

- A **CHOP** — one sample per instance, channels per attribute (`tx`, `ty`, `tz`, `rx`, `ry`, `rz`, `sx`, `sy`, `sz`, plus colors, custom attribs).
- A **DAT** (table) — one row per instance, named columns.
- A **SOP** — one instance per point of the SOP; point attributes supply tx/ty/tz, plus rotation/scale if present.
- A **POP** (TD 2025+) — the modern path; stays on the GPU.

CHOP vs SOP vs POP choice:
- **CHOP** for signal-driven motion (LFOs, audio, OSC feeds).
- **SOP** for positions that come from procedural geometry (scatter on a surface, points along a curve).
- **POP** for anything heavy or GPU-native. Prefer this on TD 2025+.
- **DAT** for fixed tables / spreadsheet-authored layouts.

## Minimal setup: grid of 1000 cubes driven by a noise CHOP

```json
{ "type": "box",   "family": "sop",  "parent": "/project1/geo1", "name": "box1",
  "params": { "sizex": 0.1, "sizey": 0.1, "sizez": 0.1 } }

{ "type": "pattern", "family": "chop", "name": "grid1",
  "params": { "length": 1000 } }

{ "type": "noise",   "family": "chop", "name": "pos1",
  "params": { "channels": "tx ty tz", "amp": 2.0, "period": 4 } }
```

Then set Geo instancing via `td_params`:

```json
{ "op": "/project1/geo1",
  "params": {
    "instanceactive": true,
    "instanceop": "../pos1",
    "instancetx": "tx",
    "instancety": "ty",
    "instancetz": "tz"
  } }
```

## Rotation and scale

On the Geo COMP's Instance page:
- `instancerx`, `instancery`, `instancerz` — map channel/column names to rotation.
- `instancesx`, `instancesy`, `instancesz` — scale. Leave blank for 1.0.
- `instancer` (Rotate Order) — default `XYZ` is fine most of the time.

## Per-instance color & custom attributes

`instancecolor` parameter page lets you pull r/g/b/a from the same source. For custom attributes passed to a GLSL MAT, use the Instance Texture / Instance CHOP reference on the Geo COMP and read them in the shader as attributes.

## Mesh pool / LOD

One Geometry COMP = one template mesh. For varied shapes, either:
- Use a `switch` SOP inside the Geo keyed by instance index (cheap, one draw).
- Use several Geometry COMPs each with its own instance source (one draw call each).

## Common pitfalls

- `instanceactive` left Off — Geo renders one mesh at origin and you wonder where your 10k cubes went.
- CHOP source has fewer samples than expected — TD only draws that many instances. `pattern` CHOP with `length` N is the clean way to set instance count.
- Using a SOP source that's been cooked into a cache — instances freeze. Clear the cache or use a non-caching upstream.
- Mixing CPU-heavy SOP generation with GPU instancing defeats the point. Generate positions as CHOP/POP if possible.
- Instance texture sample rate mismatch — if 1024 instances pull from a 512-pixel texture, half the indices wrap. Match resolution to instance count or use explicit index math.
- Forgetting that instancing happens inside the MAT's vertex stage. Custom GLSL MAT must call `TDDeform(P)` to pick up instance transforms.
