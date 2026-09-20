'use client';

import { useCallback, useState } from 'react';
import { api, ChatTurnRequest, ToolTrajectoryStep } from '@/lib/api';
import { getToolDescription } from '@/lib/toolDescriptions';
import { t } from '@/lib/i18n';
import type { LiveStep } from '@/components/LiveStepFlow';

export interface ChatStreamResult {
  chatId: string;
  answer: string;
  sources: string[];
  trajectory: ToolTrajectoryStep[];
}

/** Compile-time guard: fails the build if a new ChatStreamEvent variant goes unhandled below. */
function assertNever(value: never): never {
  throw new Error(`Unhandled chat stream event: ${JSON.stringify(value)}`);
}

/**
 * Consumes one /chat/stream turn: drives the live step list + streaming answer
 * text while the turn is in flight, and resolves with the finished turn once
 * `answer_done`/`turn_start` have both been observed. Kept out of ChatInterface
 * because this is pure SSE-vocabulary interpretation with no JSX concerns.
 */
export function useChatStream() {
  const [isStreaming, setIsStreaming] = useState(false);
  const [liveSteps, setLiveSteps] = useState<LiveStep[]>([]);
  const [streamingAnswer, setStreamingAnswer] = useState('');

  const sendMessage = useCallback(async (request: ChatTurnRequest): Promise<ChatStreamResult> => {
    setIsStreaming(true);
    setLiveSteps([]);
    setStreamingAnswer('');

    const addStep = (step: LiveStep) => {
      setLiveSteps(prev => {
        const existing = prev.find(s => s.id === step.id);
        if (existing) return prev.map(s => (s.id === step.id ? step : s));
        return [...prev, step];
      });
    };
    const updateStep = (id: string, updates: Partial<LiveStep>) => {
      setLiveSteps(prev => prev.map(s => (s.id === id ? { ...s, ...updates } : s)));
    };

    try {
      let stepCounter = 0;
      let answer: string | null = null;
      let sources: string[] = [];
      let chatId: string | null = null;
      const trajectory: ToolTrajectoryStep[] = [];
      // tool_call_result only identifies the tool, not a call id, so pair each
      // result with the oldest still-pending call for that tool (FIFO) — the
      // backend resolves tool calls for a given tool in the order it started them.
      const pendingCalls = new Map<string, Array<{ stepId: string; args: Record<string, unknown> }>>();

      for await (const event of api.askQuestionStream(request)) {
        switch (event.type) {
          case 'turn_start':
            chatId = event.data.chat_id;
            break;

          case 'iteration_start': {
            const { iteration, max_iterations } = event.data;
            setLiveSteps(prev => prev.map(s =>
              s.type === 'iteration' && s.status === 'running' ? { ...s, status: 'done' as const } : s
            ));
            addStep({
              id: `iter_${iteration}`,
              type: 'iteration',
              status: 'running',
              title: t('thinkingStep', { n: iteration, max: max_iterations }),
            });
            break;
          }

          case 'tool_call_start': {
            const { tool, arguments: args } = event.data;
            stepCounter++;
            const stepId = `tool_${stepCounter}`;
            const queue = pendingCalls.get(tool) ?? [];
            queue.push({ stepId, args });
            pendingCalls.set(tool, queue);
            addStep({
              id: stepId,
              type: 'tool',
              status: 'running',
              title: getToolDescription(tool, args),
              toolName: tool,
            });
            break;
          }

          case 'tool_call_result': {
            const { tool, status, summary } = event.data;
            const pending = pendingCalls.get(tool)?.shift();
            const resolvedStatus = status === 'success' ? 'done' as const : 'error' as const;
            if (pending) {
              updateStep(pending.stepId, { status: resolvedStatus });
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

          case 'answer_token':
            setStreamingAnswer(prev => prev + event.data.delta);
            break;

          case 'answer_done':
            answer = event.data.answer;
            sources = event.data.sources;
            break;

          case 'error':
            throw new Error(event.data.detail || 'Chat stream failed');

          case 'complete':
            // Turn is persisted server-side; nothing left to capture here —
            // chat_id already arrived on turn_start.
            break;

          default:
            assertNever(event);
        }
      }

      if (answer === null || !chatId) {
        throw new Error(t('agentNoResult'));
      }

      return { chatId, answer, sources, trajectory };
    } finally {
      setIsStreaming(false);
      setLiveSteps([]);
      setStreamingAnswer('');
    }
  }, []);

  return { isStreaming, liveSteps, streamingAnswer, sendMessage };
}
