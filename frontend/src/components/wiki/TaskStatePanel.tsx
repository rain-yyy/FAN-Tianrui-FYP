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

/** Status stamp: a bordered mono label. States are stamped, never hidden. */
function StatusStamp({ status }: { status: string }) {
  return (
    <span className="inline-block border border-current px-1.5 py-0.5 font-mono text-xs uppercase leading-none tracking-wider">
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
  live?: boolean;
  meta?: TaskMeta;
  actions?: React.ReactNode;
}

export function TaskStatePanel({
  stamp,
  title,
  detail,
  detailIsError = false,
  progress,
  live = false,
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
      className="mx-auto w-full max-w-measure py-16 md:py-24"
    >
      <div className={cn(live ? "text-rail" : "text-ink")}>
        <StatusStamp status={stamp} />
      </div>

      <h1
        id="task-state-title"
        className="mt-5 font-serif text-3xl leading-tight text-ink"
      >
        {title}
      </h1>

      {meta?.repo ? (
        <p className="mt-2 font-mono text-sm text-n-6">{meta.repo}</p>
      ) : null}

      {pct !== null ? (
        <div className="mt-8">
          <div className="flex items-baseline justify-between gap-4 text-sm">
            <span className="text-n-7">{detail ?? t("taskWaiting")}</span>
            <span className="font-mono tabular-nums text-ink">
              {Math.round(pct)}%
            </span>
          </div>
          <div
            role="progressbar"
            aria-label={t("taskProgress")}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(pct)}
            className="mt-2 h-px w-full bg-n-3"
          >
            <div
              className="-mt-px h-[3px] bg-rail transition-[width] duration-500"
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
              ? "border-ink border-l-2 bg-sheet py-3 pr-4 pl-4 font-mono text-ink"
              : "text-n-7",
          )}
        >
          {detail}
        </p>
      ) : null}

      {live ? (
        <p aria-live="polite" className="sr-only">
          {detail ? `${detail}, ${Math.round(pct ?? 0)}%` : null}
        </p>
      ) : null}

      {actions ? (
        <div className="mt-8 flex flex-wrap items-center gap-3">{actions}</div>
      ) : null}

      {meta?.taskId || created || updated ? (
        <dl className="mt-12 grid grid-cols-[auto_1fr] gap-x-6 gap-y-1 border-n-2 border-t pt-4 text-xs text-n-6">
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
  "inline-flex h-9 items-center gap-2 bg-rail px-4 text-sm font-medium text-rail-ink hover:bg-rail-raised disabled:opacity-60";
const secondaryActionClass =
  "inline-flex h-9 items-center gap-2 border border-n-3 bg-sheet px-4 text-sm text-ink hover:border-n-5";

export function BackToReposLink() {
  return (
    <Link to="/app/dashboard" className={secondaryActionClass}>
      {t("taskBackToRepos")}
    </Link>
  );
}
