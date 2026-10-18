# Doug cover: the family version

- Asset: `cover-doug-v3.png` (1448 × 1086 pixels, RGB PNG).
- SHA-256: `4ba3c4499ebe62f2b99c1fc399c88739ea2e0390a21f3c24bf8b1dbfb3ef0292`.
- Created: 2026-10-17 with the built-in `image_gen.imagegen` editing tool.
- Generation identifier: `01a14a0a-16ed-7242-bd17-4148206a5e41/exec-4b33b988-a6fa-4abb-864b-a9e9e64c981e.png`.
- Input: `cover-doug-v2.png`, SHA-256 `8e359818da81be33783080d1570617ac44be8af2d867433961fcd3afcbdb0bff`.
- Use: manual revision 1.3, placed by `chapters/00_frontmatter.tex`.

The user requested a sillier, very nerdy Doug with buck teeth and pens in his
shirt pockets so the cover could give the family a laugh. The edit adds a
buck-toothed grin, a collared shirt with a pen-filled pocket protector, a bow tie,
and a more eager expression. The graphite-and-teal drawing, historical sketches,
and textbook typography remain.

Doug's original history references are recorded in
[the first cover record](cover-doug-provenance.md). The preceding technical
illustration and its prompt are retained in
[the second cover record](cover-doug-v2-provenance.md). The clothing and expression
are illustrative choices requested for this cover, not historical evidence
about the original software.

The generated PNG is retained without pixel edits. Earlier artwork remains
available. Cover typography is separately editable LaTeX text.

## Exact editing prompt

```text
Use case: precise-object-edit.
Edit target: the supplied graphite-and-teal textbook illustration of Doug.
Make Doug visibly sillier and very nerdy so the creator's family gets a laugh. Change his expression and clothing while preserving the professional technical illustration style and the surrounding composition.

Face: keep the same vintage monitor head and BIG THICK BLACK GLASSES. Give him a delighted, slightly awkward open grin with TWO unmistakable oversized rectangular upper front BUCK TEETH, a tiny gap between them, protruding comically. Draw the teeth as a playful part of his screen-face, not realistic human dentistry or fangs. Retain simple display-like eyes, with a mildly goofy eager expression and a slightly raised brow. A little head tilt and glasses sitting just a touch crooked should add affectionate nerdiness. Make the two front teeth clearly readable at book-cover size.

Clothing: put a tidy but endearingly nerdy short-sleeve collared shirt over his mechanical torso. Add a very visible breast pocket with a pocket protector stuffed with several pens and a mechanical pencil of different heights. Add a slightly crooked small bow tie. Shirt should be light ivory with fine graphite construction lines so it belongs to the existing restrained palette. Keep his articulated robot forearms and hands, pencil, ledger, calculator and working pose.

Invariants: preserve the landscape 4:3 composition, warm-white paper, graphite/ink crosshatching, restrained teal accents, technical grids and financial charts, and the small rocket, toaster and dumbbell line studies in the margins. Keep the background, ledger and calculator substantially unchanged. The character should be affectionately goofy and proud of his calculations; the humor comes from the buck-toothed grin, thick spectacles, pocket full of pens and crooked bow tie. Keep the illustration polished enough for a textbook cover.
No words, digits, labels, logos, watermark, added characters, or additional clutter. Output only the revised illustration; cover typography is handled separately.
```

## Rebuild

Run `make pdf check` from `docs/manual`, then inspect the first PDF page.
This cover edit retains the calculation examples and their recorded application
baseline; it does not imply that those examples have been rerun.

