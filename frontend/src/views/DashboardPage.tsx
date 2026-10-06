"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import RepoGrid, { parseRepos, type RepoData } from "@/components/RepoGrid";
import RepoSearch from "@/components/RepoSearch";
import {
  secondaryActionClass,
  TaskStatePanel,
} from "@/components/wiki/TaskStatePanel";
import { api, type TaskStatusResponse } from "@/lib/api";
import { t } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { useAuth } from "@/providers/AuthProvider";
import { prefetchRouteModule } from "@/router/prefetch";

const MAX_RETRIES = 5;

export default function DashboardPage() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const [url, setUrl] = useState("");
  const [taskId, setTaskId] = useState<string | null>(null);
  const [status, setStatus] = useState<TaskStatusResponse | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [health, setHealth] = useState<boolean | null>(null);
  const [repos, setRepos] = useState<RepoData[]>([]);
  const [reposLoading, setReposLoading] = useState(true);
  const userId = user?.id;

  // One request feeds both the search field and the repository list.
  useEffect(() => {
    if (!userId) return;
    let cancelled = false;
    api
      .getDashboardRepos(userId)
      .then((response) => {
        if (!cancelled) setRepos(parseRepos(response.repos));
      })
      .catch((error: unknown) => {
        console.error("Failed to load dashboard repositories:", error);
      })
      .finally(() => {
        if (!cancelled) setReposLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [userId]);

  const pollTimerRef = useRef<NodeJS.Timeout | null>(null);
  const activePollTaskIdRef = useRef<string | null>(null);
  const pollInFlightRef = useRef(false);
  const pollAttemptRef = useRef(0);

  const getPollDelayMs = useCallback((attempt: number) => {
    if (typeof document !== "undefined" && document.hidden) return 20000;
    if (attempt <= 1) return 5000;
    if (attempt <= 3) return 10000;
    return 20000;
  }, []);

  const clearTask = useCallback(() => {
    setTaskId(null);
    setStatus(null);
    localStorage.removeItem("wiki_gen_task_id");
    localStorage.removeItem("wiki_gen_repo_url");
    if (pollTimerRef.current) clearTimeout(pollTimerRef.current);
    activePollTaskIdRef.current = null;
    pollInFlightRef.current = false;
    pollAttemptRef.current = 0;
  }, []);

  const goToWiki = useCallback(
    (finishedTaskId: string, repoUrl: string) => {
      prefetchRouteModule("wiki");
      navigate(
        `/app/wiki/${finishedTaskId}?repo=${encodeURIComponent(repoUrl)}`,
      );
    },
    [navigate],
  );

  const pollStatus = useCallback(
    async (id: string) => {
      if (activePollTaskIdRef.current !== id) return;
      if (pollInFlightRef.current) return;
      pollInFlightRef.current = true;

      try {
        const result = await api.getTaskStatus(id);
        setStatus(result);
        pollAttemptRef.current = 0;

        if (result.status === "completed" || result.status === "cached") {
          if (pollTimerRef.current) clearTimeout(pollTimerRef.current);
          activePollTaskIdRef.current = null;
          pollInFlightRef.current = false;
          const repo =
            result.result?.repo_url ||
            localStorage.getItem("wiki_gen_repo_url") ||
            url;
          goToWiki(id, repo);
          return;
        }

        if (result.status === "failed") {
          pollInFlightRef.current = false;
          return;
        }

        pollInFlightRef.current = false;
        if (pollTimerRef.current) clearTimeout(pollTimerRef.current);
        pollTimerRef.current = setTimeout(() => {
          void pollStatus(id);
        }, getPollDelayMs(pollAttemptRef.current));
      } catch (error) {
        pollInFlightRef.current = false;
        const statusCode = (error as Error & { statusCode?: number })
          .statusCode;
        if (statusCode === 404) {
          clearTask();
          return;
        }

        pollAttemptRef.current += 1;
        if (pollAttemptRef.current >= MAX_RETRIES) {
          clearTask();
          return;
        }

        if (pollTimerRef.current) clearTimeout(pollTimerRef.current);
        pollTimerRef.current = setTimeout(() => {
          void pollStatus(id);
        }, getPollDelayMs(pollAttemptRef.current));
      }
    },
    [clearTask, getPollDelayMs, goToWiki, url],
  );

  useEffect(() => {
    void api.checkHealth().then(setHealth);
    prefetchRouteModule("history");

    const tryRestorePendingTask = async () => {
      const savedTaskId = localStorage.getItem("wiki_gen_task_id");
      const savedRepoUrl = localStorage.getItem("wiki_gen_repo_url");
      if (!savedTaskId) return;

      if (savedRepoUrl) setUrl(savedRepoUrl);
      try {
        const savedTaskStatus = await api.getTaskStatus(savedTaskId);
        const isPendingTask =
          savedTaskStatus.status === "pending" ||
          savedTaskStatus.status === "processing";
        if (!isPendingTask) {
          clearTask();
          return;
        }
        setTaskId(savedTaskId);
        setStatus(savedTaskStatus);
        activePollTaskIdRef.current = savedTaskId;
        pollAttemptRef.current = 0;
        void pollStatus(savedTaskId);
      } catch {
        clearTask();
      }
    };
    void tryRestorePendingTask();

    return () => {
      if (pollTimerRef.current) clearTimeout(pollTimerRef.current);
      activePollTaskIdRef.current = null;
      pollInFlightRef.current = false;
    };
  }, [pollStatus, clearTask]);

  const startTask = async (normalizedInputUrl: string) => {
    if (!user) return;
    setUrl(normalizedInputUrl);
    setIsSubmitting(true);
    setStatus(null);
    setSubmitError(null);

    try {
      const result = await api.createTask(normalizedInputUrl, user.id);
      setTaskId(result.task_id);
      localStorage.setItem("wiki_gen_task_id", result.task_id);
      localStorage.setItem("wiki_gen_repo_url", normalizedInputUrl);
      if (pollTimerRef.current) clearTimeout(pollTimerRef.current);
      activePollTaskIdRef.current = result.task_id;
      pollAttemptRef.current = 0;
      void pollStatus(result.task_id);
    } catch {
      setSubmitError(t("dashboardStartFailed"));
    } finally {
      setIsSubmitting(false);
    }
  };

  const generating = taskId !== null && status?.status !== "failed";
  const failedMessage =
    status?.status === "failed" ? status.error || t("unknownError") : null;

  return (
    <div className="relative mx-auto w-full max-w-5xl">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 -top-24 h-[36rem] bg-[radial-gradient(50%_60%_at_50%_35%,rgb(243_128_32/0.2),transparent_70%)]"
      />

      <section className="relative pt-10 text-center md:pt-20">
        <h1 className="font-medium text-5xl tracking-tight md:text-7xl">
          GitReader
        </h1>
        <p className="mx-auto mt-5 max-w-xl text-fg-muted md:text-lg">
          {t("dashboardSubtitle")}
        </p>

        {generating ? (
          <div className="mt-8">
            <TaskStatePanel
              stamp={status?.status ?? "pending"}
              title={t("dashboardGenerating")}
              detail={status?.current_step}
              progress={status?.progress}
              meta={{
                taskId: taskId ?? undefined,
                repo: localStorage.getItem("wiki_gen_repo_url") ?? undefined,
              }}
              actions={
                <button
                  type="button"
                  onClick={clearTask}
                  className={secondaryActionClass}
                >
                  {t("dashboardStartOver")}
                </button>
              }
            />
          </div>
        ) : (
          <>
            <RepoSearch
              repos={repos}
              loading={reposLoading}
              value={url}
              onValueChange={setUrl}
              onGenerate={(repoUrl) => void startTask(repoUrl)}
              submitting={isSubmitting}
            />
            {submitError || failedMessage ? (
              <p role="alert" className="mt-4 text-danger text-sm">
                {submitError ?? failedMessage}
              </p>
            ) : null}
          </>
        )}

        {health === null ? null : (
          <p className="mt-6 inline-flex items-center gap-2 font-mono text-fg-faint text-xs">
            <span
              aria-hidden
              className={cn(
                "h-1.5 w-1.5 rounded-full",
                health ? "bg-ok" : "bg-danger",
              )}
            />
            {health ? t("apiOnline") : t("apiOffline")}
          </p>
        )}
      </section>

      {user ? <RepoGrid repos={repos} loading={reposLoading} /> : null}
    </div>
  );
}
