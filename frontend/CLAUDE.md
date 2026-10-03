# CLAUDE.md — frontend

Next.js 16 + React 19 client SPA for GitReader (repo → wiki + tool-using chat). Backend contract: `../Document/API_DOCUMENTATION.md`. The repo-root `../CLAUDE.md` covers the backend.

## Commands

```bash
npm run dev        # next dev (Turbopack) on :3000
npm run build      # production build; must pass before handing work back
npm run lint       # biome check . (there is no eslint)
npm run lint:fix   # biome check --write
npx biome check <files>   # lint only what you touched (see below)
```

No test suite. Verify UI changes in a browser: run `npm run dev`, open `http://localhost:3000/app/...`, and check the console.

## Rules

- **Biome is the linter and formatter** (`biome.jsonc`): double quotes, semicolons, 80-column lines, sorted imports, React rules. Existing files still fail `npm run lint` (about 100 legacy errors). **Make every file you touch pass `npx biome check <file>`; don't clean up files you aren't changing.** Formatting a legacy file rewrites the whole thing, so leave a legacy file alone unless the task needs it. Pass `biome check --write` explicit file paths, never a directory, or it reformats untouched legacy files.
- Use the `@/` alias for `src/*`. Put UI copy in `src/lib/i18n.ts` (`t("key")`, English only). Use design tokens (below), not raw Tailwind palette colors like `stone-*` or `sky-*`.
- Don't change the backend or invent endpoints. Task state comes **only** from `POST /task/{id}`; never infer it on the client.
- Follow the `vercel-react-best-practices` skill: lazy-load heavy components, avoid request waterfalls, clean up polling and SSE on unmount, and keep streamed tokens from re-rendering the whole tree.
- **After every change, sweep for dead code and delete it**: unused exports, components, hooks, i18n keys, CSS tokens and aliases, and `public/` assets. Find them with `grep -rn "<name>" src`. Unexport anything used only inside its own file. Changes to dependencies (`package.json`) need the user's approval first.
- Don't commit. The user commits by hand. This is a solo project: suggest **one short one-line commit message** (conventional prefix, no body), and don't split work into overly fine commits.

## Architecture

- **Routing is client-side.** `src/app/[...slug]/page.tsx` and `src/app/page.tsx` dynamically import `src/router/RouterApp.tsx` (`ssr: false`), which owns the real route tree (`createBrowserRouter`). Add a page as `src/views/XPage.tsx` plus a lazy wrapper in `src/router/routes/`. `src/app/(dashboard)/…` and `(auth)/…` are legacy redirect shims; never add routes there.
- **Route tree:** `RootLayoutView` → `GlobalRouteGuard` → public (`/login`, `/auth/callback`) and `AuthGuard` → `/app` (`AppLayout`) → `dashboard`, `history` (role `user`), `wiki/:taskId`. Guards live in `src/router/guards.tsx`; auth comes from `src/providers/AuthProvider.tsx` (Supabase, `useAuth()`).
- **Shell:** `src/layouts/AppLayout.tsx` has the indigo left rail (Repos, History, account menu); below `md` it becomes a top bar. `src/layouts/ShellContext.tsx`: a workspace page calls `useRegisterShellRepo(repoUrl, href)` to show its repo in the rail.
- **Wiki page:** `src/views/WikiPage.tsx` uses `useTaskStatus` (`src/hooks/useTaskStatus.ts`), which polls while the task is `pending`/`processing` and stops on any other status, an error, or unmount. Every non-ready state renders `src/components/wiki/TaskStatePanel.tsx`. `WikiViewer` renders only for `completed`/`cached` with R2 URLs.
- **Reading desk:** `WikiViewer` fetches structure and page JSON from the R2 CDN (not the API) with `AbortController` and caches them per session; artifacts are immutable per task. The `/app/wiki/*` route is full-bleed (`AppLayout` drops its padding), and only the article column scrolls. Layout: `WikiToc` (collapsible to codes only; a full-screen dialog below `md`) | `WikiArticle` (serif, `max-w-measure`) | margin notes (a second grid column when the reading column is at least `@5xl`, i.e. 64rem, as a container query; otherwise inline after each block).
- **Chapter codes:** `src/lib/wikiToc.ts` (`parseWikiToc`) gives top-level chapters `D1…Dn` and pages `Dn.m`, and keeps each node's `files`. The current page lives in the URL as `?d=D3.2`, so links, back/forward and deep links all work. The color slot comes from the top-level chapter (`slotFill`/`slotSwatch`, slots 1–7, then `other`). Parent chapters have their own page JSON. `toc.byFile` maps a path to every page that lists it.
- **API client:** `src/lib/api.ts`. `askQuestionStream` is a hand-rolled SSE parser for `/chat/stream` (events `turn_start`, `iteration_start`, `tool_call_start`, `tool_call_result`, `answer_token`, `answer_done`, `complete`, `error`); a new stream should follow the same pattern. `normalizeRepoUrl()` must match the backend's `_normalize_repo_url`.
- **Citations:** `src/lib/citations.ts` is the only parser for chat `sources` strings (`code:/abs/…/data/repos/<repo>/x.py`, `x.py:12-40`, `web:<url>`). It normalizes paths to repo-relative form, which `POST /file/content` requires. `parseCitations()` returns de-duplicated `files` and `links`; chips and the Code Inspector must index into the same `files` array.
- **Margin notes:** `src/lib/wikiNotes.ts` (`placeNotes`) numbers a page's `files` and puts each one beside the first block that mentions its path (or a basename that is unique on the page); unmentioned files go beside the intro. Inline code naming a cited file gets a superscript number. Hovering or focusing a note marks every page that cites it in the TOC in `evidence` red; clicking opens the lazy-loaded `CodeViewer`. Only about 20% of files are mentioned in the generated text, so most notes sit beside the intro.
- **Mermaid:** `Mermaid.tsx` loads mermaid through one shared promise, renders in a single effect (so the first visit works too), uses `securityLevel: "strict"`, and builds its `base` theme from the design tokens.
- **Map view:** `?view=map`, toggled by plain `M` (ignored while typing or inside a dialog) or the map button, swaps the TOC and article for the lazy-loaded `wiki/WikiMap`; chat stays. It is a small-multiples matrix: directory rows (`src/lib/wikiMap.ts`, depth adjustable) × top-level chapter columns. Each cell holds one square per file on the same grid in every column, filled with the chapter's `slotSvgFill` when the chapter cites the file. Files cited in the open conversation (`ChatInterface` reports them through `onCitedFiles`) get an evidence outline; a hovered note's file is filled in evidence red. Following any chapter-code link drops `view` (see `useCodeHref`). There is no repo-tree endpoint, so the map only covers files the wiki cites.
- **Code Inspector:** `CodeViewer` is a native `<dialog>` opened with `showModal()` in a layout effect (focus trap, Esc, inert page; focus is handed back to the opener by hand). It shows monochrome code with line numbers, tints the cited range in `evidence-tint` (unless it covers most of the file), scrolls it into view, and focuses the code. File text is cached per repo and path, requests are aborted on switch, and failures show the backend's `detail` with Retry. Callers pass the same `files` array their notes index into.
- **Docked chat:** `ChatInterface` is the right column of the reading desk at `lg` and up (collapsible to a strip; the preference is in localStorage), and a full-screen panel below `lg`. `WikiViewer` passes the current page (`page`: code, title, first file) for the empty-state example questions, plus `currentPageContext`, and keys the panel by `repoUrl`. History loads the first time it's opened. Messages sit in a `flex-col-reverse` scroller, which keeps the newest message in view without scroll effects. `MessageItem` renders a question (indigo rule) or an answer (serif `answerProse`, shared with the wiki through `src/lib/prose.ts`).
- **Answer evidence:** `src/lib/answerNotes.ts` (`buildAnswerNotes`) turns `sources` plus file paths in the answer's inline code into numbered notes: `file:lines`, the tool that found the file (from the trajectory's `file_path` arguments or a `code:`/`text:` prefix), and the wiki chapters that list the file (`filePages` = `toc.byFile`). Inline paths become buttons with superscript note numbers. Notes and inline paths open the lazy-loaded `CodeViewer` on the same `files` array. Hovering a note highlights the TOC through `onEvidence`, just like page notes. The trajectory is a collapsed "N tool calls" trace. ```mermaid blocks render with `Mermaid`. While a turn streams, `LiveStepFlow` shows the tool steps and the raw streamed text.
- **Streaming:** `useChatStream` owns an `AbortController`. `stop()` (and unmounting) aborts, and `sendMessage` then rejects with `ChatStreamStopped`, carrying the partial answer, which is kept with a "stopped" label. Errors show a Retry button that re-asks the last question. `api.askQuestionStream` and `api.getTaskStatus` take an `AbortSignal`.

## Design system (`src/app/globals.css`)

Tufte-style scientific publishing: cool paper, ink text, a deep-indigo rail, flat fills, hairlines, **no gradients**.

- **Surfaces and text:** `paper`, `sheet`, `ink`. **Neutral ramp:** `n-1`…`n-9` (`n-6` is the lightest neutral allowed for body text; `n-5` and lighter are for lines only). **Rail:** `rail`, `rail-raised`, `rail-line`, `rail-ink`, `rail-muted`.
- **`evidence` (red) marks "current evidence" only.** Don't use it for errors or status.
- **Directory colors** `dir-1`…`dir-7` plus `dir-other`: a fixed order, never cycled, with a matching `-on` text color for solid fills. Several are below 3:1 contrast on paper, so always pair a color with its directory code.
- **Fonts:** `font-serif` (Source Serif 4) for reading text, `font-sans` (IBM Plex Sans) for UI, `font-mono` (IBM Plex Mono) for code and `tabular-nums` figures. `max-w-measure` = 68ch.
- `muted-foreground` is the only legacy alias left, and only the login component `Auth.tsx` uses it. Don't use it in new code, and delete it once Auth is migrated.

## Gotchas

- **The API base URL comes from env.** `api.ts` reads `NEXT_PUBLIC_API_URL`, and `.env.local` points it at the deployed backend (`//fan-tianrui-fyp.fly.dev`), so local dev hits production data unless you change it. Fallback: `http://localhost:8000`.
- `wiki_route_tracking` in localStorage drives the "restore last page" redirect on `/`. `/` itself is never recorded (recording it caused a blank-page redirect loop).
- `next build` rewrites `next-env.d.ts`; don't commit that change.
- Some wikis have no vector index, so chat on them fails (e.g. hugo). Use maya (`/app/wiki/81b81b59-90ed-41b2-8669-8a31829e241e`) to test chat against production. Known backend gaps are listed in `../Document/BACKEND_GAPS_FOR_FRONTEND.md`.

## Redesign in progress

The Wiki workspace and shell are being rebuilt in 12 reviewable steps. The plan and progress log are in `../Document/FRONTEND_REDESIGN_PLAN.md`. The design direction and critique are in `.impeccable/surfaces/` and `.impeccable/critique/`, with product context in `PRODUCT.md`. All of these are local and gitignored. Done so far: tokens and fonts, the shell rail, the task-status guard, citation parsing, the reading desk with chapter codes, margin notes with Mermaid, and the docked, abortable chat, answer evidence notes, the map view, and the Code Inspector dialog. Update this file at the end of every step.
