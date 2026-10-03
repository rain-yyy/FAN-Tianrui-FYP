"use client";

import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";
import Mermaid from "@/components/Mermaid";
import { t } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { slotSwatch, type TocNode } from "@/lib/wikiToc";
import "highlight.js/styles/github.css";

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

function Markdown({ children }: { children: string }) {
  return (
    <div className={proseClass}>
      <ReactMarkdown
        remarkPlugins={REMARK_PLUGINS}
        rehypePlugins={REHYPE_PLUGINS}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}

interface WikiArticleProps {
  node: TocNode;
  /** Top-level chapter, when `node` is a page inside one. */
  chapter: TocNode | null;
  body: WikiPageBody | null;
  state: "loading" | "ready" | "error" | "missing";
}

export function WikiArticle({ node, chapter, body, state }: WikiArticleProps) {
  return (
    <article aria-busy={state === "loading"} className="min-w-0">
      <header className="border-n-3 border-b pb-6">
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
        <h1
          id="wiki-page-title"
          className="mt-3 font-serif font-semibold text-3xl text-ink leading-tight md:text-4xl"
        >
          {node.title}
        </h1>
      </header>

      {state === "loading" ? (
        <div aria-hidden className="mt-8 space-y-3">
          <div className="h-3 w-11/12 bg-n-2" />
          <div className="h-3 w-10/12 bg-n-2" />
          <div className="h-3 w-8/12 bg-n-2" />
        </div>
      ) : null}

      {state === "error" || state === "missing" ? (
        <p className="mt-8 border-ink border-l-2 bg-sheet py-3 pl-4 text-sm text-ink">
          {state === "error" ? t("wikiPageError") : t("wikiPageMissing")}
        </p>
      ) : null}

      {state === "ready" && body ? (
        <div className="mt-8 space-y-10">
          {body.intro ? <Markdown>{body.intro}</Markdown> : null}

          {body.mermaid ? (
            <figure>
              <figcaption className="mb-2 font-mono text-n-6 text-xs uppercase tracking-wider">
                {t("wikiDiagram")}
              </figcaption>
              <Mermaid chart={body.mermaid} />
            </figure>
          ) : null}

          {body.sections.map((section, index) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: sections are static per page and headings may repeat
            <section key={index} className="scroll-mt-8">
              <h2 className="mb-4 font-semibold font-serif text-2xl text-ink">
                {section.heading}
              </h2>
              <Markdown>{section.body}</Markdown>
            </section>
          ))}
        </div>
      ) : null}
    </article>
  );
}
