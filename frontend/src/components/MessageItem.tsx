"use client";

import { ChevronDown, ChevronRight, ExternalLink } from "lucide-react";
import dynamic from "next/dynamic";
import { memo, useMemo, useState } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import { Link } from "react-router-dom";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";
import Mermaid from "@/components/Mermaid";
import { useCodeHref } from "@/components/wiki/WikiToc";
import { type AnswerNote, buildAnswerNotes } from "@/lib/answerNotes";
import type { ChatMessage, ToolTrajectoryStep } from "@/lib/api";
import { formatFileCitation } from "@/lib/citations";
import { t } from "@/lib/i18n";
import { answerProse } from "@/lib/prose";
import { getToolDescription, toolLabel } from "@/lib/toolDescriptions";
import type { TocNode } from "@/lib/wikiToc";

const CodeViewer = dynamic(() => import("@/components/CodeViewer"), {
  ssr: false,
});

export interface DisplayMessage extends ChatMessage {
  id: string;
  timestamp: Date;
  sources?: string[];
  isError?: boolean;
  /** The user stopped the stream; `content` is the partial answer. */
  stopped?: boolean;
  tool_trajectory?: ToolTrajectoryStep[];
}

const REMARK_PLUGINS = [remarkGfm];
const REHYPE_PLUGINS = [rehypeHighlight];

const timeFormat = new Intl.DateTimeFormat(undefined, {
  hour: "2-digit",
  minute: "2-digit",
});

interface HastNode {
  type?: string;
  value?: string;
  properties?: { className?: unknown };
  children?: HastNode[];
}

const hastText = (node: HastNode | undefined): string =>
  node?.type === "text"
    ? (node.value ?? "")
    : (node?.children ?? []).map(hastText).join("");

/** The tool calls behind an answer, collapsed to one line by default. */
function Trace({ steps }: { steps: ToolTrajectoryStep[] }) {
  const [open, setOpen] = useState(false);
  const failed = steps.filter((s) => s.status !== "success").length;
  const Chevron = open ? ChevronDown : ChevronRight;
  return (
    <div className="mt-3">
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        aria-expanded={open}
        className="inline-flex items-center gap-1 font-mono text-n-6 text-xs hover:text-ink"
      >
        <Chevron aria-hidden className="h-3 w-3" />
        {steps.length === 1
          ? t("chatTraceOne")
          : t("chatTraceMany", { n: steps.length })}
        {failed > 0 ? `, ${t("chatTraceFailed", { n: failed })}` : null}
      </button>
      {open ? (
        <ol className="mt-1.5 space-y-1 border-n-3 border-l pl-3">
          {steps.map((step, index) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: steps have no id and never reorder
            <li key={index} className="flex gap-2 text-xs leading-snug">
              <span className="w-12 shrink-0 font-mono text-n-6">
                {toolLabel(step.tool)}
              </span>
              <span className="min-w-0 flex-1 text-n-8">
                {getToolDescription(step.tool, step.arguments)}
              </span>
              {step.status === "success" ? null : (
                <span className="shrink-0 font-mono text-ink">
                  {t("chatStepFailed")}
                </span>
              )}
            </li>
          ))}
        </ol>
      ) : null}
    </div>
  );
}

interface NotesProps {
  notes: AnswerNote[];
  filePages?: Map<string, TocNode[]>;
  onOpen: (fileIndex: number) => void;
  onEvidence?: (path: string | null) => void;
}

/** Numbered evidence under the answer: file:lines, tool, wiki chapter. */
function AnswerNotesList({ notes, filePages, onOpen, onEvidence }: NotesProps) {
  const codeHref = useCodeHref();
  return (
    <ol
      aria-label={t("chatNotes")}
      className="mt-4 space-y-1.5 border-n-3 border-t pt-3"
    >
      {notes.map((note) => {
        if (note.kind === "web") {
          return (
            <li key={`web:${note.link.url}`} className="flex gap-2 text-xs">
              <span className="w-4 shrink-0 text-right font-mono text-n-6 tabular-nums">
                {note.number}
              </span>
              <a
                href={note.link.url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex min-w-0 items-center gap-1 text-n-8 underline-offset-2 hover:text-ink hover:underline"
              >
                <span className="truncate">{note.link.url}</span>
                <ExternalLink aria-hidden className="h-3 w-3 shrink-0" />
              </a>
            </li>
          );
        }
        const { file } = note;
        const label = formatFileCitation(file);
        const pages = filePages?.get(file.path) ?? [];
        return (
          <li key={label} className="text-xs leading-snug">
            <div className="flex gap-2">
              <span className="w-4 shrink-0 text-right font-mono text-n-6 tabular-nums">
                {note.number}
              </span>
              <button
                type="button"
                onClick={() => onOpen(note.fileIndex)}
                onMouseEnter={() => onEvidence?.(file.path)}
                onMouseLeave={() => onEvidence?.(null)}
                onFocus={() => onEvidence?.(file.path)}
                onBlur={() => onEvidence?.(null)}
                title={t("wikiOpenSource", { path: label })}
                className="min-w-0 flex-1 break-words text-left font-mono text-ink hover:bg-evidence-tint focus-visible:bg-evidence-tint"
              >
                {label}
              </button>
              {note.tool ? (
                <span className="shrink-0 font-mono text-n-6">
                  {toolLabel(note.tool)}
                </span>
              ) : null}
            </div>
            {pages.length > 0 ? (
              <p className="mt-0.5 flex flex-wrap gap-x-2 pl-6">
                {pages.slice(0, 3).map((page) => (
                  <Link
                    key={page.id}
                    to={codeHref(page.code)}
                    title={page.title}
                    className="inline-flex items-center gap-1 font-mono text-n-7 tabular-nums hover:text-ink"
                  >
                    {page.code}
                  </Link>
                ))}
              </p>
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}

export interface MessageItemProps {
  message: DisplayMessage;
  repoUrl: string;
  /** Repo-relative path → wiki pages listing it (`toc.byFile`). */
  filePages?: Map<string, TocNode[]>;
  /** Hovered note path, highlighted in the wiki TOC. */
  onEvidence?: (path: string | null) => void;
}

export const MessageItem = memo(function MessageItem({
  message,
  repoUrl,
  filePages,
  onEvidence,
}: MessageItemProps) {
  const [inspected, setInspected] = useState<number | null>(null);
  const isUser = message.role === "user";
  const notes = useMemo(
    () =>
      isUser
        ? null
        : buildAnswerNotes(
            message.content,
            message.sources,
            message.tool_trajectory,
          ),
    [isUser, message.content, message.sources, message.tool_trajectory],
  );

  const components = useMemo<Components>(
    () => ({
      // Inline file paths open the Code Inspector, marked with the note number.
      code({ className, children, node: _node, ...rest }) {
        const text = String(children ?? "");
        const note =
          !className && !text.includes("\n")
            ? notes?.byMention.get(text.trim())
            : undefined;
        if (!note) {
          return (
            <code className={className} {...rest}>
              {children}
            </code>
          );
        }
        return (
          <button
            type="button"
            onClick={() => setInspected(note.fileIndex)}
            title={t("wikiOpenSource", { path: formatFileCitation(note.file) })}
            className="font-mono text-[0.875em] text-ink underline decoration-n-4 underline-offset-2 hover:decoration-ink"
          >
            {children}
            <sup className="ml-0.5 text-[0.75em] text-n-6 tabular-nums">
              {note.number}
            </sup>
          </button>
        );
      },
      // ```mermaid blocks render as diagrams.
      pre({ node, children, ...rest }) {
        const code = (node as HastNode | undefined)?.children?.[0];
        const classes = code?.properties?.className;
        if (Array.isArray(classes) && classes.includes("language-mermaid")) {
          return <Mermaid chart={hastText(code)} />;
        }
        return <pre {...rest}>{children}</pre>;
      },
    }),
    [notes],
  );

  if (isUser) {
    return (
      <div className="mb-5 border-rail border-l-2 pl-3">
        <p className="whitespace-pre-wrap font-medium text-ink text-sm leading-relaxed">
          {message.content}
        </p>
      </div>
    );
  }

  return (
    <article className="mb-8">
      <p className="mb-1 flex items-center gap-2 font-mono text-n-6 text-xs tabular-nums">
        <span>{t("chatAnswer")}</span>
        <span>{timeFormat.format(message.timestamp)}</span>
      </p>

      {message.content ? (
        <div className={answerProse}>
          <ReactMarkdown
            remarkPlugins={REMARK_PLUGINS}
            rehypePlugins={REHYPE_PLUGINS}
            components={components}
          >
            {message.content}
          </ReactMarkdown>
        </div>
      ) : null}

      {message.isError || message.stopped ? (
        <p className="mt-2 border-ink border-l-2 pl-3 font-mono text-n-7 text-xs">
          {message.isError ? t("chatTurnFailed") : t("chatStopped")}
        </p>
      ) : null}

      {notes && notes.notes.length > 0 ? (
        <AnswerNotesList
          notes={notes.notes}
          filePages={filePages}
          onOpen={setInspected}
          onEvidence={onEvidence}
        />
      ) : null}

      {message.tool_trajectory?.length ? (
        <Trace steps={message.tool_trajectory} />
      ) : null}

      {inspected !== null && notes ? (
        <CodeViewer
          sources={notes.files}
          initialSourceIndex={inspected}
          repoUrl={repoUrl}
          onClose={() => setInspected(null)}
        />
      ) : null}
    </article>
  );
});
