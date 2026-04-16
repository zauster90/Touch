# Feedback

A feedback loop lets this frame's image be influenced by the last frame's image. It is the backbone of motion trails, accumulation buffers, reaction-diffusion, simple fluid sims, and classic "painterly" looks. TD handles the one-frame cycle cleanly via the **Feedback TOP**, which breaks what would otherwise be a circular reference.

## The canonical pattern

```
  source TOP ─────▶ Composite TOP ──┬──▶ (output)
                          ▲         │
                          │         │
                     Feedback TOP ◀─┘
                     (target = composite1)
```

- The Feedback TOP's `top` parameter points at the TOP whose **previous-frame** output you want to read.
- The Composite TOP (or an Over TOP, Add TOP, whatever blend you need) mixes the live source with last-frame feedback.
- On frame 1, Feedback TOP outputs black; after that, it's the previous frame's result.

## Minimal recipe

```json
{ "type": "noise",     "family": "top", "name": "src1" }
{ "type": "composite", "family": "top", "name": "comp1",
  "inputs": ["/project1/src1", "/project1/fb1"],
  "params": { "operand": "add" } }
{ "type": "feedback",  "family": "top", "name": "fb1",
  "params": { "top": "comp1" } }
```

Then decay the feedback so it doesn't saturate:

```
  Feedback TOP ──▶ Level TOP (opacity 0.95) ──▶ into Composite
```

Or use a Transform TOP before the Level to shift/rotate/scale the feedback — that's what produces classic fractal / zoom-tunnel effects.

## Reset

Feedback TOP has a `Reset` toggle and a `Reset Pulse` momentary. Scripted reset:

```python
op('fb1').par.resetpulse.pulse()
```

Or drive it from a CHOP (e.g., a Button COMP routed into a CHOP → Pulse on rising edge) so automations can clear the buffer without Python:

- Put a `constant` CHOP or a UI button feeding into a `null` CHOP, named something like `reset_sig`.
- On the Feedback TOP, the `resetpulse` parameter can be driven by expression: `op('reset_sig')['chan1']`.

## Decay strategy

The single biggest gotcha: no decay = runaway saturation. White-out or black-out within a few seconds. Fixes:

- `level` TOP with `opacity` ~0.92–0.98 on the feedback branch (easiest).
- `math` TOP with multiply < 1.0 (same idea).
- If you want color-specific decay (e.g., fade to red), use a `lookup` TOP on the feedback branch.

## Resolution changes

Feedback is pinned to a specific resolution at cook time. If you change the source resolution on the fly, old feedback is stretched/cropped weirdly. Solution: pulse `Reset` on any resolution change, or keep resolution driven by a single constant.

## Common pitfalls

- No decay → saturates within seconds. Always put a Level/Math TOP inline.
- Feedback target points at itself (or at a downstream op that includes it) → infinite loop the **wrong** way. Target should be the **composite** that mixes source + feedback, not the feedback's own output.
- Changing the upstream resolution without a reset — ghost pixels from the old size stay visible.
- Running a heavy shader inside the feedback loop at 4K — cost compounds. Prototype at 512x512.
- Forgetting that Feedback TOP is always 1 frame behind. For multi-frame delay, chain multiple Feedback TOPs or use a Cache TOP with explicit index math.
