/**
 * Numbered evidence notes for one chat answer: the files and links the
 * backend cited, plus file paths the answer itself mentions in inline code.
 */

import type { ToolTrajectoryStep } from "@/lib/api";
import {
  type FileCitation,
  formatFileCitation,
  parseCitations,
  type WebCitation,
} from "@/lib/citations";

export interface FileNote {
  kind: "file";
  number: number;
  /** Index into `AnswerNotes.files` (shared with the Code Inspector). */
  fileIndex: number;
  file: FileCitation;
  /** Tool that surfaced the file, when it can be told. */
  tool?: string;
}

export interface LinkNote {
  kind: "web";
  number: number;
  link: WebCitation;
}

export type AnswerNote = FileNote | LinkNote;

export interface AnswerNotes {
  notes: AnswerNote[];
  files: FileCitation[];
  /** Inline-code text in the answer → the file note it names. */
  byMention: Map<string, FileNote>;
}

const FENCED_BLOCK = /```[\s\S]*?(```|$)/g;
const INLINE_CODE = /`([^`\n]+)`/g;
// Something that looks like a file: optional dirs, a name with an extension,
// optional :line or :start-end.
const FILE_LIKE = /^[\w@.\-/]+\.[A-Za-z0-9]{1,8}(:\d+(-\d+)?)?$/;

/** Tool attribution from the trajectory (file arguments) or the raw prefix. */
function toolFor(
  file: FileCitation,
  trajectory: readonly ToolTrajectoryStep[],
): string | undefined {
  for (const step of trajectory) {
    const arg = step.arguments?.file_path;
    if (typeof arg !== "string") continue;
    const argPath = parseCitations([arg]).files[0]?.path;
    if (argPath === file.path) return step.tool;
  }
  if (/^(code|text|doc):/i.test(file.raw)) return "rag_search";
  return undefined;
}

export function buildAnswerNotes(
  content: string,
  sources: readonly string[] | undefined,
  trajectory: readonly ToolTrajectoryStep[] | undefined,
): AnswerNotes {
  const cited = parseCitations(sources);
  const files: FileCitation[] = [...cited.files];
  const keys = new Set(files.map(formatFileCitation));
  const byMention = new Map<string, number>();

  // Inline paths in the answer: link to a cited file, or add a new note.
  const prose = content.replace(FENCED_BLOCK, "");
  for (const match of prose.matchAll(INLINE_CODE)) {
    const text = match[1].trim();
    if (byMention.has(text) || !FILE_LIKE.test(text)) continue;
    const mentioned = parseCitations([text]).files[0];
    if (!mentioned) continue;

    let index = -1;
    if (!mentioned.path.includes("/")) {
      // A bare file name only counts when exactly one cited file matches.
      const sameName = files.filter((f) => f.fileName === mentioned.fileName);
      if (sameName.length === 1) index = files.indexOf(sameName[0]);
    } else {
      const key = formatFileCitation(mentioned);
      index = files.findIndex(
        (f) =>
          formatFileCitation(f) === key ||
          (!mentioned.lines && f.path === mentioned.path),
      );
      if (index < 0 && !keys.has(key)) {
        files.push(mentioned);
        keys.add(key);
        index = files.length - 1;
      }
    }
    if (index >= 0) byMention.set(text, index);
  }

  const steps = trajectory ?? [];
  const fileNotes: FileNote[] = files.map((file, fileIndex) => ({
    kind: "file",
    number: fileIndex + 1,
    fileIndex,
    file,
    tool: toolFor(file, steps),
  }));
  const linkNotes: LinkNote[] = cited.links.map((link, i) => ({
    kind: "web",
    number: files.length + i + 1,
    link,
  }));

  return {
    notes: [...fileNotes, ...linkNotes],
    files,
    byMention: new Map(
      [...byMention].map(([text, index]) => [text, fileNotes[index]]),
    ),
  };
}
