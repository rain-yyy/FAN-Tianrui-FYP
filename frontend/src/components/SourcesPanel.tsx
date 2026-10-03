"use client";

import { ExternalLink, FileCode, FileText } from "lucide-react";
import {
  type FileCitation,
  formatFileCitation,
  type WebCitation,
} from "@/lib/citations";

const CODE_EXTENSIONS = new Set([
  "py",
  "ts",
  "tsx",
  "js",
  "jsx",
  "java",
  "cpp",
  "c",
  "go",
  "rs",
  "rb",
  "php",
]);

interface SourcesPanelProps {
  files: FileCitation[];
  links: WebCitation[];
  /** Index into `files` — the same array the Code Inspector receives. */
  onOpenFile: (index: number) => void;
}

function hostOf(url: string): string {
  try {
    return new URL(url).hostname;
  } catch {
    return url;
  }
}

const chipClass =
  "flex max-w-full items-center gap-1.5 border border-n-3 bg-sheet px-2 py-1 font-mono text-xs text-ink hover:border-n-5";

export default function SourcesPanel({
  files,
  links,
  onOpenFile,
}: SourcesPanelProps) {
  if (files.length === 0 && links.length === 0) return null;

  return (
    <ul className="flex flex-wrap gap-2">
      {files.map((file, index) => {
        const Icon = CODE_EXTENSIONS.has(file.extension) ? FileCode : FileText;
        const label = formatFileCitation(file);
        return (
          <li key={label} className="min-w-0">
            <button
              type="button"
              onClick={() => onOpenFile(index)}
              title={label}
              className={chipClass}
            >
              <Icon aria-hidden className="h-3.5 w-3.5 shrink-0 text-n-6" />
              <span className="truncate">{file.fileName}</span>
              {file.lines ? (
                <span className="text-n-6 tabular-nums">
                  :{file.lines.start}
                </span>
              ) : null}
            </button>
          </li>
        );
      })}
      {links.map((link) => (
        <li key={link.url} className="min-w-0">
          <a
            href={link.url}
            target="_blank"
            rel="noreferrer"
            title={link.url}
            className={chipClass}
          >
            <ExternalLink
              aria-hidden
              className="h-3.5 w-3.5 shrink-0 text-n-6"
            />
            <span className="truncate">{hostOf(link.url)}</span>
          </a>
        </li>
      ))}
    </ul>
  );
}
