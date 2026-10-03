"use client";

import { ChevronDown, ChevronRight } from "lucide-react";
import { memo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { t } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { slotFill, slotSwatch, type TocNode } from "@/lib/wikiToc";

interface WikiTocProps {
  nodes: TocNode[];
  activeId: string | null;
  activeChapterId: string | null;
  /** Codes only: the collapsed desktop column. */
  compact?: boolean;
  onNavigate?: () => void;
}

/** `?d=<code>` on the current URL, keeping every other parameter. */
function useCodeHref() {
  const [params] = useSearchParams();
  return (code: string) => {
    const next = new URLSearchParams(params);
    next.set("d", code);
    return { search: `?${next.toString()}` };
  };
}

function TocRow({
  node,
  active,
  compact,
  href,
  onNavigate,
}: {
  node: TocNode;
  active: boolean;
  compact: boolean;
  href: { search: string };
  onNavigate?: () => void;
}) {
  return (
    <Link
      to={href}
      onClick={onNavigate}
      aria-current={active ? "page" : undefined}
      title={compact ? `${node.code} ${node.title}` : undefined}
      className={cn(
        "flex min-w-0 flex-1 items-baseline gap-2 py-1 pr-2 text-sm leading-snug",
        compact ? "justify-center pl-0" : "pl-2",
        active ? slotFill(node.slot) : "text-n-8 hover:bg-n-1 hover:text-ink",
      )}
    >
      {active ? null : (
        <span
          aria-hidden
          className={cn("h-2 w-2 shrink-0 self-center", slotSwatch(node.slot))}
        />
      )}
      <span className="shrink-0 font-mono text-xs tabular-nums">
        {node.code}
      </span>
      {compact ? null : <span className="min-w-0 truncate">{node.title}</span>}
    </Link>
  );
}

function PageList({
  nodes,
  activeId,
  codeHref,
  onNavigate,
}: {
  nodes: TocNode[];
  activeId: string | null;
  codeHref: (code: string) => { search: string };
  onNavigate?: () => void;
}) {
  return (
    <ol className="mt-px ml-5 space-y-px border-n-2 border-l pl-2">
      {nodes.map((node) => (
        <li key={node.id}>
          <div className="flex">
            <TocRow
              node={node}
              active={node.id === activeId}
              compact={false}
              href={codeHref(node.code)}
              onNavigate={onNavigate}
            />
          </div>
          {node.children.length > 0 ? (
            <PageList
              nodes={node.children}
              activeId={activeId}
              codeHref={codeHref}
              onNavigate={onNavigate}
            />
          ) : null}
        </li>
      ))}
    </ol>
  );
}

export const WikiToc = memo(function WikiToc({
  nodes,
  activeId,
  activeChapterId,
  compact = false,
  onNavigate,
}: WikiTocProps) {
  const codeHref = useCodeHref();
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(
    () => new Set(),
  );

  const toggle = (id: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    <ol className="space-y-px">
      {nodes.map((chapter) => {
        const hasChildren = chapter.children.length > 0;
        const open = hasChildren && !compact && !collapsed.has(chapter.id);
        const Chevron = open ? ChevronDown : ChevronRight;
        return (
          <li key={chapter.id}>
            <div className="flex items-stretch">
              {compact ? null : hasChildren ? (
                <button
                  type="button"
                  onClick={() => toggle(chapter.id)}
                  aria-expanded={open}
                  aria-label={t("wikiToggleChapter", { title: chapter.title })}
                  className="flex w-5 shrink-0 items-center justify-center text-n-6 hover:text-ink"
                >
                  <Chevron aria-hidden className="h-3.5 w-3.5" />
                </button>
              ) : (
                <span aria-hidden className="w-5 shrink-0" />
              )}
              <TocRow
                node={chapter}
                active={
                  chapter.id === activeId ||
                  // Codes-only column: the chapter stands in for its pages.
                  (compact && activeChapterId === chapter.id)
                }
                compact={compact}
                href={codeHref(chapter.code)}
                onNavigate={onNavigate}
              />
            </div>
            {open ? (
              <PageList
                nodes={chapter.children}
                activeId={activeId}
                codeHref={codeHref}
                onNavigate={onNavigate}
              />
            ) : null}
          </li>
        );
      })}
    </ol>
  );
});
