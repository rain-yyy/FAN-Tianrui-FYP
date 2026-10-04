import { cn } from "@/lib/utils";

/** Token-styled Markdown typography shared by wiki pages and chat answers. */
const proseBase = cn(
  "prose prose-invert max-w-none leading-[1.75] text-fg",
  "prose-headings:font-medium prose-headings:tracking-tight prose-headings:text-fg",
  "prose-p:text-fg prose-li:text-fg prose-strong:font-semibold prose-strong:text-fg",
  "prose-a:text-accent prose-a:underline-offset-2",
  "prose-code:rounded-md prose-code:border prose-code:border-line prose-code:bg-raised prose-code:px-1.5 prose-code:py-0.5 prose-code:font-mono prose-code:text-[0.85em] prose-code:font-normal prose-code:text-fg",
  "prose-pre:rounded-xl prose-pre:border prose-pre:border-line prose-pre:bg-raised prose-pre:text-fg",
  "prose-blockquote:border-line-strong prose-blockquote:text-fg-muted",
  "prose-th:text-fg prose-td:text-fg prose-hr:border-line",
);

/** Wiki page body. */
export const readingProse = cn(
  proseBase,
  "text-base leading-[1.7] prose-p:my-3 prose-ul:my-3 prose-li:my-1",
);

/** Chat answers in the narrower docked panel. */
export const answerProse = cn(
  proseBase,
  "prose-sm text-[0.9375rem] prose-p:my-2 prose-ul:my-2 prose-li:my-0.5",
  "prose-headings:mt-4 prose-headings:mb-2 prose-pre:text-xs",
);
