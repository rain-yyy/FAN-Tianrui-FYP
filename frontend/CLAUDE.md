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
- **Shell:** `src/layouts/AppLayout.tsx` is a full-width top bar over the page: GitReader mark, a pill that shows the current repo (or "Find or add a repository") and links to the home page, History, "Copy link" and a Chat button (wiki pages only), the `?` help button and the account menu. It also owns the `?` shortcut and `ShortcutHelp`. `src/layouts/ShellContext.tsx`: a workspace page calls `useRegisterShellRepo(repoUrl, href)` to show its repo in the pill, and `ChatInterface` calls `useRegisterShellChat(open, setOpen)` so the top bar's Chat button can open and close it.
- **Home, history, login:** `DashboardPage` is a centred hero (wordmark, pill URL field, orange wash) that creates a task with `api.createTask`, restores a pending task from localStorage (`wiki_gen_task_id`) and polls it, showing progress through `TaskStatePanel`. `RepoGrid` lists the user's repos as rows (`api.getDashboardRepos`) or, with none, four decorative feature cards. `HistoryPage` lists tasks as rows with a `StatusStamp`. `LoginPage` is outside the shell and has its own glow. Errors are shown inline, never with `alert()`.
- **Wiki page:** `src/views/WikiPage.tsx` uses `useTaskStatus` (`src/hooks/useTaskStatus.ts`), which polls while the task is `pending`/`processing` and stops on any other status, an error, or unmount. Every non-ready state renders `src/components/wiki/TaskStatePanel.tsx`. `WikiViewer` renders only for `completed`/`cached` with R2 URLs.
- **Reading desk:** `WikiViewer` fetches structure and page JSON from the R2 CDN (not the API) with `AbortController` and caches them per session; artifacts are immutable per task. The `/app/wiki/*` route is full-bleed (`AppLayout` drops its padding) and `WikiViewer` supplies its own gutters. Three columns, each scrolling on its own: `WikiToc` (a plain "on this page" list, collapsible to codes only; a full-screen dialog below `md`) | `WikiArticle` (a rounded `panel` card, `max-w-measure`) | docked chat (a rounded card). Margin notes are a second grid column inside the article card when it is at least `@5xl` (64rem, container query); otherwise inline after each block.
- **Chapter codes:** `src/lib/wikiToc.ts` (`parseWikiToc`) gives top-level chapters `D1…Dn` and pages `Dn.m`, and keeps each node's `files`. The current page lives in the URL as `?d=D3.2`, so links, back/forward and deep links all work. Codes show as small `font-mono` `fg-faint` labels in the TOC, notes and chips; chapters are not colour-coded. Parent chapters have their own page JSON. `toc.byFile` maps a path to every page that lists it.
- **API client:** `src/lib/api.ts`. `askQuestionStream` is a hand-rolled SSE parser for `/chat/stream` (events `turn_start`, `iteration_start`, `tool_call_start`, `tool_call_result`, `answer_token`, `answer_done`, `complete`, `error`); a new stream should follow the same pattern. `normalizeRepoUrl()` must match the backend's `_normalize_repo_url`.
- **Citations:** `src/lib/citations.ts` is the only parser for chat `sources` strings (`code:/abs/…/data/repos/<repo>/x.py`, `x.py:12-40`, `web:<url>`). It normalizes paths to repo-relative form, which `POST /file/content` requires. `parseCitations()` returns de-duplicated `files` and `links`; chips and the Code Inspector must index into the same `files` array.
- **Margin notes:** `src/lib/wikiNotes.ts` (`placeNotes`) numbers a page's `files` and puts each one beside the first block that mentions its path (or a basename that is unique on the page); unmentioned files go beside the intro. Inline code naming a cited file gets a superscript number. Hovering or focusing a note marks every page that cites it in the TOC (`accent-soft` row with an accent rule); clicking opens the lazy-loaded `CodeViewer`. Only about 20% of files are mentioned in the generated text, so most notes sit beside the intro.
- **Mermaid:** `Mermaid.tsx` loads mermaid through one shared promise, renders in a single effect (so the first visit works too), uses `securityLevel: "strict"`, and builds its `base` theme from the design tokens.
- **Dialogs and shortcuts:** every modal (Code Inspector, mobile contents, enlarged diagram, `ShortcutHelp`) is a native `<dialog>` driven by `src/hooks/useModalDialog.ts`: `showModal()` in a layout effect (so children can measure it), Esc through `cancel`, backdrop click, and focus handed back to the opener by hand. Page shortcuts go through `src/hooks/useShortcut.ts`, which ignores keys typed into fields, keys pressed while a modal is open, and modifier combinations: `/` chat, `?` help (registered in `AppLayout`); Esc also closes the full-screen chat below `lg`.
- **Code Inspector:** `CodeViewer` (a dialog as above) It shows monochrome code with line numbers, tints the cited range in `accent-soft` (unless it covers most of the file), scrolls it into view, and focuses the code. File text is cached per repo and path, requests are aborted on switch, and failures show the backend's `detail` with Retry. Callers pass the same `files` array their notes index into.
- **Docked chat:** `ChatInterface` is the right-hand card of the reading desk at `lg` and up (collapsible to a strip; the preference is in localStorage), and a full-screen panel below `lg`. `WikiViewer` passes the current page (`page`: code, title, first file) for the empty-state example questions, plus `currentPageContext`, and keys the panel by `repoUrl`. History loads the first time it's opened. Messages sit in a `flex-col-reverse` scroller, which keeps the newest message in view without scroll effects. `MessageItem` renders a question (accent rule) or an answer (`answerProse`, shared with the wiki through `src/lib/prose.ts`). The composer is a pill with a round send/stop button.
- **Answer evidence:** `src/lib/answerNotes.ts` (`buildAnswerNotes`) turns `sources` plus file paths in the answer's inline code into numbered notes: `file:lines`, the tool that found the file (from the trajectory's `file_path` arguments or a `code:`/`text:` prefix), and the wiki chapters that list the file (`filePages` = `toc.byFile`). Inline paths become buttons with superscript note numbers. Notes and inline paths open the lazy-loaded `CodeViewer` on the same `files` array. Hovering a note highlights the TOC through `onEvidence`, just like page notes. The trajectory is a collapsed "N tool calls" trace. ```mermaid blocks render with `Mermaid`. While a turn streams, `LiveStepFlow` shows the tool steps and the raw streamed text.
- **Streaming:** `useChatStream` owns an `AbortController`. `stop()` (and unmounting) aborts, and `sendMessage` then rejects with `ChatStreamStopped`, carrying the partial answer, which is kept with a "stopped" label. Errors show a Retry button that re-asks the last question. `api.askQuestionStream` and `api.getTaskStatus` take an `AbortSignal`.

## Design system (`src/app/globals.css`)

Dark only: a warm charcoal canvas, rounded panels with hairline borders, one ember-orange accent. Flat fills and no drop shadows; the only glow is the faint orange wash behind the home hero, the login page and the search field. `DESIGN.md` has the full token table and patterns.

- **Surfaces:** `bg` (canvas), `panel` (cards), `raised` (diagram, code, inputs), `overlay` (dialogs and help), `line` / `line-strong` (borders).
- **Text:** `fg`, `fg-muted`, `fg-faint` (codes and meta only, 4.9:1).
- **Accent:** `accent` (links, current item, evidence), `accent-strong` (solid fills) with `accent-fg` text on top (white fails contrast on orange), `accent-soft` / `accent-line` (selected rows, notes, outlines).
- **Status:** `ok`, `warn`, `danger`, `info`. `danger` is the only error colour. Task state labels use `StatusStamp` in `TaskStatePanel`.
- **Fonts:** `font-sans` (IBM Plex Sans) for UI and reading text, `font-mono` (JetBrains Mono) for code, codes and `tabular-nums` figures. `max-w-measure` = 68ch.
- Radii: panels `rounded-2xl`, rows and cards `rounded-xl`, buttons, inputs and chips `rounded-full`.

## Gotchas

- **The API base URL comes from env.** `api.ts` reads `NEXT_PUBLIC_API_URL`, and `.env.local` points it at the deployed backend (`//fan-tianrui-fyp.fly.dev`), so local dev hits production data unless you change it. Fallback: `http://localhost:8000`.
- `wiki_route_tracking` in localStorage drives the "restore last page" redirect on `/`. `/` itself is never recorded (recording it caused a blank-page redirect loop).
- `next build` rewrites `next-env.d.ts`; don't commit that change.
- Some wikis have no vector index, so chat on them fails (e.g. hugo). Use maya (`/app/wiki/81b81b59-90ed-41b2-8669-8a31829e241e`) to test chat against production. Known backend gaps are listed in `../Document/BACKEND_GAPS_FOR_FRONTEND.md`.

## Design reference

`DESIGN.md` summarizes the current design system (tokens, layout, patterns, interaction). Backend gaps that limit the UI are tracked in `../Document/BACKEND_GAPS_FOR_FRONTEND.md` (local, untracked).
