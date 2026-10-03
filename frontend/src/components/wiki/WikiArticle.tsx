"use client";

import dynamic from "next/dynamic";
import { Fragment, memo, useMemo, useState } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import { Link } from "react-router-dom";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";
import Mermaid from "@/components/Mermaid";
import { useCodeHref } from "@/components/wiki/WikiToc";
import { parseCitations } from "@/lib/citations";
import { t } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { type MarginNote, placeNotes } from "@/lib/wikiNotes";
import { slotSwatch, type TocNode } from "@/lib/wikiToc";
import "highlight.js/styles/github.css";

const CodeViewer = dynamic(() => import("@/components/CodeViewer"), {
  ssr: false,
});

interface WikiSection {
  heading: string;
  body: string;
}

export interface WikiPageBody {
  intro: string;
  sections: WikiSection[];
  mermaid: string;
}

const REMARK_PLUGINS = [remarkGfm];
const REHYPE_PLUGINS = [rehypeHighlight];

const proseClass = cn(
  "prose max-w-none font-serif text-[1.0625rem] leading-relaxed text-ink",
  "prose-headings:font-serif prose-headings:font-semibold prose-headings:text-ink",
  "prose-p:text-ink prose-li:text-ink prose-strong:text-ink",
  "prose-a:text-rail prose-a:underline-offset-2",
  "prose-code:font-mono prose-code:text-[0.875em] prose-code:font-normal prose-code:text-ink",
  "prose-code:before:content-none prose-code:after:content-none",
  "prose-pre:rounded-none prose-pre:border prose-pre:border-n-3 prose-pre:bg-sheet prose-pre:text-ink",
  "prose-blockquote:border-n-4 prose-blockquote:text-n-8",
  "prose-th:text-ink prose-td:text-ink prose-hr:border-n-3",
);

/**
 * Main column and margin column; children pick a column explicitly. Keyed to
 * the reading column's width (a container query), since the docked chat and
 * the TOC change it independently of the viewport.
 */
const DESK_GRID =
  "grid gap-y-8 @5xl:grid-cols-[minmax(0,var(--container-measure))_14rem] @5xl:gap-x-12";
const MAIN_COL = "min-w-0 @5xl:col-start-1";
const MARGIN_COL = "@5xl:col-start-2";

const Markdown = memo(function Markdown({
  text,
  mentions,
}: {
  text: string;
  mentions: Map<string, MarginNote>;
}) {
  // Inline code naming a cited file gets that note's number as a marker.
  const components = useMemo<Components>(
    () => ({
      code({ className, children, node: _node, ...rest }) {
        const value = String(children ?? "");
        const note =
          !className && !value.includes("\n") ? mentions.get(value) : null;
        return (
          <>
            <code className={className} {...rest}>
              {children}
            </code>
            {note ? (
              <sup className="ml-0.5 font-mono text-[0.7em] text-n-6 tabular-nums">
                {note.number}
              </sup>
            ) : null}
          </>
        );
      },
    }),
    [mentions],
  );

  return (
    <div className={proseClass}>
      <ReactMarkdown
        remarkPlugins={REMARK_PLUGINS}
        rehypePlugins={REHYPE_PLUGINS}
        components={components}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
});

interface NotesProps {
  notes: MarginNote[];
  page: TocNode;
  byFile: Map<string, TocNode[]>;
  onOpen: (fileIndex: number) => void;
  onEvidence: (path: string | null) => void;
}

/** Numbered sources beside a block: path, then the other pages citing it. */
function MarginNotes({ notes, page, byFile, onOpen, onEvidence }: NotesProps) {
  const codeHref = useCodeHref();
  return (
    <aside aria-label={t("wikiNotes")} className={MARGIN_COL}>
      <ol className="space-y-3 border-n-3 border-t pt-3 @5xl:border-t-0 @5xl:pt-1">
        {notes.map(({ number, file, fileIndex }) => {
          const slash = file.path.lastIndexOf("/");
          const others = (byFile.get(file.path) ?? []).filter(
            (other) => other.id !== page.id,
          );
          return (
            <li key={file.path} className="text-xs leading-snug">
              <button
                type="button"
                onClick={() => onOpen(fileIndex)}
                onMouseEnter={() => onEvidence(file.path)}
                onMouseLeave={() => onEvidence(null)}
                onFocus={() => onEvidence(file.path)}
                onBlur={() => onEvidence(null)}
                aria-label={t("wikiOpenSource", { path: file.path })}
                title={file.path}
                className="group -mx-1.5 flex w-[calc(100%+0.75rem)] gap-2 px-1.5 py-1 text-left hover:bg-evidence-tint focus-visible:bg-evidence-tint"
              >
                <span className="w-4 shrink-0 text-right font-mono text-n-6 tabular-nums group-hover:text-evidence group-focus-visible:text-evidence">
                  {number}
                </span>
                <span className="min-w-0 break-words font-mono">
                  {slash > 0 ? (
                    <span className="text-n-6">
                      {file.path
                        .slice(0, slash)
                        .split("/")
                        .map((part, i) => (
                          // biome-ignore lint/suspicious/noArrayIndexKey: path segments are positional
                          <Fragment key={i}>
                            {part}/<wbr />
                          </Fragment>
                        ))}
                    </span>
                  ) : null}
                  <span className="text-ink">{file.fileName}</span>
                </span>
              </button>
              {others.length > 0 ? (
                <p className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 pl-6 text-n-6">
                  <span>{t("wikiAlsoIn")}</span>
                  {others.map((other) => (
                    <Link
                      key={other.id}
                      to={codeHref(other.code)}
                      title={other.title}
                      className="inline-flex items-center gap-1 font-mono text-n-7 tabular-nums hover:text-ink"
                    >
                      <span
                        aria-hidden
                        className={cn("h-1.5 w-1.5", slotSwatch(other.slot))}
                      />
                      {other.code}
                    </Link>
                  ))}
                </p>
              ) : null}
            </li>
          );
        })}
      </ol>
    </aside>
  );
}

interface WikiArticleProps {
  node: TocNode;
  /** Top-level chapter, when `node` is a page inside one. */
  chapter: TocNode | null;
  body: WikiPageBody | null;
  state: "loading" | "ready" | "error" | "missing";
  byFile: Map<string, TocNode[]>;
  repoUrl?: string;
  /** Hovered or focused note path, for highlighting elsewhere. */
  onEvidence: (path: string | null) => void;
}

export const WikiArticle = memo(function WikiArticle({
  node,
  chapter,
  body,
  state,
  byFile,
  repoUrl,
  onEvidence,
}: WikiArticleProps) {
  const [inspected, setInspected] = useState<number | null>(null);
  const files = useMemo(() => parseCitations(node.files).files, [node.files]);
  const placed = useMemo(
    () =>
      placeNotes(
        files,
        body ? [body.intro, ...body.sections.map((s) => s.body)] : [],
      ),
    [files, body],
  );

  const notesFor = (block: number) => {
    const notes = placed.byBlock[block] ?? [];
    return notes.length > 0 ? (
      <MarginNotes
        notes={notes}
        page={node}
        byFile={byFile}
        onOpen={setInspected}
        onEvidence={onEvidence}
      />
    ) : null;
  };

  return (
    <article aria-busy={state === "loading"} className={DESK_GRID}>
      <header className={cn(MAIN_COL, "border-n-3 border-b pb-6")}>
        <p className="flex items-center gap-2 font-mono text-n-7 text-xs tabular-nums">
          <span aria-hidden className={cn("h-2 w-2", slotSwatch(node.slot))} />
          <span>{node.code}</span>
          {chapter ? (
            <>
              <span aria-hidden className="text-n-5">
                /
              </span>
              <span className="truncate font-sans">{chapter.title}</span>
            </>
          ) : null}
        </p>
        <h1 className="mt-3 font-semibold font-serif text-3xl text-ink leading-tight md:text-4xl">
          {node.title}
        </h1>
      </header>

      {state === "loading" ? (
        <div aria-hidden className={cn(MAIN_COL, "space-y-3")}>
          <div className="h-3 w-11/12 bg-n-2" />
          <div className="h-3 w-10/12 bg-n-2" />
          <div className="h-3 w-8/12 bg-n-2" />
        </div>
      ) : null}

      {state === "error" || state === "missing" ? (
        <p
          className={cn(
            MAIN_COL,
            "border-ink border-l-2 bg-sheet py-3 pl-4 text-ink text-sm",
          )}
        >
          {state === "error" ? t("wikiPageError") : t("wikiPageMissing")}
        </p>
      ) : null}

      {state === "ready" && body ? (
        <>
          <div className={MAIN_COL}>
            {body.intro ? (
              <Markdown text={body.intro} mentions={placed.byMention} />
            ) : null}
          </div>
          {notesFor(0)}

          {body.mermaid ? (
            <figure className={MAIN_COL}>
              <figcaption className="mb-2 font-mono text-n-6 text-xs uppercase tracking-wider">
                {t("wikiDiagram")}
              </figcaption>
              <Mermaid chart={body.mermaid} />
            </figure>
          ) : null}

          {body.sections.map((section, index) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: sections are static per page and headings may repeat
            <Fragment key={index}>
              <section className={cn(MAIN_COL, "scroll-mt-8")}>
                <h2 className="mb-4 font-semibold font-serif text-2xl text-ink">
                  {section.heading}
                </h2>
                <Markdown text={section.body} mentions={placed.byMention} />
              </section>
              {notesFor(index + 1)}
            </Fragment>
          ))}
        </>
      ) : null}

      {inspected !== null && repoUrl ? (
        <CodeViewer
          sources={files}
          initialSourceIndex={inspected}
          repoUrl={repoUrl}
          onClose={() => setInspected(null)}
        />
      ) : null}
    </article>
  );
});
