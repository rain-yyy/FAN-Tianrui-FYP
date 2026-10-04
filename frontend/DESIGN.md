# GitReader design system

Dark-only, warm and quiet: a charcoal canvas, rounded panels with hairline borders, and one ember-orange accent. The layout (top bar, three-column reading desk, centred home hero, pill search) follows the reference screenshots in `example/`; the palette and brand are GitReader's own.

## Principles

- Content sits on `panel` cards over the `bg` canvas. Depth comes from lightness steps and 1px borders, never drop shadows.
- Orange means "you are here" or "do this": the current TOC row, links, evidence, primary buttons. Everything else is neutral.
- Every claim keeps its evidence: numbered `file:lines` notes beside wiki text and under chat answers.
- The only glow is the faint orange wash behind the home hero, the login page and the search field.

## Tokens (`src/app/globals.css`)

| Group | Token | Value | Use |
|---|---|---|---|
| Surface | `bg` | `#0e0d0c` | canvas |
| | `panel` | `#171615` | cards, menus |
| | `raised` | `#201e1c` | diagram frame, code, inputs |
| | `overlay` | `#272523` | dialogs, help |
| | `line` / `line-strong` | `#302d2a` / `#443f3a` | borders / hover, quote rules |
| Text | `fg` | `#f1eee9` | body (15.6:1 on `panel`) |
| | `fg-muted` | `#aaa49b` | secondary (7.3:1) |
| | `fg-faint` | `#8a847b` | codes, meta only (4.9:1) |
| Accent | `accent` | `#ff9d4d` | text, links, current item (8.8:1) |
| | `accent-strong` | `#f38020` | solid fills |
| | `accent-fg` | `#1a1006` | text on a fill (7.1:1; white is 2.65:1) |
| | `accent-soft` / `accent-line` | `#ff9d4d` at 13% / 45% | selected rows, notes, outlines |
| Status | `ok` / `warn` / `danger` / `info` | `#6fd39a` / `#f2d155` / `#ff7a85` / `#7cb7ff` | completed / cancelled / failed / pending |

`danger` is the only error colour. `processing` uses the accent. A status colour never stands alone: it always comes with its word.

- **Fonts:** IBM Plex Sans (UI and reading text), JetBrains Mono (code, chapter codes, `tabular-nums` figures). Wiki body text fills the article card (no measure cap) with a 1.7 line height; nothing is smaller than 12px.
- **Width:** the top bar and the reading desk are centred at `max-w-desk` (125rem, 2000px), like codewiki.google. Inside it the TOC, article card and chat share the width 1 : 3 : 2 (flex ratios; TOC at least 13rem, chat at least 22rem, 30rem from `xl`); the article body is capped at 75rem.
- **Radii:** panels `rounded-2xl`, rows and cards `rounded-xl`, buttons, inputs and chips `rounded-full`.
- **Motion:** colour and border transitions only, 150–200ms. Nothing animates size or position.

## Layout

- **Top bar** (all `/app` pages): GitReader mark, a pill showing the current repo (or "Find or add a repository") that links home, History, "Copy link" and Chat (wiki only; Chat toggles the chat card), `?` help, account menu.
- **Reading desk** (`/app/wiki/:taskId`): three columns that scroll independently. TOC list | article card (margin notes become a second column at `@5xl`) | chat card, collapsible to a strip. Below `md` the TOC is a full-screen dialog; below `lg` the chat is full screen.
- **Home:** centred wordmark, subtitle, pill search field with an orange wash, then the user's repos as rows (or four feature cards when there are none). Typing in the field drops down matching wikis as rows (owner avatar, name over owner, description, stars); a GitHub link with no wiki shows a "Generate a wiki for owner/name" row.
- **History:** rows with the repo URL, date, a status pill and a delete button.
- **Login:** centred, outside the shell, with the same wash along the bottom edge.

## Patterns

- **Chapter codes:** `D1`, `D1.2` are small mono labels in the TOC, notes and chips, and the page lives in the URL as `?d=`. Chapters are not colour-coded.
- **Margin notes and answer evidence:** numbered `file:lines` notes, with the tool that found the file and the wiki chapters that list it. Hovering one marks those chapters in the TOC (`accent-soft` row with an accent rule). Clicking opens the Code Inspector.
- **Task states:** every state is shown, never hidden, as a centred card with a coloured status pill, a progress bar when the backend reports one, and actions. `POST /task/{id}` is the source of truth.
- **Errors** are inline text or a card with a `danger` border, never `alert()`.

## Interaction

- **Shortcuts** (via `useShortcut`): `/` focuses the chat, `?` opens help, `Esc` closes the topmost dialog or the full-screen chat.
- **Dialogs** are native `<dialog>` elements (via `useModalDialog`).

## Known limits

These are backend-bound and listed in `../Document/BACKEND_GAPS_FOR_FRONTEND.md`:

- Page files have no line numbers, so margin notes cannot point at a line.
- Repo rows on the home page show no description or stars, because the dashboard endpoint returns none.
- There is no repo search endpoint, so home search only covers wikis that already exist and the top-bar pill only links home.
- No commit SHA is returned, so the TOC has no "Updated on / Commit" footer.
- Some repos have no chat index.
