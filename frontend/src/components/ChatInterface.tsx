"use client";

import {
  ArrowUp,
  History,
  MessageSquare,
  PanelRightClose,
  Plus,
  RotateCw,
  Square,
  Trash2,
  X,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { LiveStepFlow } from "@/components/LiveStepFlow";
import { type DisplayMessage, MessageItem } from "@/components/MessageItem";
import { ChatStreamStopped, useChatStream } from "@/hooks/useChatStream";
import { useShortcut } from "@/hooks/useShortcut";
import { repoFullName, useRegisterShellChat } from "@/layouts/ShellContext";
import {
  api,
  type ChatHistoryItem,
  type ChatHistoryMessage,
  normalizeRepoUrl,
  type ToolTrajectoryStep,
} from "@/lib/api";
import { t } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import type { TocNode } from "@/lib/wikiToc";

/**
 * Mirrors the backend's _generate_chat_preview_sync so a new session gets a
 * title locally, without refetching the history list after every message.
 */
function chatPreview(question: string): string {
  let q = question.trim();
  for (const delimiter of ["？", "?", "。", "\n", "，", ","]) {
    if (q.includes(delimiter)) {
      q = q.split(delimiter)[0].trim();
      break;
    }
  }
  if (q.length <= 40) return q || t("chatDefault");
  const truncated = q.slice(0, 40);
  const lastSpace = truncated.lastIndexOf(" ");
  return `${(lastSpace > 20 ? truncated.slice(0, lastSpace) : truncated).trim()}…`;
}

function toDisplayMessage(msg: ChatHistoryMessage): DisplayMessage {
  const meta = msg.metadata ?? {};
  return {
    id: msg.id,
    role: msg.role,
    content: msg.content,
    timestamp: new Date(msg.created_at),
    sources: (meta.sources as string[] | undefined) ?? [],
    tool_trajectory: (meta.tool_trajectory as ToolTrajectoryStep[]) ?? [],
  };
}

let idSeq = 0;
const localId = () => `local_${Date.now()}_${++idSeq}`;

const DOCK_KEY = "gitreader_chat_dock";
const DESKTOP_QUERY = "(min-width: 1024px)";

/** Docked on desktop unless the reader collapsed it last time. */
function initialOpen(): boolean {
  try {
    if (!window.matchMedia(DESKTOP_QUERY).matches) return false;
    return localStorage.getItem(DOCK_KEY) !== "collapsed";
  } catch {
    return true;
  }
}

function rememberOpen(open: boolean) {
  try {
    if (window.matchMedia(DESKTOP_QUERY).matches) {
      localStorage.setItem(DOCK_KEY, open ? "open" : "collapsed");
    }
  } catch {
    // Preference only; nothing to do without storage.
  }
}

interface ChatPage {
  code: string;
  title: string;
  /** First file listed for the page, used in an example question. */
  file?: string;
}

interface ChatInterfaceProps {
  userId: string;
  repoUrl: string;
  page?: ChatPage;
  currentPageContext?: string;
  initialChatId?: string;
  /** `toc.byFile`, so answer notes can name the wiki chapters of a file. */
  filePages?: Map<string, TocNode[]>;
  onEvidence?: (path: string | null) => void;
}

const iconButtonClass =
  "inline-flex h-8 w-8 items-center justify-center rounded-full text-fg-muted transition-colors hover:bg-raised hover:text-fg aria-pressed:bg-accent-soft aria-pressed:text-accent";

function HistoryList({
  userId,
  repoUrl,
  items,
  loading,
  activeChatId,
  onOpen,
  onDeleted,
}: {
  userId: string;
  repoUrl: string;
  items: ChatHistoryItem[];
  loading: boolean;
  activeChatId?: string;
  onOpen: (item: ChatHistoryItem) => void;
  onDeleted: (chatId: string) => void;
}) {
  const [confirming, setConfirming] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const remove = async (chatId: string) => {
    setBusy(chatId);
    setError(null);
    try {
      await api.deleteChatHistory(chatId, userId);
      onDeleted(chatId);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
      setConfirming(null);
    }
  };

  if (loading) {
    return <p className="p-4 text-fg-muted text-sm">{t("loadingChat")}</p>;
  }
  if (items.length === 0) {
    return (
      <div className="p-4 text-sm">
        <p className="text-fg">{t("noChatHistory")}</p>
        <p className="mt-1 text-fg-muted">{t("startChatHint")}</p>
      </div>
    );
  }

  const dateFormat = new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });

  return (
    <div>
      <p className="px-4 pt-3 pb-1 font-mono text-fg-muted text-xs">
        {repoFullName(repoUrl)}
      </p>
      {error ? (
        <p role="alert" className="mx-4 my-2 font-mono text-fg text-xs">
          {t("deleteFailed")}: {error}
        </p>
      ) : null}
      <ul className="py-1">
        {items.map((item) => {
          const chatId = item.chat_id ?? item.id;
          const active = chatId === activeChatId;
          return (
            <li
              key={chatId}
              className={cn(
                "group flex items-stretch",
                active ? "bg-raised" : "hover:bg-raised",
              )}
            >
              <button
                type="button"
                onClick={() => onOpen(item)}
                aria-current={active ? "true" : undefined}
                className="min-w-0 flex-1 px-4 py-2 text-left"
              >
                <span className="block truncate text-fg text-sm">
                  {item.title || `${t("chatDefault")} ${chatId.slice(0, 8)}`}
                </span>
                <span className="block font-mono text-fg-muted text-xs tabular-nums">
                  {dateFormat.format(new Date(item.created_at))}
                </span>
              </button>
              {confirming === chatId ? (
                <button
                  type="button"
                  onClick={() => remove(chatId)}
                  disabled={busy === chatId}
                  className="shrink-0 px-3 font-medium text-fg text-xs underline underline-offset-2"
                >
                  {busy === chatId ? t("chatDeleting") : t("chatConfirmDelete")}
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => setConfirming(chatId)}
                  aria-label={t("chatDelete")}
                  title={t("chatDelete")}
                  className="shrink-0 px-3 text-fg-muted hover:text-fg [@media(hover:hover)]:opacity-0 [@media(hover:hover)]:focus-visible:opacity-100 [@media(hover:hover)]:group-hover:opacity-100"
                >
                  <Trash2 aria-hidden className="h-3.5 w-3.5" />
                </button>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function EmptyState({
  page,
  onAsk,
}: {
  page?: ChatPage;
  onAsk: (question: string) => void;
}) {
  const examples = page
    ? [
        t("chatExampleHow", { title: page.title }),
        t("chatExampleFiles", { title: page.title }),
        ...(page.file ? [t("chatExampleFile", { file: page.file })] : []),
      ]
    : [];
  return (
    <div className="px-4 py-6">
      <p className="font-medium text-fg text-xl leading-snug tracking-tight">
        {t("chatEmptyTitle")}
      </p>
      <p className="mt-2 text-fg-muted text-sm leading-relaxed">
        {t("chatEmptyDetail")}
      </p>
      {examples.length > 0 ? (
        <>
          <p className="mt-6 flex items-center gap-2 font-mono text-fg-muted text-xs">
            <span>{t("chatExamplesFor")}</span>
            <span className="text-fg-muted">{page?.code}</span>
          </p>
          <ul className="mt-2 space-y-1.5">
            {examples.map((question) => (
              <li key={question}>
                <button
                  type="button"
                  onClick={() => onAsk(question)}
                  className="w-full rounded-xl border border-line bg-raised px-3 py-2.5 text-left text-fg text-sm leading-snug transition-colors hover:border-accent-line"
                >
                  {question}
                </button>
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </div>
  );
}

export default function ChatInterface({
  userId,
  repoUrl,
  page,
  currentPageContext,
  initialChatId,
  filePages,
  onEvidence,
}: ChatInterfaceProps) {
  const [open, setOpenState] = useState(initialOpen);
  const [view, setView] = useState<"chat" | "history">("chat");
  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  const [chatId, setChatId] = useState<string | undefined>();
  const [input, setInput] = useState("");
  const [loadingChat, setLoadingChat] = useState(false);
  const [history, setHistory] = useState<ChatHistoryItem[] | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const { isStreaming, liveSteps, streamingAnswer, sendMessage, stop } =
    useChatStream();

  const setOpen = useCallback((next: boolean) => {
    setOpenState(next);
    rememberOpen(next);
  }, []);
  useRegisterShellChat(open, setOpen);

  // `/` opens the panel and puts the cursor in the question box. The focus
  // happens in an effect because the box may not be mounted yet.
  const [focusRequest, setFocusRequest] = useState(0);
  const askShortcut = useCallback(() => {
    setOpenState(true);
    setView("chat");
    setFocusRequest((n) => n + 1);
  }, []);
  useShortcut("/", askShortcut);
  useEffect(() => {
    if (focusRequest > 0) inputRef.current?.focus();
  }, [focusRequest]);

  const showChat = async (id: string) => {
    setView("chat");
    setLoadingChat(true);
    setMessages([]);
    try {
      const loaded = await api.getChatMessages(id);
      setMessages(loaded.map(toDisplayMessage));
      setChatId(id);
    } finally {
      setLoadingChat(false);
    }
  };

  // Deep link: /app/wiki/:taskId?chatId=… opens that conversation.
  useEffect(() => {
    if (!initialChatId) return;
    let cancelled = false;
    setOpenState(true);
    setLoadingChat(true);
    api
      .getChatMessages(initialChatId)
      .then((loaded) => {
        if (cancelled) return;
        setMessages(loaded.map(toDisplayMessage));
        setChatId(initialChatId);
      })
      .finally(() => {
        if (!cancelled) setLoadingChat(false);
      });
    return () => {
      cancelled = true;
    };
  }, [initialChatId]);

  // The history list loads the first time it is shown.
  const historyRequested = view === "history";
  const historyLoaded = history !== null;
  useEffect(() => {
    if (!historyRequested || historyLoaded) return;
    let cancelled = false;
    api.getChatHistory(userId).then((all) => {
      if (cancelled) return;
      const repo = normalizeRepoUrl(repoUrl);
      setHistory(all.filter((h) => normalizeRepoUrl(h.repo_url) === repo));
    });
    return () => {
      cancelled = true;
    };
  }, [historyRequested, historyLoaded, userId, repoUrl]);

  const newChat = () => {
    if (isStreaming) stop();
    setChatId(undefined);
    setMessages([]);
    setView("chat");
    inputRef.current?.focus();
  };

  /** Runs one turn. `retry` re-asks without adding the question again. */
  const ask = async (question: string, { retry = false } = {}) => {
    if (!question || isStreaming) return;
    setView("chat");
    if (!retry) {
      setMessages((prev) => [
        ...prev,
        {
          id: localId(),
          role: "user",
          content: question,
          timestamp: new Date(),
        },
      ]);
    }
    const startedNew = !chatId;
    try {
      const result = await sendMessage({
        user_id: userId,
        question,
        repo_url: repoUrl,
        chat_id: chatId,
        current_page_context: currentPageContext,
      });
      setChatId(result.chatId);
      if (startedNew) {
        const now = new Date().toISOString();
        setHistory((prev) =>
          prev
            ? [
                {
                  id: result.chatId,
                  chat_id: result.chatId,
                  user_id: userId,
                  repo_url: repoUrl,
                  created_at: now,
                  updated_at: now,
                  title: chatPreview(question),
                },
                ...prev,
              ]
            : prev,
        );
      }
      setMessages((prev) => [
        ...prev,
        {
          id: localId(),
          role: "assistant",
          content: result.answer,
          timestamp: new Date(),
          sources: result.sources,
          tool_trajectory: result.trajectory,
        },
      ]);
    } catch (err) {
      if (err instanceof ChatStreamStopped) {
        if (err.chatId) setChatId(err.chatId);
        setMessages((prev) => [
          ...prev,
          {
            id: localId(),
            role: "assistant",
            content: err.partialAnswer,
            timestamp: new Date(),
            tool_trajectory: err.trajectory,
            stopped: true,
          },
        ]);
        return;
      }
      setMessages((prev) => [
        ...prev,
        {
          id: localId(),
          role: "assistant",
          content: err instanceof Error ? err.message : String(err),
          timestamp: new Date(),
          isError: true,
        },
      ]);
    }
  };

  const submit = () => {
    const question = input.trim();
    if (!question || isStreaming) return;
    setInput("");
    void ask(question);
  };

  const last = messages[messages.length - 1];
  const retryQuestion =
    last?.isError && !isStreaming
      ? messages.findLast((m) => m.role === "user")?.content
      : undefined;
  const retry = () => {
    if (!retryQuestion) return;
    setMessages((prev) => prev.slice(0, -1));
    void ask(retryQuestion, { retry: true });
  };

  if (!open) {
    return (
      <>
        <div className="hidden w-12 shrink-0 flex-col items-center rounded-2xl border border-line bg-panel py-2 lg:flex">
          <button
            type="button"
            onClick={() => setOpen(true)}
            aria-label={t("chatOpen")}
            title={t("chatOpenHint")}
            className="flex flex-col items-center gap-2 px-2 py-3 text-fg-muted hover:bg-raised hover:text-fg"
          >
            <MessageSquare aria-hidden className="h-4 w-4" />
            <span className="text-xs [writing-mode:vertical-rl]">
              {t("chatAsk")}
            </span>
          </button>
        </div>
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="fixed right-4 bottom-4 z-40 inline-flex h-10 items-center gap-2 rounded-full bg-accent-strong px-4 font-medium text-accent-fg text-sm lg:hidden"
        >
          <MessageSquare aria-hidden className="h-4 w-4" />
          {t("chatAsk")}
        </button>
      </>
    );
  }

  return (
    <aside
      aria-label={t("chatPanel")}
      // Full screen below lg: Esc returns to the page.
      onKeyDown={(event) => {
        if (
          event.key === "Escape" &&
          !window.matchMedia(DESKTOP_QUERY).matches
        ) {
          event.stopPropagation();
          setOpen(false);
        }
      }}
      className="fixed inset-0 z-50 flex flex-col bg-panel lg:static lg:z-auto lg:min-w-[22rem] lg:flex-[2_1_0%] lg:overflow-hidden xl:min-w-[30rem] lg:rounded-2xl lg:border lg:border-line"
    >
      <header className="flex h-12 shrink-0 items-center gap-1 border-line border-b pr-2 pl-4">
        <h2 className="min-w-0 flex-1 truncate font-medium text-fg text-sm">
          {t("chatAsk")}
          {page ? (
            <span className="ml-2 font-mono text-fg-muted text-xs">
              {page.code}
            </span>
          ) : null}
        </h2>
        <button
          type="button"
          onClick={newChat}
          aria-label={t("chatNew")}
          title={t("chatNew")}
          className={iconButtonClass}
        >
          <Plus aria-hidden className="h-4 w-4" />
        </button>
        <button
          type="button"
          onClick={() => setView(view === "history" ? "chat" : "history")}
          aria-pressed={view === "history"}
          aria-label={t("chatHistory")}
          title={t("chatHistory")}
          className={iconButtonClass}
        >
          <History aria-hidden className="h-4 w-4" />
        </button>
        <button
          type="button"
          onClick={() => setOpen(false)}
          aria-label={t("chatCollapse")}
          title={t("chatCollapse")}
          className={iconButtonClass}
        >
          <PanelRightClose aria-hidden className="hidden h-4 w-4 lg:block" />
          <X aria-hidden className="h-4 w-4 lg:hidden" />
        </button>
      </header>

      {view === "history" ? (
        <div className="min-h-0 flex-1 overflow-y-auto">
          <HistoryList
            userId={userId}
            repoUrl={repoUrl}
            items={history ?? []}
            loading={history === null}
            activeChatId={chatId}
            onOpen={(item) => void showChat(item.chat_id ?? item.id)}
            onDeleted={(id) => {
              setHistory(
                (prev) => prev?.filter((h) => h.chat_id !== id) ?? null,
              );
              if (id === chatId) newChat();
            }}
          />
        </div>
      ) : (
        // column-reverse keeps the view pinned to the newest message while
        // the answer streams, without scroll effects.
        <div className="flex min-h-0 flex-1 flex-col-reverse overflow-y-auto">
          <div className="grow px-4 py-4">
            {loadingChat ? (
              <p className="text-fg-muted text-sm">{t("loadingChat")}</p>
            ) : messages.length === 0 && !isStreaming ? (
              <EmptyState page={page} onAsk={(q) => void ask(q)} />
            ) : (
              messages.map((message) => (
                <MessageItem
                  key={message.id}
                  message={message}
                  repoUrl={repoUrl}
                  filePages={filePages}
                  onEvidence={onEvidence}
                />
              ))
            )}

            {isStreaming ? (
              <LiveStepFlow
                steps={liveSteps}
                streamingAnswer={streamingAnswer}
              />
            ) : null}

            {retryQuestion ? (
              <button
                type="button"
                onClick={retry}
                className="mb-4 inline-flex items-center gap-2 rounded-full border border-line px-3 py-1.5 text-fg text-sm hover:border-line-strong"
              >
                <RotateCw aria-hidden className="h-3.5 w-3.5" />
                {t("retry")}
              </button>
            ) : null}
          </div>
        </div>
      )}

      <form
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
        className="shrink-0 p-3"
      >
        <div className="flex items-end gap-2 rounded-3xl border border-line bg-raised pl-2 focus-within:border-accent-line">
          <textarea
            ref={inputRef}
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                submit();
              }
            }}
            rows={2}
            placeholder={
              page
                ? t("chatPlaceholderPage", { code: page.code })
                : t("chatPlaceholder")
            }
            aria-label={t("chatInputLabel")}
            className="field-sizing-content max-h-40 min-h-[3.25rem] flex-1 resize-none bg-transparent px-3 py-2.5 text-fg text-sm leading-relaxed outline-none placeholder:text-fg-faint"
          />
          {isStreaming ? (
            <button
              type="button"
              onClick={stop}
              aria-label={t("chatStop")}
              title={t("chatStop")}
              className="m-1.5 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-fg text-bg"
            >
              <Square aria-hidden className="h-3 w-3 fill-current" />
            </button>
          ) : (
            <button
              type="submit"
              disabled={!input.trim()}
              aria-label={t("chatSend")}
              title={t("chatSend")}
              className="m-1.5 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-accent-strong text-accent-fg disabled:bg-overlay disabled:text-fg-muted"
            >
              <ArrowUp aria-hidden className="h-4 w-4" />
            </button>
          )}
        </div>
      </form>
    </aside>
  );
}
