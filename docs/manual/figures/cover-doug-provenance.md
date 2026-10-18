# Doug cover illustration

- Asset: `cover-doug.png` (1448 × 1086 pixels, RGB PNG).
- SHA-256: `23b675a2258a0fddf5b6a3765ecc30966682fe99611263bafb874618344387f0`.
- Created: 2026-10-17 using the built-in `image_gen.imagegen` tool.
- Original generation identifier: `01a14a0a-16ed-7242-bd17-4148206a5e41/exec-24547e4a-77e0-45c3-8aff-74df26b90ed1.png`.
- Use: front cover of the standalone manual, revision 1.1.
- Placement: `chapters/00_frontmatter.tex`. The original generated PNG is retained without pixel edits. The title, subtitle, author, date, and revision are separate, editable LaTeX text.

## History used for the illustration

The user described Doug as an AI they created many years ago, a running family
joke with a nerdy, silly personality and large thick glasses.

The current About dialog was inspected. Its text describes the finance tool,
while the more detailed origin story survives in Git history. In commit
`8ae705f505bdfc6827a9d6e10a532fbe92f13d61`,
`finfore/ui/main_window.py`, the About text explains that Doug began as a simple
AI on Caleb's computer, struggled with counting, was rewritten repeatedly,
learned rocket calculations, and spent a semester as a toaster and fitness
coach. The 1.2.0 entry in `CHANGELOG.md` also points to the original About story.

To inspect that historical source without changing the checkout:

```sh
git show 8ae705f505bdfc6827a9d6e10a532fbe92f13d61:finfore/ui/main_window.py
```

The robot at a household ledger is an illustrative interpretation, not an
archival depiction of the original AI. Its glasses and expression carry the
personality. A rocket sketch, toaster, and dumbbell recall the recorded earlier
roles. No date of Doug's original creation is inferred. The current application
does not need to contain this older About wording for the illustration's history
reference to remain valid.

## Exact generation prompt

```text
Use case: illustration-story.
Asset type: finished illustration for the front cover of a personal-finance textbook, not a book mockup. Generate a landscape 4:3 illustration, high resolution, composed edge to edge. No title or lettering; all book typography will be typeset separately.

Subject and history: Doug is a beloved homemade AI created years ago, a running family joke: nerdy, earnest, silly, and wearing BIG THICK BLACK GLASSES. An old About dialog says he began as a simple AI on Caleb's computer who could not count correctly, was rewritten many times, learned rocket calculations, and spent a semester as a glorified toaster and fitness coach. Express this history warmly without inventing a date or depicting a real person.

Scene: a scholarly but welcoming workbench. Make Doug an original charming retro computer-robot with a gently rounded off-white monitor head, oversized thick black horn-rim glasses worn across the screen, expressive intelligent eyes visible clearly behind the lenses, one raised eyebrow and a slightly sheepish, pleased smile. His sturdy small mechanical body is sitting at the desk, thoughtfully working through a household ledger with a pencil and a small calculator. The thick glasses and endearing personality are the main visual focus. Pose and hands should be clear and plausible.

History details should be secondary: a small old-fashioned toaster with a piece of toast off to one side of the desk, a modest hand-drawn rocket trajectory schematic on graph paper behind him, and a single small dumbbell beside a worn technical book. These three subtle details recall the old About story; they must not overwhelm the finance scene. A plotted budget/forecast curve and a few neatly organized ledger pages establish the present purpose. Use lines and simple diagram marks, no readable labels or pseudo-text. There are no other characters.

Style: sophisticated illustrated university textbook cover, original editorial gouache and fine ink technical linework, tactile matte paper grain, careful material detail, measured geometry and balanced composition. Scholarly and publishable with restrained affectionate humor. Mature educational illustration, not a children's cartoon, not a glossy generic 3D mascot. Slightly nostalgic early homemade computing atmosphere, no cyberpunk or neon.

Palette: warm ivory paper, deep charcoal and muted teal echoing the existing manual, a little muted blue and warm brass detail. Soft directional daylight, gentle shadows, crisp glasses silhouette. Character centered with comfortable space around his head and glasses; keep the toaster, rocket diagram and dumbbell inside generous crop-safe edges. Entire illustration should feel cohesive and quietly amusing.
Constraints: no words, no numerals, no labels, no signature, no watermark, no logos, no mockup, no actual book border, no dollar-sign rain, no stock-market trading imagery. The requested artwork is figurative and illustrative, not a historical photograph.
```

## Replacing the cover

Keep the artwork and book typography separate so revisions to the title or
version do not require generating another image. Retain any replacement as a
versioned sibling until it has been reviewed. Update this record with its prompt,
history references, dimensions, and hash, then rebuild with `make pdf` and run
`make check`. Inspect the first PDF page for cropping and readable type.

