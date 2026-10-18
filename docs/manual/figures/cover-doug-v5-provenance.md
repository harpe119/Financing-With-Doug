# Doug cover: comically thick spectacles

- Asset: `cover-doug-v5.png` (1448 × 1086 pixels, RGB PNG).
- SHA-256: `1c1b9ad2de96a75ad9b6b9524a2be7c2edee32578572d7954693e2dbdb0265e1`.
- Created: 2026-10-17 with the built-in `image_gen.imagegen` editing tool.
- Generation identifier: `01a14a0a-16ed-7242-bd17-4148206a5e41/exec-e81a1169-6710-4216-ae1e-0a41b8b2e847.png`.
- Edit input: `cover-doug-v4.png`, SHA-256 `a2e2e621568c510d7aac3dc794f14d6827228b9e118f8f49862a73455713617d`.
- Supporting input: the user-supplied photograph with large round glasses and a
  caption, supplied in the conversation. It was used as an appearance reference;
  the photo and caption are not included in the manual.
- Use: manual revision 1.5, placed by `chapters/00_frontmatter.tex`.

The user requested comically thick glasses and supplied a reference for Doug's
appearance. This edit changes the spectacles to large rounded frames with deep,
bulging optical lenses, visible edge rings, magnified eyes, and a more wide-eyed
expression. Curls, buck teeth, the pen-filled shirt pocket, and the technical
illustration setting remain.

The tool received the two most recent conversation images: the supplied
photograph as a supporting reference and the inspected v4 illustration as the
edit target. No identity was inferred from the photograph. The generated PNG is
retained without pixel edits. The
[previous artwork and prompt](cover-doug-v4-provenance.md) remain available.
Doug's recorded history references are in
[the original cover record](cover-doug-provenance.md).

## Exact editing prompt

```text
Use case: precise-object-edit.
Inputs: the photograph with the large round spectacles and the caption "NERD!" is a reference for glasses shape, magnified eyes and comic expression. The graphite-and-teal illustration of curly-haired robot Doug at his ledger is the EDIT TARGET. Edit the Doug illustration, not the photograph.

Primary change: make Doug's glasses COMICALLY THICK. Use the reference photo's enormous rounded spectacles and wide-eyed, slightly startled nerd expression. Give Doug oversized round-to-oval dark tortoiseshell/near-black frames with absurdly thick, three-dimensional rims and especially thick optical lenses. Each lens should look like the bottom of a heavy glass bottle: chunky, bulging, with several visible concentric edge rings and obvious depth projecting out from the face. Make the glass thickness immediately readable at book-cover size, not just a subtle outline. The lenses should dramatically magnify his eyes behind them, with humorous optical distortion at the edges and soft restrained reflections. His magnified eyes and raised brows should convey the reference's solemn, wide-eyed, slightly bewildered look.

Keep Doug an original homemade AI: vintage off-white monitor casing, messy sandy curls, lanky mechanical neck and limbs, collared shirt with a pen-filled pocket protector, and the two recognizable buck teeth. Adjust the small mouth toward a stiff, awkward almost-pursed expression, leaving the buck teeth visible. The humor should be affectionate and very nerdy. Preserve the shirt, working pose, hands, pencil, ledger and calculator.

Maintain the professional textbook illustration treatment exactly: graphite-and-ink crosshatching, warm-white paper, muted teal graphs, technical grids, small rocket/toaster/dumbbell studies, landscape 4:3 composition. Keep surrounding layout and objects unchanged. Preserve image margins and fit all hair and eyewear inside the frame.

Do not import the photo background, skin texture, photographic style, or the caption. No words, letters, numerals, labels, watermarks, signatures, logos or extra objects. Output only the edited textbook illustration. Cover typography is separate.
```

## Rebuild

Run `make pdf check` from `docs/manual` and inspect the cover at readable scale.
Cover typography remains separate LaTeX text. Calculation examples and their
recorded application baseline are retained rather than regenerated for an
artwork revision.

