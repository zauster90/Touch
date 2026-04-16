# Operators

TouchDesigner is a dataflow system. Every node is an "operator" belonging to one of 7 families. Pick the right family first; everything else follows. Wrong family means you are fighting TD.

Operators wire through **inputs** (left side) and **outputs** (right). Same-family wiring is the normal case — TOP→TOP, CHOP→CHOP. Cross-family happens through *converter* ops (`topto`, `chopto`, `sopto`, `chopto`, etc.) or through parameters referencing another op.

## The 7 families

| Family | Domain | Color | When to use |
|--------|--------|-------|-------------|
| TOP | 2D raster (GPU textures) | purple | images, video, shaders, compositing, render output |
| SOP | 3D geometry (CPU meshes) | blue | modeling, procedural geometry, per-vertex edits |
| POP | 3D points (GPU, TD 2025+) | teal | GPU-accelerated point ops, particles, heavy instancing data |
| CHOP | channels / time-series / signals | green | audio, animation, timing, control data, MIDI, OSC |
| DAT | text, tables, scripts | salmon | strings, tables, Python scripts, JSON, text files |
| COMP | containers, 3D objects, UI | grey/black | Geometry, Camera, Light, Base, Container, Window |
| MAT | materials (shaders bound to geo) | brown | PBR MAT, Phong MAT, Constant MAT, GLSL MAT |

## Common ops per family

- **TOP**: `constant`, `noise`, `movieFileIn`, `rampTOP`, `composite`, `level`, `blur`, `feedback`, `render`, `glsl`
- **SOP**: `box`, `sphere`, `grid`, `torus`, `noise` (SOP), `transform`, `copy`, `merge`, `facet`
- **POP** (TD 2025+): `sourcePOP`, `transformPOP`, `noisePOP`, `glslPOP`, `mergePOP`
- **CHOP**: `constant`, `noise` (CHOP), `pattern`, `lfo`, `math`, `select`, `merge`, `timer`, `audiodevin`, `midiin`, `osc in`
- **DAT**: `text`, `table`, `execute`, `chopexec`, `datexec`, `parameter`, `opfind`, `info`
- **COMP**: `geo` (Geometry COMP), `cam` (Camera), `light`, `env` (Environment Light), `base`, `container`, `window`
- **MAT**: `pbrMAT`, `phongMAT`, `constantMAT`, `glslMAT`, `wireframeMAT`

## Creating ops

`td_create` takes a `type` that is the lowercase operator name (same as you'd type in the OP Create dialog), plus an optional `parent` path and optional `inputs` array of op paths to wire immediately.

```json
{
  "type": "noise",
  "family": "top",
  "parent": "/project1",
  "name": "noise1",
  "params": { "resolutionw": 1920, "resolutionh": 1080 }
}
```

Wire while creating:

```json
{
  "type": "composite",
  "family": "top",
  "parent": "/project1",
  "name": "comp1",
  "inputs": ["/project1/noise1", "/project1/movieIn1"]
}
```

For Python fallback via `td_execute`, the idiomatic form is:

```python
n = parent().create(noiseTOP, 'noise1')
n.par.resolutionw = 1920
n.par.resolutionh = 1080
```

## TD 2025: POP family

POPs are new first-class GPU point operators. They supersede the older pattern of using SOPs with CHOPs for instance data. If you're generating large particle fields, instance positions, or per-point attributes, **reach for POPs first** — they stay on the GPU, where SOPs would round-trip through CPU memory.

A POP chain ends at a `popToSOP` (for rendering via a Geometry COMP that points at the SOP) or is rendered directly by newer paths that accept POPs as instance sources.

## Common pitfalls

- Mixing families without a converter — silently does nothing or throws a type error invisible in the UI.
- Creating a SOP when a POP would do — you pay CPU cost and lose parallelism.
- Forgetting that TOP resolution is set by the *first TOP in the chain*, not downstream. Set `resolutionw`/`resolutionh` on the source, or use a `resolutionTOP`.
- Assuming CHOPs sample at your screen rate — they cook at `project.cookRate` (60 FPS default). Set sample rate explicitly if you care.
- Naming clashes: TD auto-suffixes (`noise1`, `noise2`) but `td_create` with an explicit `name` will fail if one exists.
