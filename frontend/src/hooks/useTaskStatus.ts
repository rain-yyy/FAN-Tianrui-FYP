"use client";

import { useCallback, useEffect, useState } from "react";
import { api, type TaskStatusResponse } from "@/lib/api";

/** Statuses for which the backend is still working and we keep polling. */
const IN_FLIGHT = new Set(["pending", "processing"]);
const POLL_INTERVAL_MS = 2500;

type TaskStatusState =
  | { kind: "loading" }
  | { kind: "not-found" }
  | { kind: "unreachable"; message: string }
  | { kind: "task"; task: TaskStatusResponse };

export function isTaskInFlight(task: TaskStatusResponse): boolean {
  return IN_FLIGHT.has(task.status);
}

/**
 * Tracks one wiki-generation task through `POST /task/{id}`.
 *
 * The response is the single source of truth: nothing is inferred locally.
 * While the task is `pending`/`processing` it is re-fetched every
 * POLL_INTERVAL_MS; polling stops on any other status, on a request error,
 * or when the component unmounts / the task id changes.
 */
export function useTaskStatus(taskId: string | undefined) {
  const [state, setState] = useState<TaskStatusState>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);

  // biome-ignore lint/correctness/useExhaustiveDependencies: `attempt` re-runs the fetch loop on demand
  useEffect(() => {
    if (!taskId) {
      setState({ kind: "not-found" });
      return;
    }

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    setState({ kind: "loading" });

    const load = async () => {
      try {
        const task = await api.getTaskStatus(taskId);
        if (cancelled) return;
        setState({ kind: "task", task });
        if (isTaskInFlight(task)) {
          timer = setTimeout(load, POLL_INTERVAL_MS);
        }
      } catch (error) {
        if (cancelled) return;
        const statusCode = (error as { statusCode?: number }).statusCode;
        setState(
          statusCode === 404
            ? { kind: "not-found" }
            : {
                kind: "unreachable",
                message: error instanceof Error ? error.message : String(error),
              },
        );
      }
    };

    void load();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [taskId, attempt]);

  const reload = useCallback(() => setAttempt((n) => n + 1), []);

  return { state, reload };
}
