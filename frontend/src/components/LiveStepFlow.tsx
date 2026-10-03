"use client";

import { t } from "@/lib/i18n";
import { toolLabel } from "@/lib/toolDescriptions";
import { cn } from "@/lib/utils";

export interface LiveStep {
  id: string;
  type: "iteration" | "tool";
  status: "pending" | "running" | "done" | "error";
  title: string;
  /** Tool identifier (e.g. "rag_search"). */
  toolName?: string;
}

interface LiveStepFlowProps {
  steps: LiveStep[];
  streamingAnswer: string;
}

const STATUS_KEY = {
  pending: "chatStepPending",
  running: "chatStepRunning",
  done: "chatStepDone",
  error: "chatStepFailed",
} as const;

/** The turn in progress: tool steps as a trace, then the streamed text. */
export function LiveStepFlow({ steps, streamingAnswer }: LiveStepFlowProps) {
  // Iterations are bookkeeping; show the step counter once, not as rows.
  const iteration = steps.findLast((s) => s.type === "iteration");
  const tools = steps.filter((s) => s.type === "tool");

  return (
    <div className="mb-8">
      <p className="mb-2 flex items-center gap-2 font-mono text-fg-muted text-xs">
        <span
          aria-hidden
          className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent-strong"
        />
        <span>{iteration?.title ?? t("agentWorking")}</span>
      </p>

      {tools.length > 0 ? (
        <ol className="space-y-1 border-line border-l pl-3">
          {tools.map((step) => (
            <li key={step.id} className="flex gap-2 text-xs leading-snug">
              <span className="w-12 shrink-0 font-mono text-fg-muted">
                {step.toolName ? toolLabel(step.toolName) : ""}
              </span>
              <span className="min-w-0 flex-1 text-fg">{step.title}</span>
              <span
                className={cn(
                  "shrink-0 font-mono",
                  step.status === "error" ? "text-danger" : "text-fg-muted",
                )}
              >
                {t(STATUS_KEY[step.status])}
              </span>
            </li>
          ))}
        </ol>
      ) : null}

      {streamingAnswer ? (
        // Plain text while streaming; Markdown renders once the turn is done.
        <p className="mt-3 whitespace-pre-wrap font-sans text-[0.9375rem] text-fg leading-relaxed">
          {streamingAnswer}
        </p>
      ) : null}
    </div>
  );
}
