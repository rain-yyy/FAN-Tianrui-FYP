/**
 * Data for the repository map: directories (rows) × top-level chapters
 * (columns). There is no repo-tree endpoint, so the map covers the files the
 * wiki pages list (`toc.byFile`).
 */

import type { TocNode } from "@/lib/wikiToc";

export interface MapRow {
  /** Directory at the chosen depth, e.g. `hugolib` or `markup/goldmark`. */
  dir: string;
  /** Repo-relative paths under `dir`, sorted. */
  files: string[];
}

export const ROOT_DIR = ".";

const dirSegments = (path: string) => path.split("/").slice(0, -1);

/** Deepest directory level among the files (at least 1). */
export function maxDirDepth(files: Iterable<string>): number {
  let depth = 1;
  for (const file of files) {
    depth = Math.max(depth, dirSegments(file).length);
  }
  return depth;
}

/** Groups files by their directory, cut to `depth` levels. */
export function buildMapRows(files: Iterable<string>, depth: number): MapRow[] {
  const rows = new Map<string, string[]>();
  for (const file of files) {
    const dir = dirSegments(file).slice(0, depth).join("/") || ROOT_DIR;
    const list = rows.get(dir);
    if (list) list.push(file);
    else rows.set(dir, [file]);
  }
  return [...rows]
    .sort(([a], [b]) =>
      a === ROOT_DIR ? -1 : b === ROOT_DIR ? 1 : a.localeCompare(b),
    )
    .map(([dir, list]) => ({ dir, files: list.sort() }));
}

/** Every file listed by a chapter or any of its pages. */
export function chapterFiles(chapter: TocNode): Set<string> {
  const files = new Set<string>();
  const walk = (node: TocNode) => {
    for (const file of node.files) files.add(file);
    for (const child of node.children) walk(child);
  };
  walk(chapter);
  return files;
}
