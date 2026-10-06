"use client";

import { Link } from "react-router-dom";
import { t } from "@/lib/i18n";
import { cn } from "@/lib/utils";

const timeFormat = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "short",
});

function formatTime(value: string | null | undefined): string | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : timeFormat.format(date);
}

const STATUS_TONE: Record<string, string> = {
  pending: "border-info/40 text-info",
  processing: "border-accent-line text-accent",
  completed: "border-ok/40 text-ok",
  cached: "border-ok/40 text-ok",
  cancelled: "border-warn/40 text-warn",
  failed: "border-danger/40 text-danger",
  error: "border-danger/40 text-danger",
};

/** Status label: a coloured pill. States are always shown, never hidden. */
export function StatusStamp({ status }: { status: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-2 rounded-full border px-3 py-1 font-mono text-xs uppercase leading-none tracking-wider",
        STATUS_TONE[status] ?? "border-line text-fg-muted",
      )}
    >
      <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-current" />
      {status}
    </span>
  );
}

interface TaskMeta {
  taskId?: string;
  repo?: string;
  createdAt?: string | null;
  updatedAt?: string | null;
}

interface TaskStatePanelProps {
  /** Raw status string from the API, or a local state label. */
  stamp: string;
  title: string;
  /** Free text shown verbatim (e.g. backend `current_step` or `error`). */
  detail?: string | null;
  detailIsError?: boolean;
  /** 0–100, from the backend. Omit when there is no progress to show. */
  progress?: number | null;
  meta?: TaskMeta;
  actions?: React.ReactNode;
}

export function TaskStatePanel({
  stamp,
  title,
  detail,
  detailIsError = false,
  progress,
  meta,
  actions,
}: TaskStatePanelProps) {
  const pct =
    typeof progress === "number" && Number.isFinite(progress)
      ? Math.min(100, Math.max(0, progress))
      : null;
  const created = formatTime(meta?.createdAt);
  const updated = formatTime(meta?.updatedAt);

  return (
    <section
      aria-labelledby="task-state-title"
      className="mx-4 my-10 rounded-2xl border border-line bg-panel p-8 sm:mx-auto sm:w-full sm:max-w-xl md:my-16"
    >
      <StatusStamp status={stamp} />

      <h1
        id="task-state-title"
        className="mt-5 font-medium text-2xl text-fg leading-tight tracking-tight"
      >
        {title}
      </h1>

      {meta?.repo ? (
        <p className="mt-2 font-mono text-sm text-fg-muted">{meta.repo}</p>
      ) : null}

      {pct !== null ? (
        <div className="mt-8">
          <div className="flex items-baseline justify-between gap-4 text-sm">
            <span className="text-fg-muted">{detail ?? t("taskWaiting")}</span>
            <span className="font-mono tabular-nums text-fg">
              {Math.round(pct)}%
            </span>
          </div>
          <div
            role="progressbar"
            aria-label={t("taskProgress")}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(pct)}
            className="mt-2 h-1 w-full overflow-hidden rounded-full bg-raised"
          >
            <div
              className="h-full rounded-full bg-accent-strong transition-[width] duration-500"
              style={{ width: `${pct}%` }}
            />
          </div>
        </div>
      ) : null}

      {pct === null && detail ? (
        <p
          className={cn(
            "mt-6 whitespace-pre-wrap break-words text-sm leading-relaxed",
            detailIsError
              ? "rounded-xl border border-danger/40 bg-raised px-4 py-3 font-mono text-fg"
              : "text-fg-muted",
          )}
        >
          {detail}
        </p>
      ) : null}

      {actions ? (
        <div className="mt-8 flex flex-wrap items-center gap-3">{actions}</div>
      ) : null}

      {meta?.taskId || created || updated ? (
        <dl className="mt-12 grid grid-cols-[auto_1fr] gap-x-6 gap-y-1 border-line border-t pt-4 text-xs text-fg-muted">
          {meta?.taskId ? (
            <>
              <dt>{t("taskIdLabel")}</dt>
              <dd className="truncate font-mono">{meta.taskId}</dd>
            </>
          ) : null}
          {created ? (
            <>
              <dt>{t("taskCreatedLabel")}</dt>
              <dd className="font-mono tabular-nums">{created}</dd>
            </>
          ) : null}
          {updated ? (
            <>
              <dt>{t("taskUpdatedLabel")}</dt>
              <dd className="font-mono tabular-nums">{updated}</dd>
            </>
          ) : null}
        </dl>
      ) : null}
    </section>
  );
}

export const primaryActionClass =
  "inline-flex h-9 items-center gap-2 rounded-full bg-accent-strong px-4 text-sm font-medium text-accent-fg hover:bg-accent disabled:opacity-60";
export const secondaryActionClass =
  "inline-flex h-9 items-center gap-2 rounded-full border border-line px-4 text-sm text-fg hover:border-line-strong";

export function BackToReposLink() {
  return (
    <Link to="/app/dashboard" className={secondaryActionClass}>
      {t("taskBackToRepos")}
    </Link>
  );
}
