"use client";

import { RotateCw } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import WikiViewer from "@/components/WikiViewer";
import {
  BackToReposLink,
  primaryActionClass,
  TaskStatePanel,
} from "@/components/wiki/TaskStatePanel";
import { isTaskInFlight, useTaskStatus } from "@/hooks/useTaskStatus";
import { repoFullName, useRegisterShellRepo } from "@/layouts/ShellContext";
import { api, type TaskStatusResponse } from "@/lib/api";
import { t } from "@/lib/i18n";
import { useAuth } from "@/providers/AuthProvider";

const READY_STATUSES = new Set(["completed", "cached"]);

function rememberTask(taskId: string, repoUrl: string) {
  try {
    localStorage.setItem("wiki_gen_task_id", taskId);
    if (repoUrl) localStorage.setItem("wiki_gen_repo_url", repoUrl);
  } catch {
    // Storage may be unavailable (private mode); the page works without it.
  }
}

/** Starts a fresh generation for the same repo and moves to its task page. */
function RegenerateButton({ repoUrl }: { repoUrl: string }) {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!repoUrl || !user) return null;

  const regenerate = async () => {
    setBusy(true);
    setError(null);
    try {
      const { task_id } = await api.createTask(repoUrl, user.id);
      navigate(`/app/wiki/${task_id}?repo=${encodeURIComponent(repoUrl)}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  };

  return (
    <>
      <button
        type="button"
        onClick={regenerate}
        disabled={busy}
        className={primaryActionClass}
      >
        <RotateCw aria-hidden className="h-4 w-4" />
        {busy ? t("taskRegenerating") : t("taskRegenerate")}
      </button>
      {error ? (
        <p role="alert" className="basis-full font-mono text-sm text-ink">
          {error}
        </p>
      ) : null}
    </>
  );
}

function TaskView({
  task,
  repoUrl,
  userId,
  initialChatId,
}: {
  task: TaskStatusResponse;
  repoUrl: string;
  userId: string;
  initialChatId?: string;
}) {
  const meta = {
    taskId: task.task_id,
    repo: repoUrl ? repoFullName(repoUrl) : undefined,
    createdAt: task.created_at,
    updatedAt: task.last_updated,
  };

  if (isTaskInFlight(task)) {
    return (
      <TaskStatePanel
        live
        stamp={task.status}
        title={t("taskInFlightTitle")}
        detail={task.current_step || null}
        progress={task.progress ?? 0}
        meta={meta}
        actions={<BackToReposLink />}
      />
    );
  }

  if (READY_STATUSES.has(task.status)) {
    const structureUrl = task.result?.r2_structure_url;
    const contentUrls = task.result?.r2_content_urls;
    if (structureUrl && contentUrls) {
      return (
        <WikiViewer
          userId={userId}
          structureUrl={structureUrl}
          contentUrls={contentUrls}
          repoUrl={repoUrl}
          initialChatId={initialChatId}
        />
      );
    }
    // Completed according to the API, but without published artifacts the
    // wiki cannot be rendered. Say so instead of guessing.
    return (
      <TaskStatePanel
        stamp={task.status}
        title={t("taskNoArtifactsTitle")}
        detail={t("taskNoArtifactsDetail")}
        meta={meta}
        actions={
          <>
            <RegenerateButton repoUrl={repoUrl} />
            <BackToReposLink />
          </>
        }
      />
    );
  }

  // failed, cancelled, or any status this client does not know about:
  // show the backend's own words.
  const failed = task.status === "failed";
  return (
    <TaskStatePanel
      stamp={task.status}
      title={failed ? t("taskFailedTitle") : t("taskStoppedTitle")}
      detail={task.error || (failed ? t("taskFailedNoDetail") : null)}
      detailIsError={Boolean(task.error)}
      meta={meta}
      actions={
        <>
          <RegenerateButton repoUrl={repoUrl} />
          <BackToReposLink />
        </>
      }
    />
  );
}

export default function WikiPage() {
  const { taskId } = useParams<{ taskId: string }>();
  const [searchParams] = useSearchParams();
  const { user } = useAuth();
  const { state, reload } = useTaskStatus(taskId);

  const task = state.kind === "task" ? state.task : null;
  // The task record is authoritative; ?repo= only fills the gap before it loads.
  const repoUrl =
    task?.repo_url || task?.result?.repo_url || searchParams.get("repo") || "";
  const initialChatId = searchParams.get("chatId") || undefined;

  useRegisterShellRepo(repoUrl, `/app/wiki/${taskId ?? ""}`);

  const taskKey = task?.task_id ?? "";
  useEffect(() => {
    if (taskKey) rememberTask(taskKey, repoUrl);
  }, [taskKey, repoUrl]);

  if (state.kind === "loading") {
    return (
      <TaskStatePanel
        live
        stamp={t("taskLoadingStamp")}
        title={t("taskLoadingTitle")}
        meta={{ taskId, repo: repoUrl ? repoFullName(repoUrl) : undefined }}
      />
    );
  }

  if (state.kind === "not-found") {
    return (
      <TaskStatePanel
        stamp="404"
        title={t("taskNotFoundTitle")}
        detail={t("taskNotFoundDetail")}
        meta={{ taskId }}
        actions={<BackToReposLink />}
      />
    );
  }

  if (state.kind === "unreachable") {
    return (
      <TaskStatePanel
        stamp={t("taskUnreachableStamp")}
        title={t("taskUnreachableTitle")}
        detail={state.message}
        detailIsError
        meta={{ taskId }}
        actions={
          <>
            <button
              type="button"
              onClick={reload}
              className={primaryActionClass}
            >
              <RotateCw aria-hidden className="h-4 w-4" />
              {t("retry")}
            </button>
            <BackToReposLink />
          </>
        }
      />
    );
  }

  if (!user) return null; // AuthGuard guarantees a user; this narrows the type.

  return (
    <TaskView
      task={state.task}
      repoUrl={repoUrl}
      userId={user.id}
      initialChatId={initialChatId}
    />
  );
}
