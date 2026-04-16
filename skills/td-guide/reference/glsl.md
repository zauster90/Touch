# GLSL

GLSL in TD appears in three places, each with different I/O conventions. Confusing them is the #1 reason Claude-authored shaders don't compile.

- **GLSL TOP**: per-pixel over a 2D raster. Basically a fullscreen quad fragment shader.
- **GLSL MAT**: a material bound to 3D geometry. Runs vertex + pixel per rendered mesh. Has access to TD's standard lighting uniforms.
- **GLSL POP** (TD 2025+): compute-style, runs per point in a POP stream.

After editing any GLSL op, call `td_errors` — TD reports compile/link failures there, not in a popup.

## GLSL TOP

One file, one stage (pixel is common; there's also a separate Vertex DAT if you need it). Default boilerplate:

```glsl
// Pixel Shader
out vec4 fragColor;

void main() {
    vec2 uv = vUV.st;               // 0..1 across the TOP
    vec4 src = texture(sTD2DInputs[0], uv);
    fragColor = TDOutputSwizzle(src);
}
```

- `sTD2DInputs[i]` — samplers for inputs wired to the GLSL TOP.
- `vUV.st` — normalized UVs. `vUV.p` is array layer for 2D-array inputs.
- `TDOutputSwizzle()` — wrap your final color. TD handles color-space/layout quirks inside it.
- Extra samplers beyond inputs: add a parameter of type `TOP` or `Sampler2D` via Customize Component, then `uniform sampler2D sMyMap;` (name matches par token).
- Extra scalar/vector uniforms: add a Float/Int/RGB parameter. Access as `uniform float uStrength;` — the `u`-prefix is TD convention for param-driven uniforms, but the binding is by parameter name set in the op's `Vectors` / `Arrays` page.

Add a custom uniform via `td_params`:

```json
{ "op": "/project1/glsl1",
  "params": { "uniname0": "uStrength", "value0x": 0.8 } }
```

## GLSL MAT

Vertex and pixel stages live in two DAT-like slots. You get TD's scene uniforms for free:

- `uTDMats[i]` — per-instance/per-camera matrices (`world`, `cam`, `camInverse`, `proj`, `worldCam`, `worldCamInverse`, `camProj`).
- `uTDGeneral` — time, resolution, etc.
- `uTDLights[i]`, `uTDEnvLights[i]` — lighting state.

Minimum viable vertex:

```glsl
void main() {
    vec4 worldSpacePos = TDDeform(P);
    gl_Position = TDWorldToProj(worldSpacePos);
}
```

Minimum viable pixel:

```glsl
out vec4 fragColor;
void main() {
    fragColor = TDOutputSwizzle(vec4(1, 0, 0, 1));
}
```

Use `TDDeform`, `TDWorldToProj`, `TDLighting` — the TD helper macros handle instancing, bone deformation, and the standard BRDF. Roll-your-own matrix math breaks instancing.

## GLSL POP (TD 2025+)

Compute-style: one invocation per point. Read point attributes, write point attributes.

```glsl
void main() {
    vec3 p = inPoint.P;
    p.y += sin(p.x * 4.0 + uTime) * 0.1;
    outPoint.P = p;
}
```

Attribute set depends on upstream POPs; check the POP's Info CHOP for the schema.

## Common pitfalls

- Forgetting `TDOutputSwizzle()` — colors look wrong on some OS/driver combos.
- Using `texture2D()` (GLSL 1.20) instead of `texture()` (GLSL 3.30+). TD uses modern GLSL.
- Adding a uniform to the shader source without also registering it on the op's parameters — it silently stays at 0.
- Editing a GLSL TOP and not checking `td_errors`. The op will display black with no indication the shader failed.
- GLSL MAT: writing to `gl_Position` from raw `P` without `TDDeform`/`TDWorldToProj`. Works for a single non-instanced mesh, breaks the moment you instance it.
- Sampler parameter name mismatch: the sampler's **name** in the op's Samplers page must match the `uniform sampler2D <name>` in the shader exactly.
