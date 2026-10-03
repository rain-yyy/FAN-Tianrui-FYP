"use client";

import { Code2, Maximize2, RefreshCw, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useModalDialog } from "@/hooks/useModalDialog";
import { t } from "@/lib/i18n";

type MermaidApi = typeof import("mermaid").default;

let mermaidReady: Promise<MermaidApi> | null = null;

/** Loads and configures mermaid once; every diagram awaits the same promise. */
function loadMermaid(): Promise<MermaidApi> {
  if (!mermaidReady) {
    mermaidReady = import("mermaid")
      .then(({ default: mermaid }) => {
        // Mermaid needs literal colors, so read the design tokens once.
        const style = getComputedStyle(document.documentElement);
        const token = (name: string) =>
          style.getPropertyValue(`--color-${name}`).trim();
        mermaid.initialize({
          startOnLoad: false,
          suppressErrorRendering: true,
          securityLevel: "strict",
          theme: "base",
          fontFamily: style.getPropertyValue("--font-sans").trim(),
          themeVariables: {
            background: token("raised"),
            primaryColor: token("panel"),
            primaryTextColor: token("fg"),
            primaryBorderColor: token("fg-faint"),
            secondaryColor: token("panel"),
            tertiaryColor: token("panel"),
            lineColor: token("fg-muted"),
            textColor: token("fg"),
            clusterBkg: token("panel"),
            clusterBorder: token("line-strong"),
            edgeLabelBackground: token("raised"),
            fontSize: "14px",
          },
          flowchart: { htmlLabels: true, curve: "basis" },
        });
        return mermaid;
      })
      .catch((error: unknown) => {
        mermaidReady = null; // allow a later retry
        throw error;
      });
  }
  return mermaidReady;
}

const DIAGRAM_TYPES = [
  "graph",
  "flowchart",
  "sequencediagram",
  "classdiagram",
  "statediagram",
  "erdiagram",
  "gantt",
  "pie",
  "journey",
  "mindmap",
  "timeline",
  "gitgraph",
  "quadrantchart",
  "sankey",
  "xychart",
  "block-beta",
];

/** Strips a surrounding ``` fence the generator sometimes leaves in. */
function cleanChart(code: string): string {
  return code
    .trim()
    .replace(/^```(?:mermaid)?\s*/, "")
    .replace(/```$/, "")
    .trim();
}

function hasDiagramType(code: string): boolean {
  const firstLine = code.split("\n")[0].trim().toLowerCase();
  return DIAGRAM_TYPES.some((type) => firstLine.startsWith(type));
}

const NODE_LABEL = /(\w+)\[([^\]"]*)\]/g;

/**
 * Quotes `[...]` labels containing characters that are syntax in Mermaid,
 * a common failure in generated diagrams. `aggressive` also escapes quotes
 * and angle brackets.
 */
function quoteLabels(code: string, aggressive: boolean): string {
  const special = aggressive ? /[()@<>#;&,:.`'"]/ : /[()@<>#;&,:.`']/;
  return code.replace(NODE_LABEL, (match, id: string, label: string) => {
    if (!special.test(label)) return match;
    let safe = label.replace(/"/g, "'");
    if (aggressive) safe = safe.replace(/</g, "&lt;").replace(/>/g, "&gt;");
    return `${id}["${safe}"]`;
  });
}

let renderSeq = 0;

async function renderChart(code: string): Promise<string> {
  const mermaid = await loadMermaid();
  try {
    return (
      await mermaid.render(`mmd-${++renderSeq}`, quoteLabels(code, false))
    ).svg;
  } catch {
    return (await mermaid.render(`mmd-${++renderSeq}`, quoteLabels(code, true)))
      .svg;
  }
}

type RenderState =
  | { status: "loading" }
  | { status: "ready"; svg: string }
  | { status: "error"; message: string };

const smallButtonClass =
  "inline-flex items-center gap-1.5 rounded-full border border-line px-3 py-1 text-fg-muted text-xs transition-colors hover:border-line-strong hover:text-fg";

function ZoomedDiagram({ svg, onClose }: { svg: string; onClose: () => void }) {
  const dialogProps = useModalDialog(onClose);
  return (
    <dialog
      {...dialogProps}
      aria-label={t("wikiDiagram")}
      className="m-0 h-dvh max-h-none w-full max-w-none bg-panel p-0 text-fg backdrop:bg-bg/80"
    >
      <div className="flex h-full flex-col">
        <div className="flex h-12 shrink-0 items-center justify-end border-line border-b px-4">
          <button
            type="button"
            onClick={onClose}
            aria-label={t("diagramClose")}
            className="-mr-2 p-2 text-fg-muted hover:text-fg"
          >
            <X aria-hidden className="h-5 w-5" />
          </button>
        </div>
        <div
          className="min-h-0 flex-1 overflow-auto p-8 [&_svg]:mx-auto [&_svg]:h-auto [&_svg]:w-full [&_svg]:max-w-none"
          // biome-ignore lint/security/noDangerouslySetInnerHtml: SVG produced by mermaid with securityLevel "strict"
          dangerouslySetInnerHTML={{ __html: svg }}
        />
      </div>
    </dialog>
  );
}

export default function Mermaid({ chart }: { chart: string }) {
  const code = useMemo(() => cleanChart(chart), [chart]);
  const valid = code !== "" && hasDiagramType(code);
  const [state, setState] = useState<RenderState>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [showSource, setShowSource] = useState(false);
  const [zoomed, setZoomed] = useState(false);
  const closeZoom = useCallback(() => setZoomed(false), []);

  // biome-ignore lint/correctness/useExhaustiveDependencies: `attempt` re-runs the render on Retry
  useEffect(() => {
    if (!valid) return;
    let cancelled = false;
    setState({ status: "loading" });
    renderChart(code)
      .then((svg) => {
        if (!cancelled) setState({ status: "ready", svg });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        const message = error instanceof Error ? error.message : String(error);
        setState({ status: "error", message: message.slice(0, 200) });
      });
    return () => {
      cancelled = true;
    };
  }, [code, valid, attempt]);

  const current: RenderState = valid
    ? state
    : { status: "error", message: t("diagramMissingType") };

  if (current.status === "loading") {
    return (
      <div className="flex min-h-40 items-center justify-center rounded-xl border border-line bg-raised text-fg-muted text-sm">
        {t("diagramRendering")}
      </div>
    );
  }

  if (current.status === "error") {
    return (
      <div className="rounded-xl border border-line bg-raised p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-fg text-sm">{t("diagramFailed")}</p>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => setShowSource((prev) => !prev)}
              aria-expanded={showSource}
              className={smallButtonClass}
            >
              <Code2 aria-hidden className="h-3.5 w-3.5" />
              {showSource ? t("diagramHideSource") : t("diagramShowSource")}
            </button>
            {valid ? (
              <button
                type="button"
                onClick={() => setAttempt((prev) => prev + 1)}
                className={smallButtonClass}
              >
                <RefreshCw aria-hidden className="h-3.5 w-3.5" />
                {t("retry")}
              </button>
            ) : null}
          </div>
        </div>
        {showSource ? (
          <>
            <pre className="mt-3 max-h-52 overflow-auto rounded-lg border border-line bg-bg p-3 font-mono text-fg-muted text-xs">
              <code>{code}</code>
            </pre>
            <p className="mt-2 font-mono text-fg-faint text-xs">
              {current.message}
            </p>
          </>
        ) : null}
      </div>
    );
  }

  return (
    <>
      <div className="relative rounded-xl border border-line bg-raised">
        <div
          className="flex justify-center overflow-x-auto p-6"
          // biome-ignore lint/security/noDangerouslySetInnerHtml: SVG produced by mermaid with securityLevel "strict"
          dangerouslySetInnerHTML={{ __html: current.svg }}
        />
        <button
          type="button"
          onClick={() => setZoomed(true)}
          aria-label={t("diagramEnlarge")}
          title={t("diagramEnlarge")}
          className="absolute right-2 bottom-2 rounded-lg p-1.5 text-fg-muted transition-colors hover:bg-overlay hover:text-fg"
        >
          <Maximize2 aria-hidden className="h-4 w-4" />
        </button>
      </div>
      {zoomed ? <ZoomedDiagram svg={current.svg} onClose={closeZoom} /> : null}
    </>
  );
}
