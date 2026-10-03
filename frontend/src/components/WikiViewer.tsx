"use client";

import { List, PanelLeftClose, PanelLeftOpen, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import ChatInterface from "@/components/ChatInterface";
import { TaskStatePanel } from "@/components/wiki/TaskStatePanel";
import { WikiArticle, type WikiPageBody } from "@/components/wiki/WikiArticle";
import { WikiToc } from "@/components/wiki/WikiToc";
import { t } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { parseWikiToc, type WikiToc as Toc, type TocNode } from "@/lib/wikiToc";

interface WikiViewerProps {
  userId: string;
  structureUrl: string;
  contentUrls: string[];
  repoUrl?: string;
  initialChatId?: string;
}

// Artifacts live under a per-task R2 prefix and never change, so an in-memory
// cache for the session is safe.
const structureCache = new Map<string, Toc>();
const pageCache = new Map<string, WikiPageBody>();

/** Raw R2 URLs are private; the public CDN serves the same paths. */
function publicUrl(url: string): string {
  if (!url.includes("r2.cloudflarestorage.com")) return url;
  try {
    return `https://cityu-fyp.livelive.fun${new URL(url).pathname}`;
  } catch {
    return url;
  }
}

/** `…/architecture-cli.json` → `architecture-cli`. */
function pageIdOf(url: string): string {
  const path = url.split(/[?#]/)[0];
  return (path.split("/").pop() ?? "").replace(/\.json$/, "");
}

/** Page JSON `content`; older artifacts nest it as a JSON string. */
function parsePageBody(raw: unknown): WikiPageBody {
  let value = raw;
  for (let depth = 0; depth < 2 && typeof value === "string"; depth++) {
    try {
      value = JSON.parse(value);
    } catch {
      return { intro: value as string, sections: [], mermaid: "" };
    }
  }
  if (!value || typeof value !== "object") {
    return { intro: "", sections: [], mermaid: "" };
  }
  const record = value as {
    intro?: unknown;
    sections?: unknown;
    mermaid?: unknown;
  };
  const intro = typeof record.intro === "string" ? record.intro : "";
  if (intro.trim().startsWith("{")) {
    const nested = parsePageBody(intro);
    if (nested.sections.length > 0 || nested.intro !== intro) return nested;
  }
  return {
    intro,
    sections: Array.isArray(record.sections)
      ? record.sections.filter(
          (s): s is WikiPageBody["sections"][number] =>
            Boolean(s) &&
            typeof s.heading === "string" &&
            typeof s.body === "string",
        )
      : [],
    mermaid: typeof record.mermaid === "string" ? record.mermaid : "",
  };
}

type Loaded<T> = { url: string; data: T } | { url: string; error: string };

/** Fetches JSON from the CDN once per URL; abortable, cached for the session. */
function useArtifact<T>(
  url: string | null,
  cache: Map<string, T>,
  parse: (json: unknown) => T,
): { data: T | null; error: string | null; loading: boolean } {
  const [loaded, setLoaded] = useState<Loaded<T> | null>(null);
  const cached = url ? cache.get(url) : undefined;

  useEffect(() => {
    if (!url || cache.has(url)) return;
    const controller = new AbortController();
    fetch(url, { signal: controller.signal })
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((json: unknown) => {
        const data = parse(json);
        cache.set(url, data);
        setLoaded({ url, data });
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setLoaded({
          url,
          error: err instanceof Error ? err.message : String(err),
        });
      });
    return () => controller.abort();
  }, [url, cache, parse]);

  if (!url) return { data: null, error: null, loading: false };
  if (cached) return { data: cached, error: null, loading: false };
  if (loaded?.url !== url) return { data: null, error: null, loading: true };
  return "data" in loaded
    ? { data: loaded.data, error: null, loading: false }
    : { data: null, error: loaded.error, loading: false };
}

const parsePageJson = (json: unknown) =>
  parsePageBody((json as { content?: unknown } | null)?.content);

/** Short page summary sent along with chat questions. */
function pageContext(
  node: TocNode,
  chapter: TocNode | null,
  body: WikiPageBody | null,
): string {
  const parts = [`Page: ${node.code} ${node.title}`];
  if (chapter) parts.push(`Path: ${chapter.title} / ${node.title}`);
  if (node.files.length > 0) {
    parts.push(`Related files: ${node.files.slice(0, 3).join(", ")}`);
  }
  const firstSentence = body?.intro.split(/[.!?。！？]\s*/)[0]?.trim();
  if (firstSentence && firstSentence.length < 150) {
    parts.push(`Summary: ${firstSentence}`);
  }
  const headings = body?.sections
    .slice(0, 4)
    .map((s) => s.heading)
    .join(", ");
  if (headings) parts.push(`Sections: ${headings}`);
  return parts.join("\n");
}

function ContentsPanel({
  toc,
  node,
  onClose,
}: {
  toc: Toc;
  node: TocNode;
  onClose: () => void;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    closeRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={t("wikiContents")}
      className="fixed inset-0 z-50 flex flex-col bg-sheet md:hidden"
    >
      <div className="flex h-12 shrink-0 items-center justify-between border-n-3 border-b px-4">
        <span className="font-medium text-ink text-sm">
          {t("wikiContents")}
        </span>
        <button
          ref={closeRef}
          type="button"
          onClick={onClose}
          aria-label={t("wikiCloseContents")}
          className="-mr-2 p-2 text-n-7 hover:text-ink"
        >
          <X aria-hidden className="h-5 w-5" />
        </button>
      </div>
      <nav
        aria-label={t("wikiContents")}
        className="flex-1 overflow-y-auto px-2 py-3"
      >
        <WikiToc
          nodes={toc.nodes}
          activeId={node.id}
          activeChapterId={node.chapterId}
          onNavigate={onClose}
        />
      </nav>
    </div>
  );
}

export default function WikiViewer({
  userId,
  structureUrl,
  contentUrls,
  repoUrl,
  initialChatId,
}: WikiViewerProps) {
  const [searchParams] = useSearchParams();
  const [tocCollapsed, setTocCollapsed] = useState(false);
  const [contentsOpen, setContentsOpen] = useState(false);
  const [evidencePath, setEvidencePath] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const closeContents = useCallback(() => setContentsOpen(false), []);

  const structure = useArtifact(
    publicUrl(structureUrl),
    structureCache,
    parseWikiToc,
  );
  const toc = structure.data;

  const pageUrls = useMemo(() => {
    const map = new Map<string, string>();
    for (const url of contentUrls) map.set(pageIdOf(url), publicUrl(url));
    return map;
  }, [contentUrls]);

  const code = searchParams.get("d");
  const node = (code && toc?.byCode.get(code)) || toc?.order[0] || null;
  const chapter =
    node && node.chapterId !== node.id
      ? (toc?.byId.get(node.chapterId) ?? null)
      : null;
  const pageUrl = node ? (pageUrls.get(node.id) ?? null) : null;
  const page = useArtifact(pageUrl, pageCache, parsePageJson);

  const nodeId = node?.id;
  useEffect(() => {
    if (nodeId) scrollRef.current?.scrollTo({ top: 0 });
    setEvidencePath(null);
  }, [nodeId]);
  const evidence = evidencePath ? toc?.byFile.get(evidencePath) : undefined;

  const chatContext = node ? pageContext(node, chapter, page.data) : undefined;
  const chatPage = useMemo(
    () =>
      node
        ? { code: node.code, title: node.title, file: node.files[0] }
        : undefined,
    [node],
  );

  if (structure.error) {
    return (
      <TaskStatePanel
        stamp="r2"
        title={t("wikiStructureError")}
        detail={structure.error}
        detailIsError
      />
    );
  }

  return (
    <div className="flex min-h-0 flex-1">
      <aside
        className={cn(
          "hidden shrink-0 flex-col border-n-3 border-r bg-sheet md:flex",
          tocCollapsed ? "w-16" : "w-56 2xl:w-72",
        )}
      >
        <div
          className={cn(
            "flex h-12 shrink-0 items-center border-n-2 border-b",
            tocCollapsed ? "justify-center" : "justify-between pr-2 pl-4",
          )}
        >
          {tocCollapsed ? null : (
            <span className="truncate font-medium text-ink text-sm">
              {toc?.title || t("wikiContents")}
            </span>
          )}
          <button
            type="button"
            onClick={() => setTocCollapsed((prev) => !prev)}
            aria-expanded={!tocCollapsed}
            aria-label={
              tocCollapsed ? t("wikiExpandToc") : t("wikiCollapseToc")
            }
            title={tocCollapsed ? t("wikiExpandToc") : t("wikiCollapseToc")}
            className="p-2 text-n-6 hover:text-ink"
          >
            {tocCollapsed ? (
              <PanelLeftOpen aria-hidden className="h-4 w-4" />
            ) : (
              <PanelLeftClose aria-hidden className="h-4 w-4" />
            )}
          </button>
        </div>
        <nav
          aria-label={t("wikiContents")}
          aria-busy={structure.loading}
          className="min-h-0 flex-1 overflow-y-auto px-2 py-3"
        >
          {toc && node ? (
            <WikiToc
              nodes={toc.nodes}
              activeId={node.id}
              activeChapterId={node.chapterId}
              evidence={evidence}
              compact={tocCollapsed}
            />
          ) : null}
        </nav>
      </aside>

      <div
        ref={scrollRef}
        className="@container min-w-0 flex-1 overflow-y-auto"
      >
        {node ? (
          <div className="sticky top-0 z-10 flex h-12 items-center border-n-3 border-b bg-paper px-4 md:hidden">
            <button
              type="button"
              onClick={() => setContentsOpen(true)}
              aria-haspopup="dialog"
              aria-label={t("wikiOpenContents")}
              className="flex min-w-0 items-center gap-2 text-ink text-sm"
            >
              <List aria-hidden className="h-4 w-4 shrink-0 text-n-6" />
              <span className="font-mono text-xs tabular-nums">
                {node.code}
              </span>
              <span className="truncate">{node.title}</span>
            </button>
          </div>
        ) : null}

        <div className="mx-auto max-w-6xl px-5 py-10 md:px-10 lg:py-14">
          {node && toc ? (
            <WikiArticle
              node={node}
              chapter={chapter}
              body={page.data}
              byFile={toc.byFile}
              repoUrl={repoUrl}
              onEvidence={setEvidencePath}
              state={
                !pageUrl
                  ? "missing"
                  : page.error
                    ? "error"
                    : page.loading
                      ? "loading"
                      : "ready"
              }
            />
          ) : null}
        </div>
      </div>

      {contentsOpen && toc && node ? (
        <ContentsPanel toc={toc} node={node} onClose={closeContents} />
      ) : null}

      {repoUrl ? (
        <ChatInterface
          // A different repo starts a fresh conversation.
          key={repoUrl}
          userId={userId}
          repoUrl={repoUrl}
          page={chatPage}
          currentPageContext={chatContext}
          initialChatId={initialChatId}
        />
      ) : null}
    </div>
  );
}
