import { cn } from "@/lib/utils";

/** Token-styled Markdown typography shared by wiki pages and chat answers. */
const proseBase = cn(
  "prose max-w-none font-serif leading-relaxed text-ink",
  "prose-headings:font-serif prose-headings:font-semibold prose-headings:text-ink",
  "prose-p:text-ink prose-li:text-ink prose-strong:text-ink",
  "prose-a:text-rail prose-a:underline-offset-2",
  "prose-code:font-mono prose-code:text-[0.875em] prose-code:font-normal prose-code:text-ink",
  "prose-code:before:content-none prose-code:after:content-none",
  "prose-pre:rounded-none prose-pre:border prose-pre:border-n-3 prose-pre:bg-sheet prose-pre:text-ink",
  "prose-blockquote:border-n-4 prose-blockquote:text-n-8",
  "prose-th:text-ink prose-td:text-ink prose-hr:border-n-3",
);

/** Wiki page body. */
export const readingProse = cn(proseBase, "text-[1.0625rem]");

/** Chat answers in the narrower docked panel. */
export const answerProse = cn(
  proseBase,
  "prose-sm text-[0.9375rem] prose-p:my-2 prose-ul:my-2 prose-li:my-0.5",
  "prose-headings:mt-4 prose-headings:mb-2 prose-pre:text-xs",
);
