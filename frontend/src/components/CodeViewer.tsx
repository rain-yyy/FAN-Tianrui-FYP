"use client";

import { Check, Copy, RotateCw, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useModalDialog } from "@/hooks/useModalDialog";
import { api } from "@/lib/api";
import { type FileCitation, formatFileCitation } from "@/lib/citations";
import { t } from "@/lib/i18n";
import { cn } from "@/lib/utils";

interface CodeViewerProps {
  onClose: () => void;
  /** Files the inspector can switch between; indexes match the caller's. */
  sources: FileCitation[];
  initialSourceIndex?: number;
  repoUrl: string;
}

// File text per repo and path for the session; ranges of one file share it.
const fileCache = new Map<string, string>();
const cacheKey = (repoUrl: string, path: string) => `${repoUrl}\n${path}`;

type FileState =
  | { status: "loading" }
  | { status: "ready"; text: string }
  | { status: "error"; message: string };

type Loaded = { key: string; state: FileState };

/** Fetches a file once per session; abortable, retried by bumping `attempt`. */
function useFileText(
  repoUrl: string,
  path: string | undefined,
  attempt: number,
): FileState {
  const key = path ? cacheKey(repoUrl, path) : "";
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const cached = key ? fileCache.get(key) : undefined;

  // biome-ignore lint/correctness/useExhaustiveDependencies: `attempt` re-runs the fetch on Retry
  useEffect(() => {
    if (!path || fileCache.has(key)) return;
    const controller = new AbortController();
    api
      .getFileContent(repoUrl, path, controller.signal)
      .then((text) => {
        fileCache.set(key, text);
        setLoaded({ key, state: { status: "ready", text } });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setLoaded({
          key,
          state: {
            status: "error",
            message: error instanceof Error ? error.message : String(error),
          },
        });
      });
    return () => controller.abort();
  }, [repoUrl, path, key, attempt]);

  if (cached !== undefined) return { status: "ready", text: cached };
  if (!loaded || loaded.key !== key) return { status: "loading" };
  return loaded.state;
}

/** Highlight a range only when it narrows the file down. */
function focusRange(file: FileCitation, lineCount: number) {
  if (!file.lines) return null;
  const start = Math.max(1, Math.min(file.lines.start, lineCount));
  const end = Math.max(start, Math.min(file.lines.end, lineCount));
  return end - start + 1 < lineCount * 0.8 ? { start, end } : { start, end: 0 };
}

function CodeLines({
  text,
  file,
  label,
}: {
  text: string;
  file: FileCitation;
  label: string;
}) {
  const scrollRef = useRef<HTMLElement>(null);
  const lines = text.split("\n");
  if (lines.length > 1 && lines[lines.length - 1] === "") lines.pop();
  const range = focusRange(file, lines.length);
  const gutter = `${String(lines.length).length + 1}ch`;
  const focusStart = range?.start;

  // Bring the cited line into view and focus the code for keyboard scrolling.
  // useModalDialog opens the dialog in a layout effect, so it has layout here.
  useEffect(() => {
    const scroller = scrollRef.current;
    if (!scroller) return;
    scroller.focus({ preventScroll: true });
    if (!focusStart) return;
    const line = scroller.querySelector<HTMLElement>(
      `[data-line="${focusStart}"]`,
    );
    if (line) scroller.scrollTop = line.offsetTop - scroller.clientHeight / 4;
  }, [focusStart]);

  return (
    <section
      ref={scrollRef}
      // biome-ignore lint/a11y/noNoninteractiveTabindex: the code region scrolls, so it must take keyboard focus
      tabIndex={0}
      aria-label={label}
      className="min-h-0 flex-1 overflow-auto bg-raised outline-none focus-visible:shadow-[inset_0_0_0_2px_var(--color-accent-line)]"
    >
      <pre className="py-3 font-mono text-[0.8125rem] text-fg leading-[1.6]">
        {lines.map((content, index) => {
          const number = index + 1;
          const marked =
            range !== null &&
            range.end > 0 &&
            number >= range.start &&
            number <= range.end;
          return (
            <div
              // biome-ignore lint/suspicious/noArrayIndexKey: lines are positional
              key={index}
              data-line={number}
              className={cn("flex", marked && "bg-accent-soft")}
            >
              <span
                aria-hidden
                className={cn(
                  "shrink-0 select-none pr-4 text-right tabular-nums",
                  marked
                    ? "text-accent shadow-[inset_2px_0_0_var(--color-accent)]"
                    : "text-fg-muted",
                )}
                style={{ width: `calc(${gutter} + 1.25rem)` }}
              >
                {number}
              </span>
              <code className="whitespace-pre pr-6">{content || " "}</code>
            </div>
          );
        })}
      </pre>
    </section>
  );
}

export default function CodeViewer({
  onClose,
  sources,
  initialSourceIndex = 0,
  repoUrl,
}: CodeViewerProps) {
  const [index, setIndex] = useState(() =>
    Math.min(Math.max(0, initialSourceIndex), Math.max(0, sources.length - 1)),
  );
  const [filter, setFilter] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [copied, setCopied] = useState(false);
  const file = sources[index];
  const state = useFileText(repoUrl, file?.path, attempt);
  const label = file ? formatFileCitation(file) : "";

  const dialogProps = useModalDialog(onClose);

  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 1500);
    return () => clearTimeout(timer);
  }, [copied]);

  const copyPath = () => {
    if (!label) return;
    void navigator.clipboard.writeText(label).then(() => setCopied(true));
  };

  const retry = () => {
    if (file) fileCache.delete(cacheKey(repoUrl, file.path));
    setAttempt((n) => n + 1);
  };

  const query = filter.trim().toLowerCase();
  const visible = sources
    .map((source, i) => ({ source, i }))
    .filter(
      ({ source }) => !query || source.path.toLowerCase().includes(query),
    );
  const slash = file ? file.path.lastIndexOf("/") : -1;

  return (
    <dialog
      {...dialogProps}
      aria-labelledby="code-inspector-title"
      className="m-auto h-[min(88vh,60rem)] w-[min(72rem,calc(100vw-2rem))] max-w-none overflow-hidden rounded-2xl border border-line-strong bg-panel p-0 text-fg backdrop:bg-bg/80"
    >
      <div className="flex h-full flex-col">
        <header className="flex h-12 shrink-0 items-center gap-3 border-line border-b pr-2 pl-4">
          <h2
            id="code-inspector-title"
            className="shrink-0 font-medium text-fg-muted text-xs uppercase tracking-wider"
          >
            {t("inspectorTitle")}
          </h2>
          {file ? (
            <p
              className="min-w-0 flex-1 truncate font-mono text-sm"
              title={label}
            >
              {slash > 0 ? (
                <span className="text-fg-muted">
                  {file.path.slice(0, slash + 1)}
                </span>
              ) : null}
              <span className="text-fg">{file.fileName}</span>
              {file.lines ? (
                <span className="text-fg-muted tabular-nums">
                  :{file.lines.start}
                  {file.lines.end !== file.lines.start
                    ? `–${file.lines.end}`
                    : ""}
                </span>
              ) : null}
            </p>
          ) : (
            <span className="flex-1" />
          )}
          <button
            type="button"
            onClick={copyPath}
            disabled={!file}
            className="inline-flex h-8 items-center gap-1.5 rounded-full px-3 text-fg-muted text-xs hover:bg-raised hover:text-fg"
          >
            {copied ? (
              <Check aria-hidden className="h-3.5 w-3.5" />
            ) : (
              <Copy aria-hidden className="h-3.5 w-3.5" />
            )}
            {copied ? t("inspectorCopied") : t("inspectorCopy")}
          </button>
          <button
            type="button"
            onClick={onClose}
            aria-label={t("inspectorClose")}
            title={t("inspectorClose")}
            className="inline-flex h-8 w-8 items-center justify-center rounded-full text-fg-muted hover:bg-raised hover:text-fg"
          >
            <X aria-hidden className="h-4 w-4" />
          </button>
        </header>

        <div className="flex min-h-0 flex-1">
          {sources.length > 1 ? (
            <nav
              aria-label={t("inspectorFiles")}
              className="hidden w-60 shrink-0 flex-col border-line border-r bg-bg md:flex"
            >
              {sources.length > 8 ? (
                <input
                  type="search"
                  value={filter}
                  onChange={(event) => setFilter(event.target.value)}
                  placeholder={t("inspectorFilter")}
                  aria-label={t("inspectorFilter")}
                  className="m-2 border border-line bg-panel px-2 py-1.5 text-xs outline-none placeholder:text-fg-faint focus:border-accent-line"
                />
              ) : null}
              <ul className="min-h-0 flex-1 overflow-y-auto py-1">
                {visible.map(({ source, i }) => {
                  const itemLabel = formatFileCitation(source);
                  return (
                    <li key={itemLabel}>
                      <button
                        type="button"
                        onClick={() => setIndex(i)}
                        aria-current={i === index ? "true" : undefined}
                        title={itemLabel}
                        className={cn(
                          "block w-full truncate px-3 py-1.5 text-left font-mono text-xs",
                          i === index
                            ? "bg-accent-soft text-accent"
                            : "text-fg hover:bg-raised hover:text-fg",
                        )}
                      >
                        {source.fileName}
                        {source.lines ? (
                          <span
                            className={cn(
                              "tabular-nums",
                              i === index ? "text-accent" : "text-fg-faint",
                            )}
                          >
                            :{source.lines.start}
                          </span>
                        ) : null}
                      </button>
                    </li>
                  );
                })}
              </ul>
              <p className="border-line border-t px-3 py-2 font-mono text-fg-muted text-xs tabular-nums">
                {t("inspectorCount", { n: sources.length })}
              </p>
            </nav>
          ) : null}

          <div
            className="flex min-w-0 flex-1 flex-col"
            aria-busy={state.status === "loading"}
          >
            {sources.length > 1 ? (
              // Below md the file list is hidden; a select keeps switching.
              <select
                value={index}
                onChange={(event) => setIndex(Number(event.target.value))}
                aria-label={t("inspectorFiles")}
                className="shrink-0 border-line border-b bg-bg px-3 py-2 font-mono text-xs md:hidden"
              >
                {sources.map((source, i) => (
                  <option key={formatFileCitation(source)} value={i}>
                    {formatFileCitation(source)}
                  </option>
                ))}
              </select>
            ) : null}
            {!file ? null : state.status === "loading" ? (
              <p className="p-6 text-fg-muted text-sm">
                {t("inspectorLoading", { path: file.path })}
              </p>
            ) : state.status === "error" ? (
              <div className="p-6">
                <p className="font-medium text-fg text-sm">
                  {t("inspectorFailed", { path: file.path })}
                </p>
                <p className="mt-2 border-line-strong border-l-2 bg-bg py-2 pl-3 font-mono text-fg text-xs">
                  {state.message}
                </p>
                <button
                  type="button"
                  onClick={retry}
                  className="mt-4 inline-flex items-center gap-2 rounded-full border border-line px-3 py-1.5 text-fg text-sm hover:border-line-strong"
                >
                  <RotateCw aria-hidden className="h-3.5 w-3.5" />
                  {t("retry")}
                </button>
              </div>
            ) : (
              <CodeLines
                // Remount per file so scroll position and focus start fresh.
                key={label}
                text={state.text}
                file={file}
                label={t("inspectorCode", { path: label })}
              />
            )}
          </div>
        </div>
      </div>
    </dialog>
  );
}
