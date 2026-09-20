# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
npm run dev     # start Next.js dev server (Turbopack)
npm run build   # production build
npm run start   # serve the production build
npm run lint    # eslint (flat config: eslint-config-next core-web-vitals + typescript)
```

There is no test suite configured in this package.

## Architecture

This is a Next.js 16 (App Router) app, but the App Router is used only as a **static shell**: every real route is handled client-side by `react-router-dom`, not by Next's file-based routing.

- `src/app/page.tsx` and `src/app/[...slug]/page.tsx` both just render `RouterApp` (dynamically imported with `ssr: false`). The catch-all route means Next always hands off to the client router regardless of path.
- `src/router/RouterApp.tsx` defines the actual route tree with `createBrowserRouter`. This is the file to edit when adding/moving pages. Route components live in `src/router/routes/*Route.tsx` (thin lazy wrappers) and render views from `src/views/*Page.tsx`.
- Route tree shape: `/` (`RootLayoutView`) → `GlobalRouteGuard` (records last-visited path to `localStorage` under `wiki_route_tracking`, used by `RestoreLastPath` to send `/` back to wherever the user was) → public routes (`/login`, `/auth/callback`) and `AuthGuard`-protected routes under `/app` (`AppLayout` → `dashboard`, `history` (requires role `user` via `RoutePermissionGuard`), `wiki/:taskId`).
- Auth guards and role checks are in `src/router/guards.tsx`. Auth state comes from `src/providers/AuthProvider.tsx`, which wraps Supabase (`src/lib/supabase.ts`) session state in a context (`useAuth()`).
- All interactive components are `'use client'` — there is effectively no server-rendered content; treat this as a client SPA that happens to be deployed via Next/Vercel.

### Backend integration

- `src/lib/api.ts` is the single client for the FastAPI backend (see `../docker/scripts/api.py` in the monorepo). It covers wiki generation tasks (`/generate`, `/task/:id`, `/tasks`, `/dashboard/repos`) and the single unified, agent-first chat endpoint (`/chat`, `/chat/stream` SSE, `/chat/history`, `/chat/messages/:id`) — there is no separate "RAG mode" vs. "Agent mode" split; `/chat`'s `ChatTurnResponse` returns a tool-call trajectory (`ToolTrajectoryStep[]`, field `tool_trajectory`) alongside the answer, and `/chat/stream` emits it incrementally via `turn_start`/`iteration_start`/`tool_call_start`/`tool_call_result`/`answer_token`/`answer_done`/`complete`/`error` events.
- **`API_BASE_URL` in `src/lib/api.ts` is currently hardcoded to `http://localhost:8000`** (the `NEXT_PUBLIC_API_URL` env read is commented out). When working against a deployed backend, check this constant first.
- The SSE stream parser (`askQuestionStream`) is a hand-rolled `event:`/`data:` line parser, not `EventSource` — follow the same pattern if adding another streaming endpoint.
- `normalizeRepoUrl()` in `api.ts` must stay in sync with the backend's `_normalize_repo_url` — it's how the frontend matches a pasted GitHub URL to an already-indexed repo.

### Wiki viewer / chat

- `WikiViewer` (`src/components/WikiViewer.tsx`) fetches a wiki structure JSON and per-section content JSON (from R2/CDN URLs returned by the task status, not from the API base) and renders Markdown + Mermaid diagrams, with an in-memory TTL cache (`contentCache`) keyed by content URL.
- `ChatInterface` (`src/components/ChatInterface.tsx`) implements the single chat/agent panel embedded in the wiki view: chat history sidebar, streaming answer rendering (`MessageItem`), source citations (`SourcesPanel`, `CodeViewer`), and a live tool-call/iteration display (`LiveStepFlow`) driven by the unified SSE event vocabulary — there is no mode toggle. It locally replicates the backend's chat-title generation (`generateChatPreview`) to avoid an extra round trip.
- `src/lib/i18n.ts` is a flat English string table (`t()` lookup) — there is no locale switching, it exists purely to keep UI copy out of components.

### Path aliases

`@/*` maps to `src/*` (see `tsconfig.json`). Use `@/...` imports rather than relative paths across directories, matching the existing codebase.

## Environment

Relevant vars (see `.env.local`, not committed): `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY` (required for auth — `lib/supabase.ts` warns and falls back to an unusable client if missing), `NEXT_PUBLIC_API_URL` (currently unused, see above).
