"use client";

import { Clock, Loader2, Trash2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { StatusStamp } from "@/components/wiki/TaskStatePanel";
import { api, type TaskStatusResponse } from "@/lib/api";
import { t } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { useAuth } from "@/providers/AuthProvider";
import { prefetchRouteModule } from "@/router/prefetch";

export default function HistoryPage() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const [tasks, setTasks] = useState<TaskStatusResponse[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [deletingTaskId, setDeletingTaskId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    if (!user) return;
    setIsLoading(true);
    try {
      const taskList = await api.getTasks(user.id);
      setTasks(taskList);
    } finally {
      setIsLoading(false);
    }
  }, [user]);

  useEffect(() => {
    prefetchRouteModule("wiki");
    void loadData();
  }, [loadData]);

  const sortedTasks = useMemo(() => {
    return [...tasks].sort(
      (a, b) =>
        new Date(b.last_updated || b.created_at).getTime() -
        new Date(a.last_updated || a.created_at).getTime(),
    );
  }, [tasks]);

  const handleOpenTask = (task: TaskStatusResponse) => {
    if (
      (task.status !== "completed" && task.status !== "cached") ||
      !task.result
    ) {
      setError(t("historyTaskNotReady"));
      return;
    }
    setError(null);
    navigate(
      `/app/wiki/${task.task_id}?repo=${encodeURIComponent(task.result.repo_url || task.repo_url)}`,
    );
  };

  const handleDeleteTask = async (
    task: TaskStatusResponse,
    event: React.MouseEvent,
  ) => {
    event.stopPropagation();
    if (!user) return;
    setDeletingTaskId(task.task_id);
    setError(null);
    try {
      if (task.status === "processing" || task.status === "pending") {
        try {
          await api.cancelTask(task.task_id);
        } catch (err) {
          throw new Error(
            err instanceof Error
              ? `${t("cancelTaskFailed")}: ${err.message}`
              : t("cancelTaskFailed"),
          );
        }
      }

      await api.deleteTask(task.task_id, user.id);
      setTasks((prev) => prev.filter((item) => item.task_id !== task.task_id));
    } catch (err) {
      setError(err instanceof Error ? err.message : t("deleteFailed"));
    } finally {
      setDeletingTaskId(null);
    }
  };

  if (isLoading) {
    return (
      <div className="flex min-h-[50vh] items-center justify-center">
        <Loader2 aria-hidden className="h-6 w-6 animate-spin text-fg-muted" />
      </div>
    );
  }

  if (sortedTasks.length === 0) {
    return (
      <div className="flex min-h-[50vh] items-center justify-center">
        <div className="rounded-2xl border border-line border-dashed px-12 py-12 text-center">
          <Clock aria-hidden className="mx-auto mb-4 h-10 w-10 text-fg-faint" />
          <p className="text-fg-muted">{t("noHistory")}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto w-full max-w-3xl space-y-6 pt-4">
      <h1 className="font-medium text-3xl tracking-tight">
        {t("historyTitle")}
      </h1>
      {error ? (
        <p role="alert" className="text-danger text-sm">
          {error}
        </p>
      ) : null}
      <ul className="space-y-3">
        {sortedTasks.map((item) => {
          const clickable =
            item.status === "completed" || item.status === "cached";
          const deleting = deletingTaskId === item.task_id;
          return (
            <li key={`task-${item.id}`}>
              {/* biome-ignore lint/a11y/useSemanticElements: the row holds a nested delete button */}
              <div
                role="button"
                tabIndex={0}
                onClick={() => !deleting && clickable && handleOpenTask(item)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    if (!deleting && clickable) handleOpenTask(item);
                  }
                }}
                className={cn(
                  "flex items-center gap-4 rounded-2xl border border-line bg-panel px-5 py-4 transition-colors",
                  deleting && "opacity-60",
                  clickable
                    ? "cursor-pointer hover:border-accent-line"
                    : "cursor-default opacity-80",
                )}
              >
                <div className="min-w-0 flex-1 space-y-1.5">
                  <p className="break-all font-mono text-fg text-sm">
                    {item.repo_url}
                  </p>
                  <p className="font-mono text-fg-faint text-xs tabular-nums">
                    {new Date(item.created_at).toLocaleDateString("en-US")}
                  </p>
                </div>
                <StatusStamp status={item.status} />
                <button
                  type="button"
                  onClick={(event) => void handleDeleteTask(item, event)}
                  disabled={deleting}
                  aria-label={t("deleteTask")}
                  title={t("deleteTask")}
                  className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-fg-muted transition-colors hover:bg-raised hover:text-danger"
                >
                  {deleting ? (
                    <Loader2 aria-hidden className="h-4 w-4 animate-spin" />
                  ) : (
                    <Trash2 aria-hidden className="h-4 w-4" />
                  )}
                </button>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
