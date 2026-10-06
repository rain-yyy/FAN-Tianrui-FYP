/**
 * Places a page's source files as numbered margin notes next to the block
 * of text that first mentions them.
 */

import type { FileCitation } from "@/lib/citations";

export interface MarginNote {
  /** 1-based number shown in the margin and on inline markers. */
  number: number;
  file: FileCitation;
  /** Index into the page's `files` array (shared with the Code Inspector). */
  fileIndex: number;
}

interface PlacedNotes {
  /** Notes per block, where block 0 is the intro and block i+1 section i. */
  byBlock: MarginNote[][];
  /** Inline-code text (full path, or a basename unique on this page) → note. */
  byMention: Map<string, MarginNote>;
}

/**
 * A file belongs to the first block that mentions its path, or its basename
 * when no other file on the page shares it. Unmentioned files go with the
 * intro. Numbers follow reading order.
 */
export function placeNotes(
  files: FileCitation[],
  blocks: string[],
): PlacedNotes {
  const baseCounts = new Map<string, number>();
  for (const file of files) {
    baseCounts.set(file.fileName, (baseCounts.get(file.fileName) ?? 0) + 1);
  }
  const uniqueBase = (file: FileCitation) =>
    file.fileName.length > 3 && baseCounts.get(file.fileName) === 1;

  const located = files.map((file, fileIndex) => {
    for (let block = 0; block < blocks.length; block++) {
      let at = blocks[block].indexOf(file.path);
      if (at < 0 && uniqueBase(file)) at = blocks[block].indexOf(file.fileName);
      if (at >= 0) return { file, fileIndex, block, at };
    }
    return { file, fileIndex, block: 0, at: Number.MAX_SAFE_INTEGER };
  });
  located.sort((a, b) => a.block - b.block || a.at - b.at);

  const byBlock: MarginNote[][] = Array.from(
    { length: Math.max(1, blocks.length) },
    () => [],
  );
  const byMention = new Map<string, MarginNote>();
  located.forEach(({ file, fileIndex, block }, index) => {
    const note = { number: index + 1, file, fileIndex };
    byBlock[block].push(note);
    byMention.set(file.path, note);
    if (uniqueBase(file)) byMention.set(file.fileName, note);
  });
  return { byBlock, byMention };
}
