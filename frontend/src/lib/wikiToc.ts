/**
 * Wiki table of contents with stable chapter codes.
 *
 * Top-level chapters get D1…Dn and their pages Dn.m. The code is the shared
 * name for a chapter across the TOC, margin notes, chips, the map and the URL
 * (`?d=D3.2`). The directory color comes from the top-level chapter: slots
 * 1–7 in order, then everything folds into `other` (never cycled).
 */

type DirSlot = 1 | 2 | 3 | 4 | 5 | 6 | 7 | "other";

export interface TocNode {
  id: string;
  title: string;
  /** Repo-relative files the generator attached to this page. */
  files: string[];
  code: string;
  slot: DirSlot;
  /** Top-level chapter this node belongs to (itself for a chapter). */
  chapterId: string;
  children: TocNode[];
}

export interface WikiToc {
  title: string;
  nodes: TocNode[];
  byId: Map<string, TocNode>;
  byCode: Map<string, TocNode>;
  /** Depth-first reading order. */
  order: TocNode[];
  /** Repo-relative path → every page that lists it, in reading order. */
  byFile: Map<string, TocNode[]>;
}

const DIR_SLOTS = 7;

/** Literal class names so Tailwind can see them. */
const SLOT_CLASSES: Record<
  DirSlot,
  { fill: string; swatch: string; svg: string }
> = {
  1: { fill: "bg-dir-1 text-dir-1-on", swatch: "bg-dir-1", svg: "fill-dir-1" },
  2: { fill: "bg-dir-2 text-dir-2-on", swatch: "bg-dir-2", svg: "fill-dir-2" },
  3: { fill: "bg-dir-3 text-dir-3-on", swatch: "bg-dir-3", svg: "fill-dir-3" },
  4: { fill: "bg-dir-4 text-dir-4-on", swatch: "bg-dir-4", svg: "fill-dir-4" },
  5: { fill: "bg-dir-5 text-dir-5-on", swatch: "bg-dir-5", svg: "fill-dir-5" },
  6: { fill: "bg-dir-6 text-dir-6-on", swatch: "bg-dir-6", svg: "fill-dir-6" },
  7: { fill: "bg-dir-7 text-dir-7-on", swatch: "bg-dir-7", svg: "fill-dir-7" },
  other: {
    fill: "bg-dir-other text-dir-other-on",
    swatch: "bg-dir-other",
    svg: "fill-dir-other",
  },
};

/** Solid selected fill with its matching text color. */
export const slotFill = (slot: DirSlot) => SLOT_CLASSES[slot].fill;
/** Small identity mark; always shown next to the code, never alone. */
export const slotSwatch = (slot: DirSlot) => SLOT_CLASSES[slot].swatch;
/** SVG fill for map marks. */
export const slotSvgFill = (slot: DirSlot) => SLOT_CLASSES[slot].svg;

interface RawTocItem {
  id?: unknown;
  section_id?: unknown;
  filename?: unknown;
  title?: unknown;
  name?: unknown;
  files?: unknown;
  children?: unknown;
}

const asString = (value: unknown) =>
  typeof value === "string" && value.trim() ? value.trim() : null;

function rawItems(data: unknown): RawTocItem[] {
  if (Array.isArray(data)) return data as RawTocItem[];
  if (data && typeof data === "object") {
    const record = data as { toc?: unknown; pages?: unknown };
    if (Array.isArray(record.toc)) return record.toc as RawTocItem[];
    if (Array.isArray(record.pages)) return record.pages as RawTocItem[];
  }
  return [];
}

function buildNodes(
  items: RawTocItem[],
  prefix: string,
  top: { slot: DirSlot; chapterId: string } | null,
): TocNode[] {
  const nodes: TocNode[] = [];
  for (const item of items) {
    const id =
      asString(item.id) ??
      asString(item.section_id) ??
      asString(item.filename)?.replace(/\.json$/, "");
    if (!id) continue;
    const position = nodes.length + 1;
    const code = top ? `${prefix}.${position}` : `D${position}`;
    const own = top ?? {
      slot: position <= DIR_SLOTS ? (position as DirSlot) : "other",
      chapterId: id,
    };
    nodes.push({
      id,
      title: asString(item.title) ?? asString(item.name) ?? id,
      files: Array.isArray(item.files)
        ? item.files.filter((f): f is string => typeof f === "string")
        : [],
      code,
      slot: own.slot,
      chapterId: own.chapterId,
      children: Array.isArray(item.children)
        ? buildNodes(item.children as RawTocItem[], code, own)
        : [],
    });
  }
  return nodes;
}

/** Parses `wiki_structure.json` (`{title, toc: [...]}` or a bare array). */
export function parseWikiToc(data: unknown): WikiToc {
  const nodes = buildNodes(rawItems(data), "", null);
  const byId = new Map<string, TocNode>();
  const byCode = new Map<string, TocNode>();
  const order: TocNode[] = [];
  const byFile = new Map<string, TocNode[]>();
  const walk = (list: TocNode[]) => {
    for (const node of list) {
      byId.set(node.id, node);
      byCode.set(node.code, node);
      order.push(node);
      for (const file of node.files) {
        const pages = byFile.get(file);
        if (pages) pages.push(node);
        else byFile.set(file, [node]);
      }
      walk(node.children);
    }
  };
  walk(nodes);
  const title =
    data && typeof data === "object" && !Array.isArray(data)
      ? asString((data as { title?: unknown }).title)
      : null;
  return { title: title ?? "", nodes, byId, byCode, order, byFile };
}
