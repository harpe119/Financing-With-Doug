# Doug cover: fresh portrait from the photograph

- Active asset: `cover-doug-v6.png` (1086 × 1448 pixels, RGB PNG).
- SHA-256: `0653567ba9640d5414636af7f5ae7e7deb3daa41fc0a37cf7645cf1f77e308a5`.
- Created: 2026-10-17 with the built-in `image_gen.imagegen` generation tool.
- Generation identifier: `01a14a0a-16ed-7242-bd17-4148206a5e41/exec-62803896-ed02-47fb-b98b-af7d3f7d8818.png`.
- Visual reference: the user-supplied photograph with large brown spectacles,
  magnified eyes, a pursed mouth, a receding hairline, and a green background.
- Use: introduced in manual revision 1.6; retained in revision 1.7, placed by `chapters/00_frontmatter.tex`.

The user explicitly requested a fresh start using the photograph and withdrew
the textbook-style art direction. This is a new full-color, human-looking
caricature that takes the photograph's appearance and expression as its basis.
It replaces the earlier monitor-headed robot, technical drawings, curly hair,
and graphite treatment. The reference's caption is not reproduced.

The tool's recent-image window included the supplied photograph and previous
conversation images. The prompt explicitly designated the photograph as the
only visual reference and instructed the tool to disregard the old illustrations
and covers. No previous generated file was specified as an edit target.

The generated PNG is copied without pixel edits. Revision 1.6 placed editable
sans-serif typography beside the portrait and author metadata in a separate
footer. Revision 1.7 centers the title above the portrait and places the author,
revision, and date below. Its small italic caption reads “Doug's self-portrait.
Accuracy not independently verified.” The caption is a joke about Doug's idea of
his own appearance. It is editable LaTeX text, not part of the generated image.

The user's reference photograph itself is not included in the manual or
repository. Earlier concepts and generation records are retained as history;
they are not the basis of this cover.

## Exact generation prompt

```text
Use case: illustration-story.
Create an entirely NEW character illustration from scratch. The ONLY visual reference to use is the user-supplied PHOTOGRAPH: the close-up of a man with huge round brown glasses, very wide magnified eyes, a receding hairline, pursed lips, and a green background, captioned "NERD!". Ignore all of the illustrated robots, diagrams and previous book-cover images supplied in the conversation; they are discarded concepts, not references.

Doug is a homemade AI with an extremely nerdy, unintentionally funny personality, a running family joke. Personify him here as a HUMAN-LOOKING comic character inspired directly by the photograph. Give him a broad, slightly awkward face, a prominent rounded nose, short thinning brown hair with a high receding hairline, raised brows, enormously thick ROUND brown spectacles, and eyes comically magnified by the heavy curved lenses. Capture the photograph's very particular expression: wide-eyed, pursed lips, raised chin, solemnly self-important and a little bewildered. The humor should be affectionate and immediately recognizable from the reference. Do not give him a robot or computer-monitor head, curly hair, an open grin, or the earlier cartoon's proportions.

Style: richly colored, playful, beautifully rendered 3D caricature with tactile materials, soft cinematic light, subtle skin detail and an expressive sculpted face. Warm, inviting, funny, and suitable for a family's personal-finance program. Nothing academic: no graphite, crosshatching, engineering sketches, plotted graphs, grids, parchment, or textbook treatment.

Scene: a simple personal-finance desk. Doug wears a slightly unfashionable pale blue short-sleeve button-down shirt with a small pocket protector and several pens. He pauses above a basic calculator and a household ledger, glancing upward with that absurdly serious nerd expression. Keep props minimal so the face and GLASSES dominate. Thick lens edges, magnification, a little optical distortion, and chunky translucent brown frames should be unmistakable. No need for teeth to show; the pursed-mouth reference takes priority.

Composition: portrait 3:4, high quality, cover-ready illustration, waist/chest-up character occupying the middle and lower portion. Soft muted sage-green backdrop recalling the reference photo, with warm light on Doug. Keep the TOP QUARTER clean and quiet green for separately typeset cover title, and a small quiet area near the bottom for author information. Fit the whole head and glasses inside the image with comfortable margins. Use realistic lighting but an unmistakably illustrated, exaggerated comic character.

Output just the artwork. Do not reproduce the photograph's "NERD!" caption. No words, captions, title, lettering, numerals, watermarks, signatures, logos, extra people, rocket/toaster/dumbbell motifs, book borders, or book mockup.
```

## Rebuild

Run `make pdf check` from `docs/manual`, then inspect the cover at readable
scale. The calculation examples and their inspected application baseline remain
the recorded evidence; rebuilding the cover does not regenerate them.

