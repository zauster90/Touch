# Rendering

To see 3D in TD, you wire four things together: **Geometry COMP** (what to draw) + **Camera COMP** (viewpoint) + **Light COMP** (illumination, optional for unlit materials) + **Render TOP** (the executor producing the final 2D image).

Everything downstream of that is 2D compositing on TOPs. The Render TOP is the one-way gate from 3D to 2D.

## The pipeline

```
  [SOP chain] ──▶ Geometry COMP ──┐
                                  │
                      Camera COMP ─┼──▶ Render TOP ──▶ (post TOPs) ──▶ output
                                  │
                       Light COMP ─┘

  Geometry COMP.par.material = [MAT path]
```

- **Geometry COMP**: a component that wraps SOPs and holds material/render flags. Set `Render` to On. Set `par.material` to a MAT path.
- **Camera COMP**: pinhole by default. Key params: `tx/ty/tz` position, `rx/ry/rz` rotation, `fov`, `near`, `far`. Or use a Look-At constraint by setting `lookat` to another COMP path.
- **Light COMP**: has a `lighttype` menu — `point`, `cone` (spotlight), `distant`, `environment`. Environment Lights (often their own `envlight` op) are what drives IBL for PBR MAT.
- **Render TOP**: params point at the pieces: `geometry`, `camera`, `lights`. It can accept multiple Geo/Light COMPs as space-separated paths. Set `resolutionw`/`resolutionh` here.

## MAT attachment

MATs don't wire — they're referenced by path from a Geometry COMP's `material` parameter. Same MAT can be used by many Geos.

- `constantMAT` — unlit, one color. Fastest. Use for UI, debug, emissive geo.
- `phongMAT` — classic diffuse + specular + normal. Cheap and "looks fine".
- `pbrMAT` — physically based; needs an Environment Light for reflections to look right.
- `glslMAT` — write your own. See [glsl](glsl.md).

## Minimal "rotating cube" recipe

Using `td_create` calls in sequence (parent = `/project1`):

```json
{ "type": "box",    "family": "sop",  "name": "box1" }
{ "type": "geo",    "family": "comp", "name": "geo1" }
{ "type": "cam",    "family": "comp", "name": "cam1",  "params": { "tz": 5 } }
{ "type": "light",  "family": "comp", "name": "light1" }
{ "type": "constantMAT", "family": "mat", "name": "mat1", "params": { "colorr": 1, "colorg": 0.3, "colorb": 0.7 } }
{ "type": "render", "family": "top",  "name": "render1",
  "params": { "geometry": "geo1", "camera": "cam1", "lights": "light1",
              "resolutionw": 1280, "resolutionh": 720 } }
```

Then, inside `/project1/geo1`, drop the `box1` SOP (or create it there originally) and set its Render/Display flags. Point `geo1.par.material` at `/project1/mat1`. Animate `geo1.par.rx` with an LFO CHOP or an expression (`absTime.seconds * 30`).

Via `td_params`:

```json
{ "op": "/project1/geo1", "params": { "material": "../mat1", "render": true, "display": true } }
{ "op": "/project1/geo1", "params": { "rx": "absTime.seconds * 30" } }
```

## Debugging a black render

1. `td_screenshot /project1/render1` — confirm it's actually black, not just unmapped.
2. Check `geo1.par.render` is On and `geo1.par.display` is On.
3. Is `cam1` looking at the geo? `tz` positive usually means camera on +Z looking at origin; geo at origin should appear. Pull camera back if geo is huge.
4. PBR MAT with no Environment Light renders near-black. Add an `envlight` COMP.
5. Normals flipped (boxes and primitives usually fine; imported geo often wrong) — slap a `facet` SOP with Compute Normals On.

## Common pitfalls

- Forgetting to set the Geometry COMP's `Render` flag — geo exists in the scene but is invisible.
- Render TOP `lights` parameter left empty with a PBR MAT — you get unlit-looking output.
- Camera `near`/`far` clipping geometry that's too close or too far. Default near is 0.1.
- Multiple Geos in one Render TOP must all be listed in the Render TOP's `geometry` param, space-separated — it doesn't walk the network.
- Z-fighting on coplanar geo: offset by a tiny amount or use a depth offset in the MAT.
