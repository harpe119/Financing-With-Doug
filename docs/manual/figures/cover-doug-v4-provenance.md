# Doug cover: awkward and deadpan

- Asset: `cover-doug-v4.png` (1448 × 1086 pixels, RGB PNG).
- SHA-256: `a2e2e621568c510d7aac3dc794f14d6827228b9e118f8f49862a73455713617d`.
- Created: 2026-10-17 with the built-in `image_gen.imagegen` editing tool.
- Generation identifier: `01a14a0a-16ed-7242-bd17-4148206a5e41/exec-003c5aff-0cdc-4116-bea6-4b5f2c807c93.png`.
- Input: `cover-doug-v3.png`, SHA-256 `4ba3c4499ebe62f2b99c1fc399c88739ea2e0390a21f3c24bf8b1dbfb3ef0292`.
- Use: manual revision 1.4, placed by `chapters/00_frontmatter.tex`.

The user suggested an appearance a bit like Napoleon Dynamite. This edit applies
that fictional character's awkward, deadpan nerdiness to Doug: messy sandy curls,
a longer face, lankier mechanical proportions, oversized glasses, a slightly
open mouth with visible buck teeth, and a pen-filled shirt pocket. The previous
bow tie is removed. Doug retains his computer casing and articulated hands.

The technical illustration style, warm paper, muted teal, historical sketches,
and separate serif cover typography are retained. The historical references for
Doug's earlier roles are recorded in
[the original cover record](cover-doug-provenance.md). The curls and appearance
are creative choices for this cover, not a claim about the original AI.

The image was edited with the built-in tool and copied without pixel edits.
The [preceding version](cover-doug-v3-provenance.md), its artwork, and earlier
variants remain available.

## Exact editing prompt

```text
Use case: precise-object-edit.
Edit target: the supplied graphite-and-teal illustration of the robot Doug at his financial ledger.
User direction: Doug should look a bit like the fictional character Napoleon Dynamite. Rework Doug's proportions, hair, expression, and posture to capture that unmistakably awkward, deadpan, extremely nerdy comic energy, adapted to Doug as an original homemade AI.

Character changes:
- Make Doug lankier, with a longer, narrower vintage computer face, a longer slim mechanical neck, narrow shoulders, and an awkward slightly slouched posture. Avoid the current squat, cheerful mascot proportions.
- Give him a conspicuous mop of messy sandy-brown tight curls, like an earnest but ill-fitting curly wig perched on the monitor casing. This is a key visual cue. Render the curls in the same fine graphite and restrained color-wash style as the rest of the drawing.
- Keep very large, thick spectacles, with a slightly squared aviator-like silhouette. They should dominate his long face and look a little too big.
- Replace the wide delighted grin with a wonderfully deadpan, mildly bewildered, slack-mouthed expression: half-lidded eyes looking slightly to the side, an awkward small open mouth, and the two prominent buck teeth still visible. Affectionately dorky and unintentionally funny.
- Keep a loose plain short-sleeved collared shirt with a stuffed pocket protector and several pens. Remove the bow tie; slightly ill-fitting everyday nerd clothing suits this direction better.
- Preserve enough of the off-white computer casing and articulated metal hands/neck that he is clearly Doug the homemade AI. The humor is in his gawky appearance and serious attitude toward his calculations.

Preserve the professional textbook plate treatment: careful graphite/ink linework and crosshatching, a limited ivory/charcoal/muted-teal palette, warm paper, the spare ledger and calculator, technical forecast graphs, and the small rocket, toaster and dumbbell studies in the margins. Keep the landscape 4:3 composition. Fit his curls and full head comfortably inside the frame. Maintain the hand-with-pencil and calculator interaction. Modify the character, not the overall graphic layout.

No title, words, slogans, digits, labels, logos, signature, watermark, movie-poster elements, extra characters, or new clutter. Output the revised illustration only; cover typography is separate.
```

## Rebuild

Run `make pdf check` from `docs/manual` and inspect the first PDF page.
The calculation examples and their recorded application baseline are retained;
this cover edit does not imply a fresh run of those calculations.

