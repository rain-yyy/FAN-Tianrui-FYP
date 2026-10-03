"use client";

import { motion } from "framer-motion";
import {
  Bot,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  FileCode,
  GitBranch,
  Globe,
  Map as MapIcon,
  Search,
  Sparkles,
  TextSearch,
  XCircle,
} from "lucide-react";
import React, { useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";
import type { ChatMessage, ToolTrajectoryStep } from "@/lib/api";
import { parseCitations } from "@/lib/citations";
import { t } from "@/lib/i18n";
import { getToolDescription } from "@/lib/toolDescriptions";
import { cn } from "@/lib/utils";
import CodeViewer from "./CodeViewer";
import SourcesPanel from "./SourcesPanel";

export interface DisplayMessage extends ChatMessage {
  id: string;
  timestamp: Date;
  sources?: string[];
  isError?: boolean;
  /** The user stopped the stream; `content` is the partial answer. */
  stopped?: boolean;
  tool_trajectory?: ToolTrajectoryStep[];
}

const toolIcons: Record<string, React.ReactNode> = {
  rag_search: <Search className="w-3.5 h-3.5" />,
  code_graph: <GitBranch className="w-3.5 h-3.5" />,
  file_read: <FileCode className="w-3.5 h-3.5" />,
  repo_map: <MapIcon className="w-3.5 h-3.5" />,
  grep_search: <TextSearch className="w-3.5 h-3.5" />,
  web_search: <Globe className="w-3.5 h-3.5" />,
};

const REMARK_PLUGINS = [remarkGfm];
const REHYPE_PLUGINS = [rehypeHighlight];

const TrajectoryDisplay = ({
  trajectory,
}: {
  trajectory: ToolTrajectoryStep[];
}) => {
  const [expanded, setExpanded] = useState(false);

  if (!trajectory || trajectory.length === 0) return null;

  const successCount = trajectory.filter((s) => s.status === "success").length;

  return (
    <div className="mt-4 border border-stone-200 rounded-xl overflow-hidden bg-stone-50">
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="w-full px-4 py-3 flex items-center justify-between text-sm text-stone-600 hover:text-stone-900 hover:bg-stone-100 transition-colors"
        aria-expanded={expanded}
        tabIndex={0}
        onKeyDown={(e) => e.key === "Enter" && setExpanded(!expanded)}
      >
        <div className="flex items-center gap-2">
          <Bot className="w-4 h-4 text-teal-700" />
          <span>Exploration</span>
          <span className="text-xs px-2 py-0.5 rounded-full bg-teal-100 text-teal-900 border border-teal-200">
            {successCount} steps
          </span>
        </div>
        {expanded ? (
          <ChevronUp className="w-4 h-4" />
        ) : (
          <ChevronDown className="w-4 h-4" />
        )}
      </button>

      {expanded && (
        <div className="px-4 pb-4 space-y-2">
          {trajectory.map((step, idx) => (
            <div
              // biome-ignore lint/suspicious/noArrayIndexKey: steps have no id and never reorder
              key={idx}
              className={cn(
                "flex items-center gap-3 p-3 rounded-lg transition-colors",
                step.status === "success" ? "bg-white" : "bg-rose-50",
              )}
            >
              <div
                className={cn(
                  "w-7 h-7 rounded-lg flex items-center justify-center shrink-0",
                  step.status === "success"
                    ? "bg-teal-100 text-teal-900"
                    : "bg-rose-100 text-rose-800",
                )}
              >
                {toolIcons[step.tool] || <Sparkles className="w-3.5 h-3.5" />}
              </div>
              <div className="flex-1 min-w-0 flex items-center justify-between">
                <span className="text-sm text-stone-700">
                  {getToolDescription(step.tool, step.arguments)}
                </span>
                {step.status === "success" ? (
                  <CheckCircle2 className="w-4 h-4 text-green-400 shrink-0" />
                ) : (
                  <XCircle className="w-4 h-4 text-red-400 shrink-0" />
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

export interface MessageItemProps {
  message: DisplayMessage;
  repoUrl: string;
}

export const MessageItem = React.memo(
  ({ message, repoUrl }: MessageItemProps) => {
    const isUser = message.role === "user";
    const [isCodeViewerOpen, setIsCodeViewerOpen] = useState(false);
    const [selectedSourceIndex, setSelectedSourceIndex] = useState(0);
    // One parsed list for both the chips and the Code Inspector, so the
    // clicked chip and the opened file always share an index.
    const citations = useMemo(
      () => parseCitations(message.sources),
      [message.sources],
    );

    const openFile = (index: number) => {
      setSelectedSourceIndex(index);
      setIsCodeViewerOpen(true);
    };

    return (
      <motion.div
        initial={{ opacity: 0, y: 15 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, ease: "easeOut" }}
        className="mb-8"
      >
        {/* Code Viewer Modal: mounted only while open, so its internal state always starts correct */}
        {isCodeViewerOpen && (
          <CodeViewer
            onClose={() => setIsCodeViewerOpen(false)}
            sources={citations.files}
            initialSourceIndex={selectedSourceIndex}
            repoUrl={repoUrl}
          />
        )}

        <div
          className={cn(
            "flex gap-4 group",
            isUser ? "flex-row-reverse" : "flex-row",
          )}
        >
          {!isUser && (
            <div className="w-8 h-8 rounded-xl flex items-center justify-center shrink-0 mt-1 shadow-sm bg-teal-50 ring-1 ring-teal-200">
              <Bot className="w-4 h-4 text-teal-700" />
            </div>
          )}

          <div
            className={cn(
              "flex-1 min-w-0 space-y-1.5",
              isUser ? "flex flex-col items-end" : "text-left",
            )}
          >
            {!isUser && (
              <div className="flex items-center gap-2 mb-1.5 ml-1">
                <span className="font-medium text-ink text-sm">Agent</span>
                <span className="font-mono text-n-6 text-xs tabular-nums">
                  {message.timestamp.toLocaleTimeString([], {
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                </span>
              </div>
            )}

            <div
              className={cn(
                isUser
                  ? "bg-sky-600 text-white px-5 py-3.5 rounded-3xl rounded-tr-md max-w-[85%] text-[15px] leading-relaxed shadow-sm"
                  : "prose prose-stone prose-sm max-w-none prose-p:text-stone-700 prose-p:leading-[1.7] prose-p:text-[15px] prose-pre:bg-stone-100 prose-pre:border prose-pre:border-stone-200 prose-pre:rounded-xl prose-pre:shadow-sm prose-code:text-sky-800 prose-code:bg-sky-50 prose-code:px-1.5 prose-code:py-0.5 prose-code:rounded-md prose-code:font-medium prose-headings:text-stone-900 prose-headings:font-semibold prose-a:text-sky-700 hover:prose-a:text-sky-800 prose-a:no-underline prose-ul:my-2 prose-li:my-0.5",
              )}
            >
              {isUser ? (
                <div className="whitespace-pre-wrap">{message.content}</div>
              ) : (
                <ReactMarkdown
                  remarkPlugins={REMARK_PLUGINS}
                  rehypePlugins={REHYPE_PLUGINS}
                >
                  {message.content}
                </ReactMarkdown>
              )}
            </div>

            {message.isError || message.stopped ? (
              <p className="border-ink border-l-2 pl-3 font-mono text-n-7 text-xs">
                {message.isError ? t("chatTurnFailed") : t("chatStopped")}
              </p>
            ) : null}

            {!isUser &&
              message.tool_trajectory &&
              message.tool_trajectory.length > 0 && (
                <TrajectoryDisplay trajectory={message.tool_trajectory} />
              )}

            {!isUser &&
              (citations.files.length > 0 || citations.links.length > 0) && (
                <div className="mt-5">
                  <div className="mb-2.5 flex items-center gap-2 font-medium text-n-6 text-xs uppercase tracking-wider">
                    <span>References</span>
                    <div className="h-px flex-1 bg-n-2" />
                  </div>
                  <SourcesPanel
                    files={citations.files}
                    links={citations.links}
                    onOpenFile={openFile}
                  />
                </div>
              )}
          </div>
        </div>
      </motion.div>
    );
  },
);

MessageItem.displayName = "MessageItem";
