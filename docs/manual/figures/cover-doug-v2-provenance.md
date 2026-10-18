# Doug cover: technical illustration

- Asset: `cover-doug-v2.png` (1448 × 1086 pixels, RGB PNG).
- SHA-256: `8e359818da81be33783080d1570617ac44be8af2d867433961fcd3afcbdb0bff`.
- Created: 2026-10-17 with the built-in `image_gen.imagegen` editing tool.
- Generation identifier: `01a14a0a-16ed-7242-bd17-4148206a5e41/exec-ee6898cb-f065-49fa-a92a-ed1b8d655fe8.png`.
- Input: `cover-doug.png`, SHA-256 `23b675a2258a0fddf5b6a3765ecc30966682fe99611263bafb874618344387f0`.
- Use: manual revision 1.2, placed by `chapters/00_frontmatter.tex`.

The user requested a more professional textbook treatment. The revised artwork
uses technical linework, a limited graphite-and-teal palette, a quieter expression,
and a simpler composition. Thick glasses retain Doug's identity. Small rocket,
toaster, and dumbbell studies refer to his recorded earlier roles. The history
sources and original interpretation are documented in
[the first cover record](cover-doug-provenance.md).

The generated PNG is retained without pixel edits. The cover uses separate,
editable LaTeX typography: a serif title, a warm paper background, a fine rule,
and a contained illustration. The original artwork remains available alongside
this version. Neither image is an archival depiction of the original AI, and
the illustrated curves are decorative rather than calculated evidence.

## Exact editing prompt

```text
Use case: style-transfer.
Edit target: the supplied Doug cover illustration.
Request: revise this illustration into a more professional academic textbook plate. Preserve the idea of Doug as a homemade retro computer AI with very thick black glasses, working on personal-finance calculations, but substantially reduce the cute cartoon treatment and visual clutter.

Style: precise graphite and ink technical illustration with very restrained teal washes on clean warm-white paper, fine crosshatching, clear material and mechanical construction. Think a carefully commissioned plate in a university textbook on computation and applied modeling. Flat editorial drawing with controlled detail, not a cinematic scene, not a toy-like 3D rendering. Limited palette of charcoal, graphite gray, muted teal, and ivory.

Doug: retain the recognizable off-white vintage monitor-shaped head and thick black horn-rim spectacles. Give him smaller, understated display-like eyes instead of enormous human cartoon eyes. A calm, thoughtful expression and only the faintest hint of a smile; remove the expressive cartoon eyebrows. Keep a quiet, endearing nerdiness in the glasses and slight head tilt. Head and mechanical shoulders/hands should read as a thoughtfully illustrated machine, not a baby robot.

Composition: landscape 4:3, isolated editorial composition centered within generous open paper margins. Doug at a spare drafting desk with a ledger, a small calculator, and one pencil. Behind and around him, a light technical grid, a restrained forecast fan and a few careful chart lines should provide a geometric setting. Remove the plants, warm window vignette, brass lamp, tall book stacks, piggy bank, coin stacks, and foreground paper clutter. Recall the historical rocket-calculation, toaster, and fitness-coach roles only through three small, faint technical line studies at the margins: a simple rocket, a toaster profile, and a dumbbell. These must be secondary, like quiet archival sketches. Keep the overall image calm, balanced, scholarly, and suitable for an adult reference manual.

Output only the revised illustration, edge-to-edge paper background, high resolution. No cover typography, words, digits, labels, logos, signatures, or watermark. Book typography is added separately in LaTeX. Keep thick spectacles unmistakable.
```

## Review and rebuild

Run `make pdf check` from `docs/manual`, then inspect the first PDF page.
The application examples and their recorded source baseline are retained;
a cover revision does not imply that those calculations have been rerun.

