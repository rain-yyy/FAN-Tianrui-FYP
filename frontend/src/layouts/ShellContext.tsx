"use client";

import {
  createContext,
  type ReactNode,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

interface ShellRepo {
  /** Full `owner/name` label, e.g. `tiangolo/fastapi`. */
  fullName: string;
  /** Route of the wiki workspace that registered this repo. */
  href: string;
}

interface ShellChat {
  open: boolean;
  setOpen: (open: boolean) => void;
}

interface ShellContextValue {
  repo: ShellRepo | null;
  setRepo: (repo: ShellRepo | null) => void;
  chat: ShellChat | null;
  setChat: (chat: ShellChat | null) => void;
}

const ShellContext = createContext<ShellContextValue | null>(null);

export function ShellProvider({ children }: { children: ReactNode }) {
  const [repo, setRepo] = useState<ShellRepo | null>(null);
  const [chat, setChat] = useState<ShellChat | null>(null);
  const value = useMemo(() => ({ repo, setRepo, chat, setChat }), [repo, chat]);
  return (
    <ShellContext.Provider value={value}>{children}</ShellContext.Provider>
  );
}

export function useShellRepo(): ShellRepo | null {
  return useContext(ShellContext)?.repo ?? null;
}

export function useShellChat(): ShellChat | null {
  return useContext(ShellContext)?.chat ?? null;
}

/** `https://github.com/owner/name(.git)` → `owner/name`. */
export function repoFullName(repoUrl: string): string {
  const trimmed = repoUrl
    .trim()
    .replace(/\.git$/, "")
    .replace(/\/+$/, "");
  const parts = trimmed.split("/").filter(Boolean);
  return parts.slice(-2).join("/") || trimmed;
}

/**
 * Lets a workspace page show its repository in the shell rail while mounted.
 * Pass an empty `repoUrl` until the repo is known.
 */
export function useRegisterShellRepo(repoUrl: string, href: string) {
  const setRepo = useContext(ShellContext)?.setRepo;
  useEffect(() => {
    if (!setRepo || !repoUrl) return;
    setRepo({ fullName: repoFullName(repoUrl), href });
    return () => setRepo(null);
  }, [setRepo, repoUrl, href]);
}

/**
 * Lets the chat panel report its open state to the top bar's Chat button
 * while mounted. `setOpen` must be stable.
 */
export function useRegisterShellChat(
  open: boolean,
  setOpen: (open: boolean) => void,
) {
  const setChat = useContext(ShellContext)?.setChat;
  useEffect(() => {
    if (!setChat) return;
    setChat({ open, setOpen });
    return () => setChat(null);
  }, [setChat, open, setOpen]);
}
