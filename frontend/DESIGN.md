# GitReader design system

This describes the Wiki workspace and the app shell as built: the reading desk, docked chat, repository map and Code Inspector. Dashboard, History and Login have not been migrated yet. They should adopt this system unchanged.

## Thesis

**Every claim has its evidence pinned beside it.** GitReader reads like a scientific publication, Tufte-style: structure and provenance stay in the margin instead of hiding behind clicks. We reject the category default of a "doc tree + prose + floating chat drawer".

A reader follows one path:

1. Read a claim in the body.
2. See in the margin which file it comes from (`file:line`, chapter code).
3. Press `M` to see where those files sit in the repository.
4. Ask in the docked chat. The answer comes back with the same kind of numbered evidence, and that evidence lights up in the contents and on the map.

## Tokens (`src/app/globals.css`, Tailwind v4 `@theme`)

| Role | Token | Value | Rule |
|---|---|---|---|
| Page | `paper` | `#f8f9fb` | Cool white background |
| Surface | `sheet` | `#ffffff` | Panels, dialogs, code |
| Text | `ink` | `#16181d` | Body text and primary actions |
| Neutrals | `n-1` … `n-9` | `#f1f3f6` … `#1f242c` | Lighter means sparser. **`n-6` (`#5f6775`) is the lightest neutral allowed for text**; `n-1`–`n-5` are fills and hairlines only |
| Shell | `rail`, `rail-raised`, `rail-line`, `rail-ink`, `rail-muted` | `#23205c` … | Indigo rail, the primary action fill, focus rings |
| Evidence | `evidence`, `evidence-tint` | `#c8312b`, `#fbeceb` | **Only** "current evidence": a hovered note, cited lines, files cited in this conversation. Never used for errors or status |
| Chapters | `dir-1` … `dir-7`, `dir-other` | blue, orange, green, amber, pink, dark green, violet, grey | Data colours in a fixed order, never cycled. Chapters past D7 use `dir-other`. Each has a `-on` text colour (≥ 4.5:1) for solid fills |

- **Checked**: the chapter palette passed the dataviz `validate_palette` check against `paper`. Adjacent colour-blind ΔE is ≥ 9.1 and normal-vision ΔE is ≥ 19.6.
- **Pairing**: several chapter colours are below 3:1 on paper, so a colour never identifies a chapter on its own; it always appears next to the chapter code.
- **No gradients, no drop shadows.** Separation comes from hairlines (`border-n-2` / `n-3`) and flat fills.
- **Legacy alias**: `muted-foreground` is the only one left, used only by `Auth.tsx`. Delete it once Login is migrated.

### Type

| Use | Family | Notes |
|---|---|---|
| Reading text: wiki body, chat answers, headings | `font-serif` (Source Serif 4) | Wiki body is 17px (`readingProse`), answers 15px (`answerProse`). Both come from `src/lib/prose.ts` |
| UI: labels, buttons, questions | `font-sans` (IBM Plex Sans 400/500/600) | |
| Code, paths, chapter codes, figures | `font-mono` (IBM Plex Mono 400/500) | Figures use `tabular-nums` |

- Body text is capped at `max-w-measure` (68ch).
- The smallest text is 12px (`text-xs`). Nothing is set at 9–11px.

## Layout: the reading desk

Columns, left to right:

1. **Indigo rail** (80px).
2. **Contents**: `w-56`, `w-72` at `2xl`. It collapses to a 64px column of chapter codes.
3. **Article**: body at 68ch, plus a 14rem margin-note column when the article area is at least 64rem wide. This is a container query (`@5xl`), because both the TOC and the chat change the article's width.
4. **Docked chat**: `clamp(20rem, 28vw, 36rem)`. It collapses to a strip, and the setting is remembered per reader.

- **Scrolling**: only the article column scrolls. The wiki route is full-bleed, and `AppLayout` drops its padding there.
- **Map mode** (`?view=map`) replaces the contents and the article; chat stays.
- **Below `md`**: the rail becomes a top bar and the contents become a full-screen dialog.
- **Below `lg`**: chat becomes a full-screen panel opened from an "Ask" button.

## Signature patterns

**Chapter codes.**
- Top-level chapters are `D1…Dn`; pages inside them are `Dn.m` (`src/lib/wikiToc.ts`).
- The code is the shared name for a chapter everywhere: TOC, page header, margin notes, answer notes, map columns, and the URL (`?d=D3.2`).
- The selected TOC row is filled with its chapter colour, uses the `-on` text colour, and carries `aria-current="page"`.

**Margin notes** (`src/lib/wikiNotes.ts`, `WikiArticle`):
- **Content**: a page's files, as numbered notes.
- **Placement**: each note sits beside the block that first mentions the file; files the text never mentions sit beside the intro.
- **Inline markers**: inline code naming a cited file gets a superscript number.
- **Cross-references**: a note lists the other chapters that cite the same file.
- **Signature interaction**: hovering or focusing a note tints every TOC row that cites the file in `evidence`, and fills the file's square on the map.

**Answer evidence** (`src/lib/answerNotes.ts`, `MessageItem`):
- **Notes**: chat answers get the same treatment. Each note shows `file:lines`, the tool that found it (`read`, `search`, `graph`…), and the wiki chapters that list it.
- **Inline paths**: paths in the answer become buttons that open the Code Inspector.
- **Trace**: the tool trace is a collapsed "N tool calls" line.

**Repository map** (`WikiMap`):
- **Structure**: a small-multiples matrix. Rows are directories, with adjustable depth; columns are top-level chapters.
- **Marks**: each cell has one 8px square per file, on the same grid in every column, so cells compare directly. A square is filled with the chapter colour where that chapter cites the file.
- **Evidence**:
  - Files cited in the open conversation get an `evidence` outline.
  - A hovered file is outlined in `ink` in every column.
  - The file of a note hovered elsewhere is filled in `evidence`.
- **Labels**: everything is labelled directly in a caption line; there is no legend.

**Task states** (`TaskStatePanel`):
- Every non-ready state is stamped, never hidden: loading, generating with a progress bar, failed with the backend's error verbatim, not found, unreachable, and finished without artifacts.
- The task record from `POST /task/{id}` is the only source of truth.

## Interaction

| Key | Action |
|---|---|
| `/` | Open the chat and focus the question box |
| `M` | Toggle the repository map |
| `Esc` | Close a dialog, the contents, or the full-screen chat |
| `?` | Show the shortcut list |

- Shortcuts go through `useShortcut`. They are ignored while typing in a field, while a modal is open, and with Ctrl/Alt/Cmd held.
- All dialogs (Code Inspector, contents, enlarged diagram, shortcuts) are native `<dialog>` elements opened through `useModalDialog`. That gives each one a focus trap, `Esc`, an inert page and a backdrop click to close, and returns focus to whatever opened it.
- The Code Inspector:
  - scrolls to the cited line, tints the range, and focuses the code for keyboard scrolling;
  - on failure shows the backend's `detail` with a Retry button.
- Streaming answers can be stopped. The partial text is kept and labelled. Failed answers offer Retry.

## Accessibility

- **Navigation**: a "Skip to content" link, and a `document.title` that names the current page or map.
- **Screen readers**: `aria-live` regions say only status ("Agent is working", the hovered map file). They never read streamed text or file contents.
- **Focus**: visible focus in `rail` colour.
- **Contrast**: text uses `n-6` or darker. Placeholders are `n-6` (5.7:1 on `sheet`, 5.4:1 on `paper`).
- **Motion**: `prefers-reduced-motion` reduces every animation and transition to near zero.

## Motion

Almost none. Two things move:

- the pulsing dot while an answer streams;
- a CSS width transition on the task progress bar.

There are no entrance animations; framer-motion has been removed.

## Assets and provenance

- **Rasters**: the only raster shipped is `public/logo.png` (26 KB). It is a project asset that predates this redesign, last changed in `b115d2a` (2026-04-17). No new rasters were generated or added.
- **Fonts**: Source Serif 4, IBM Plex Sans and IBM Plex Mono load through `next/font/google` and are self-hosted at build time.
- **Diagrams**: Mermaid draws its diagrams at runtime with `securityLevel: "strict"`, themed from the tokens.

## Finish review (2026-10-03)

The review checked the build against the direction contract (`.impeccable/surfaces/src-views-wikipage-tsx.md`) and the original critique (15/40). The critique's priority issues are all fixed:

- **P0: citations** opened the wrong file (path prefix and index misalignment). Fixed.
- **P0: a missing task** crashed the page. Fixed.
- **P1: Mermaid** didn't render on first load. Fixed.
- **P1: the overlay chat drawer** covered the wiki. It is now docked.
- **P2: the trace** ("1 steps", paths you couldn't click). Fixed.

The persona red flags are fixed too:

- **No escape hatches**: there were no shortcuts and no way to stop a streaming answer.
- **Inaccessible dialogs**: no dialog semantics or `aria-current`.
- **Small, low-contrast text**: 10px text and contrast below the minimum.
- **Hidden actions**: hover-only delete buttons.

Two issues turned up during the review and were fixed in this step:

- The history delete button was invisible on touch screens.
- The Code Inspector had no way to switch files below `md`.

**Verdict: ships for the Wiki workspace.** Remaining limits, each recorded in the local `../Document/BACKEND_GAPS_FOR_FRONTEND.md`:

- **Data limits** (backend):
  - Page files carry no line numbers or per-claim provenance, so most margin notes sit beside the intro.
  - There is no repo-tree endpoint, so the map shows only files the wiki cites.
  - Some wikis have no vector index, so chat on them fails.
- **Code Inspector**: shows code in a single colour, with no syntax highlighting. This is deliberate for restraint, and it keeps line ranges exact.
- **Not yet migrated**: Dashboard, History and Login still use the old styles.
