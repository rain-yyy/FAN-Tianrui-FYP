"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { LiveStep } from "@/components/LiveStepFlow";
import { api, type ChatTurnRequest, type ToolTrajectoryStep } from "@/lib/api";
import { t } from "@/lib/i18n";
import { getToolDescription } from "@/lib/toolDescriptions";

export interface ChatStreamResult {
  chatId: string;
  answer: string;
  sources: string[];
  trajectory: ToolTrajectoryStep[];
}

/** Thrown by `sendMessage` when `stop()` cut the turn short. */
export class ChatStreamStopped extends Error {
  constructor(
    readonly chatId: string | null,
    readonly partialAnswer: string,
    readonly trajectory: ToolTrajectoryStep[],
  ) {
    super("Stopped");
    this.name = "ChatStreamStopped";
  }
}

/** Compile-time guard: fails the build if a new ChatStreamEvent variant goes unhandled below. */
function assertNever(value: never): never {
  throw new Error(`Unhandled chat stream event: ${JSON.stringify(value)}`);
}

/**
 * Consumes one /chat/stream turn: drives the live step list and the streaming
 * answer while the turn is in flight, and resolves with the finished turn.
 * `stop()` aborts the request; the turn then rejects with ChatStreamStopped
 * carrying whatever had streamed. Unmounting aborts too.
 */
export function useChatStream() {
  const [isStreaming, setIsStreaming] = useState(false);
  const [liveSteps, setLiveSteps] = useState<LiveStep[]>([]);
  const [streamingAnswer, setStreamingAnswer] = useState("");
  const controllerRef = useRef<AbortController | null>(null);

  useEffect(() => () => controllerRef.current?.abort(), []);

  const stop = useCallback(() => controllerRef.current?.abort(), []);

  const sendMessage = useCallback(
    async (request: ChatTurnRequest): Promise<ChatStreamResult> => {
      controllerRef.current?.abort();
      const controller = new AbortController();
      controllerRef.current = controller;
      setIsStreaming(true);
      setLiveSteps([]);
      setStreamingAnswer("");

      const addStep = (step: LiveStep) => {
        setLiveSteps((prev) =>
          prev.some((s) => s.id === step.id)
            ? prev.map((s) => (s.id === step.id ? step : s))
            : [...prev, step],
        );
      };
      const updateStep = (id: string, updates: Partial<LiveStep>) => {
        setLiveSteps((prev) =>
          prev.map((s) => (s.id === id ? { ...s, ...updates } : s)),
        );
      };

      let stepCounter = 0;
      let answer: string | null = null;
      let partial = "";
      let sources: string[] = [];
      let chatId: string | null = null;
      const trajectory: ToolTrajectoryStep[] = [];
      // tool_call_result only identifies the tool, not a call id, so pair each
      // result with the oldest still-pending call for that tool (FIFO); the
      // backend resolves calls for a given tool in the order it started them.
      const pendingCalls = new Map<
        string,
        Array<{ stepId: string; args: Record<string, unknown> }>
      >();

      try {
        for await (const event of api.askQuestionStream(
          request,
          controller.signal,
        )) {
          switch (event.type) {
            case "turn_start":
              chatId = event.data.chat_id;
              break;

            case "iteration_start": {
              const { iteration, max_iterations } = event.data;
              setLiveSteps((prev) =>
                prev.map((s) =>
                  s.type === "iteration" && s.status === "running"
                    ? { ...s, status: "done" as const }
                    : s,
                ),
              );
              addStep({
                id: `iter_${iteration}`,
                type: "iteration",
                status: "running",
                title: t("thinkingStep", { n: iteration, max: max_iterations }),
              });
              break;
            }

            case "tool_call_start": {
              const { tool, arguments: args } = event.data;
              stepCounter++;
              const stepId = `tool_${stepCounter}`;
              const queue = pendingCalls.get(tool) ?? [];
              queue.push({ stepId, args });
              pendingCalls.set(tool, queue);
              addStep({
                id: stepId,
                type: "tool",
                status: "running",
                title: getToolDescription(tool, args),
                toolName: tool,
              });
              break;
            }

            case "tool_call_result": {
              const { tool, status, summary } = event.data;
              const pending = pendingCalls.get(tool)?.shift();
              if (pending) {
                updateStep(pending.stepId, {
                  status: status === "success" ? "done" : "error",
                });
              }
              trajectory.push({
                tool,
                arguments: pending?.args ?? {},
                status,
                summary,
                duration_ms: null,
              });
              break;
            }

            case "answer_token":
              partial += event.data.delta;
              setStreamingAnswer(partial);
              break;

            case "answer_done":
              answer = event.data.answer;
              sources = event.data.sources;
              break;

            case "error":
              throw new Error(event.data.detail || t("chatStreamFailed"));

            case "complete":
              // Persisted server-side; chat_id already arrived on turn_start.
              break;

            default:
              assertNever(event);
          }
        }

        if (answer === null || !chatId) throw new Error(t("agentNoResult"));
        return { chatId, answer, sources, trajectory };
      } catch (error) {
        if (controller.signal.aborted) {
          throw new ChatStreamStopped(chatId, partial, trajectory);
        }
        throw error;
      } finally {
        if (controllerRef.current === controller) {
          controllerRef.current = null;
          setIsStreaming(false);
          setLiveSteps([]);
          setStreamingAnswer("");
        }
      }
    },
    [],
  );

  return { isStreaming, liveSteps, streamingAnswer, sendMessage, stop };
}
