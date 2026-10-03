"use client";

import { X } from "lucide-react";
import dynamic from "next/dynamic";
import { memo, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useCodeHref } from "@/components/wiki/WikiToc";
import { type FileCitation, parseCitations } from "@/lib/citations";
import { t } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import {
  buildMapRows,
  chapterFiles,
  type MapRow,
  maxDirDepth,
  ROOT_DIR,
} from "@/lib/wikiMap";
import {
  slotSvgFill,
  slotSwatch,
  type TocNode,
  type WikiToc,
} from "@/lib/wikiToc";

const CodeViewer = dynamic(() => import("@/components/CodeViewer"), {
  ssr: false,
});

// One square per file; every cell uses the same grid, so cells compare
// directly across chapters (small multiples on a shared scale).
const MARK = 8;
const GAP = 2;
const PER_ROW = 8;
const CELL_WIDTH = PER_ROW * (MARK + GAP) - GAP;

interface CellProps {
  files: string[];
  covered: ReadonlySet<string>;
  fill: string;
  cited: ReadonlySet<string>;
  /** Hovered file, only when it is in this row. */
  hovered: string | null;
  /** Evidence path from a hovered note, only when it is in this row. */
  evidence: string | null;
  label: string;
}

const MapCell = memo(function MapCell({
  files,
  covered,
  fill,
  cited,
  hovered,
  evidence,
  label,
}: CellProps) {
  const lines = Math.ceil(files.length / PER_ROW);
  const height = lines * (MARK + GAP) - GAP;
  const count = files.filter((f) => covered.has(f)).length;
  return (
    <svg
      role="img"
      aria-label={`${label}: ${count}/${files.length}`}
      width={CELL_WIDTH}
      height={height}
      viewBox={`0 0 ${CELL_WIDTH} ${height}`}
      className="block overflow-visible"
    >
      {files.map((file, i) => {
        const x = (i % PER_ROW) * (MARK + GAP);
        const y = Math.floor(i / PER_ROW) * (MARK + GAP);
        const isCovered = covered.has(file);
        return (
          <rect
            key={file}
            data-file={file}
            x={x}
            y={y}
            width={MARK}
            height={MARK}
            rx={1}
            className={cn(
              "cursor-pointer",
              file === evidence
                ? "fill-evidence"
                : isCovered
                  ? fill
                  : "fill-n-2",
              cited.has(file) && "stroke-evidence",
              file === hovered && "stroke-ink",
            )}
            strokeWidth={cited.has(file) || file === hovered ? 1.5 : 0}
          />
        );
      })}
    </svg>
  );
});

interface WikiMapProps {
  toc: WikiToc;
  /** Files cited in the current conversation. */
  citedFiles: ReadonlySet<string>;
  /** Path of a hovered note elsewhere, filled as current evidence. */
  evidencePath: string | null;
  repoUrl?: string;
  onClose: () => void;
}

export default function WikiMap({
  toc,
  citedFiles,
  evidencePath,
  repoUrl,
  onClose,
}: WikiMapProps) {
  const codeHref = useCodeHref();
  const allFiles = useMemo(() => [...toc.byFile.keys()], [toc]);
  const deepest = useMemo(() => maxDirDepth(allFiles), [allFiles]);
  const [depth, setDepth] = useState(() => Math.min(2, deepest));
  const [hovered, setHovered] = useState<string | null>(null);
  const [inspected, setInspected] = useState<FileCitation | null>(null);

  const rows = useMemo(() => buildMapRows(allFiles, depth), [allFiles, depth]);
  const columns = useMemo(
    () =>
      toc.nodes.map((chapter) => ({ chapter, files: chapterFiles(chapter) })),
    [toc],
  );
  const citedCount = allFiles.filter((f) => citedFiles.has(f)).length;

  const fileAt = (target: EventTarget | null) =>
    target instanceof Element ? target.getAttribute("data-file") : null;

  const hoveredPages = hovered ? (toc.byFile.get(hovered) ?? []) : [];
  const rowFile = (row: MapRow, path: string | null) =>
    path && row.files.includes(path) ? path : null;

  return (
    <section
      aria-labelledby="wiki-map-title"
      className="flex min-h-0 min-w-0 flex-1 flex-col"
    >
      <header className="flex shrink-0 flex-wrap items-center gap-x-6 gap-y-2 border-n-3 border-b px-5 py-3 md:px-8">
        <h1
          id="wiki-map-title"
          className="font-semibold font-serif text-ink text-xl"
        >
          {t("mapTitle")}
        </h1>
        <fieldset className="flex items-center gap-1 text-xs">
          <legend className="sr-only">{t("mapDepth")}</legend>
          <span aria-hidden className="mr-1 text-n-6">
            {t("mapDepth")}
          </span>
          {Array.from({ length: deepest }, (_, i) => i + 1).map((level) => (
            <button
              key={level}
              type="button"
              aria-pressed={depth === level}
              onClick={() => setDepth(level)}
              className="h-6 min-w-6 border border-n-3 px-1.5 font-mono text-n-7 tabular-nums hover:border-n-5 aria-pressed:border-ink aria-pressed:bg-ink aria-pressed:text-sheet"
            >
              {level}
            </button>
          ))}
        </fieldset>
        <button
          type="button"
          onClick={onClose}
          title={t("mapClose")}
          className="ml-auto inline-flex items-center gap-1.5 text-n-7 text-sm hover:text-ink"
        >
          <X aria-hidden className="h-4 w-4" />
          {t("mapClose")}
          <kbd className="border border-n-3 px-1 font-mono text-n-6 text-xs">
            M
          </kbd>
        </button>
      </header>

      {/* Direct labels instead of a legend: what a mark means, then the
          hovered file. */}
      <p
        aria-live="polite"
        className="min-h-9 shrink-0 border-n-2 border-b px-5 py-2 text-n-7 text-xs md:px-8"
      >
        {hovered ? (
          <>
            <span className="font-mono text-ink">{hovered}</span>
            <span className="ml-3">
              {hoveredPages.map((page) => page.code).join(" · ")}
            </span>
            {citedFiles.has(hovered) ? (
              <span className="ml-3 text-evidence">{t("mapCitedHere")}</span>
            ) : null}
          </>
        ) : (
          t("mapCaption", {
            files: allFiles.length,
            dirs: rows.length,
            cited: citedCount,
          })
        )}
      </p>

      <div className="min-h-0 flex-1 overflow-auto px-5 pb-10 md:px-8">
        <table
          className="border-separate border-spacing-x-4 border-spacing-y-1.5"
          onPointerLeave={() => setHovered(null)}
        >
          <thead>
            <tr>
              <th
                scope="col"
                className="sticky top-0 left-0 z-20 bg-paper pt-4 pb-2 text-left font-normal text-n-6 text-xs"
              >
                {t("mapDirectory")}
              </th>
              {columns.map(({ chapter, files }) => (
                <th
                  key={chapter.id}
                  scope="col"
                  className="sticky top-0 z-10 bg-paper pt-4 pb-2 text-left align-bottom font-normal"
                  style={{ minWidth: CELL_WIDTH }}
                >
                  <ChapterHeader
                    chapter={chapter}
                    href={codeHref(chapter.code)}
                    count={files.size}
                  />
                </th>
              ))}
            </tr>
          </thead>
          {/* biome-ignore lint/a11y/useKeyWithClickEvents: clicking a square is a pointer shortcut; every file stays keyboard-reachable through the page notes and the Code Inspector */}
          <tbody
            onPointerOver={(event) => setHovered(fileAt(event.target))}
            onClick={(event) => {
              const file = fileAt(event.target);
              if (file) setInspected(parseCitations([file]).files[0] ?? null);
            }}
          >
            {rows.map((row) => (
              <tr key={row.dir}>
                <th
                  scope="row"
                  title={row.dir}
                  className="sticky left-0 z-10 max-w-52 truncate bg-paper py-0.5 pr-2 text-left align-top font-mono font-normal text-n-8 text-xs"
                >
                  {row.dir === ROOT_DIR ? t("mapRoot") : row.dir}
                  <span className="ml-1.5 text-n-6 tabular-nums">
                    {row.files.length}
                  </span>
                </th>
                {columns.map(({ chapter, files }) => (
                  <td key={chapter.id} className="py-0.5 align-top">
                    <MapCell
                      files={row.files}
                      covered={files}
                      fill={slotSvgFill(chapter.slot)}
                      cited={citedFiles}
                      hovered={rowFile(row, hovered)}
                      evidence={rowFile(row, evidencePath)}
                      label={`${chapter.code} ${row.dir}`}
                    />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {inspected && repoUrl ? (
        <CodeViewer
          sources={[inspected]}
          repoUrl={repoUrl}
          onClose={() => setInspected(null)}
        />
      ) : null}
    </section>
  );
}

function ChapterHeader({
  chapter,
  href,
  count,
}: {
  chapter: TocNode;
  href: { search: string };
  count: number;
}) {
  return (
    <Link
      to={href}
      title={chapter.title}
      className="group block text-xs leading-tight"
    >
      <span className="flex items-center gap-1.5 font-mono text-ink tabular-nums group-hover:underline">
        <span aria-hidden className={cn("h-2 w-2", slotSwatch(chapter.slot))} />
        {chapter.code}
      </span>
      <span className="mt-0.5 block font-mono text-n-6 tabular-nums">
        {count}
      </span>
    </Link>
  );
}
