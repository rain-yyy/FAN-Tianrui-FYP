"use client";

import {
  Check,
  CircleHelp,
  History,
  Link2,
  LogOut,
  Search,
} from "lucide-react";
import Image from "next/image";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { Link, NavLink, Outlet, useMatch } from "react-router-dom";
import { ShortcutHelp } from "@/components/wiki/ShortcutHelp";
import { useShortcut } from "@/hooks/useShortcut";
import { ShellProvider, useShellRepo } from "@/layouts/ShellContext";
import { t } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { useAuth } from "@/providers/AuthProvider";
import { prefetchRouteModule } from "@/router/prefetch";

const iconButtonClass =
  "inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-line text-fg-muted transition-colors hover:border-line-strong hover:text-fg";

function SearchPill() {
  const repo = useShellRepo();
  const onHome = useMatch("/app/dashboard") !== null;
  // The home page carries its own large search field.
  if (onHome) return <div className="flex-1" />;

  return (
    <div className="flex min-w-0 flex-1 justify-center">
      <Link
        to="/app/dashboard"
        onMouseEnter={() => prefetchRouteModule("dashboard")}
        onFocus={() => prefetchRouteModule("dashboard")}
        className="flex h-10 w-full max-w-md items-center gap-3 rounded-full border border-line bg-panel px-4 text-sm transition-colors hover:border-accent"
      >
        <span
          className={cn(
            "min-w-0 flex-1 truncate",
            repo ? "text-fg" : "text-fg-muted",
          )}
        >
          {repo ? repo.fullName : t("shellFindRepo")}
        </span>
        <Search aria-hidden className="h-4 w-4 shrink-0 text-fg-muted" />
      </Link>
    </div>
  );
}

function CopyLinkButton() {
  const [copied, setCopied] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(window.location.href);
    } catch {
      return;
    }
    setCopied(true);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setCopied(false), 1500);
  };

  return (
    <button
      type="button"
      onClick={copy}
      className="inline-flex h-9 items-center gap-2 rounded-full border border-line px-3 text-fg-muted text-sm transition-colors hover:border-line-strong hover:text-fg"
    >
      {copied ? (
        <Check aria-hidden className="h-4 w-4 text-accent" />
      ) : (
        <Link2 aria-hidden className="h-4 w-4" />
      )}
      <span className="hidden sm:inline">
        {copied ? t("shellLinkCopied") : t("shellCopyLink")}
      </span>
    </button>
  );
}

function UserMenu() {
  const { user, signOut } = useAuth();
  const identity = user?.email ?? user?.phone ?? user?.id ?? t("unknownUser");
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuId = useId();

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setOpen(false);
      triggerRef.current?.focus();
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  return (
    <div className="relative" ref={rootRef}>
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        aria-label={t("userMenu")}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        title={identity}
        className={cn(
          iconButtonClass,
          "font-mono text-sm uppercase",
          open && "border-accent text-fg",
        )}
      >
        {identity.slice(0, 1)}
      </button>

      {open ? (
        <div
          id={menuId}
          role="menu"
          aria-label={t("userMenu")}
          className="absolute top-full right-0 z-50 mt-2 w-64 overflow-hidden rounded-xl border border-line bg-panel text-fg"
        >
          <div className="border-line border-b px-4 py-3">
            <p className="text-fg-muted text-xs">{t("signedInAs")}</p>
            <p className="mt-0.5 truncate text-sm font-medium" title={identity}>
              {identity}
            </p>
          </div>
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false);
              void signOut();
            }}
            className="flex w-full items-center gap-2 px-4 py-2.5 text-left text-sm hover:bg-raised focus-visible:bg-raised"
          >
            <LogOut aria-hidden className="h-4 w-4 text-fg-muted" />
            {t("signOut")}
          </button>
        </div>
      ) : null}
    </div>
  );
}

function TopBar({ onHelp }: { onHelp: () => void }) {
  const inWiki = useMatch("/app/wiki/*") !== null;
  return (
    <header className="z-40 flex h-16 shrink-0 items-center gap-3 px-4 md:gap-4 md:px-6">
      <Link
        to="/app/dashboard"
        aria-label={t("shellHome")}
        onMouseEnter={() => prefetchRouteModule("dashboard")}
        className="flex shrink-0 items-center gap-2.5"
      >
        <Image src="/logo.png" alt="" width={28} height={28} priority />
        <span className="hidden font-medium text-lg tracking-tight sm:block">
          GitReader
        </span>
      </Link>

      <SearchPill />

      <nav aria-label={t("shellNav")} className="flex items-center gap-2">
        <NavLink
          to="/app/history"
          title={t("history")}
          onMouseEnter={() => prefetchRouteModule("history")}
          onFocus={() => prefetchRouteModule("history")}
          className={({ isActive }) =>
            cn(
              "inline-flex h-9 items-center gap-2 rounded-full border px-3 text-sm transition-colors",
              isActive
                ? "border-accent text-fg"
                : "border-line text-fg-muted hover:border-line-strong hover:text-fg",
            )
          }
        >
          <History aria-hidden className="h-4 w-4" />
          <span className="hidden sm:inline">{t("history")}</span>
        </NavLink>
        {inWiki ? <CopyLinkButton /> : null}
        <button
          type="button"
          onClick={onHelp}
          aria-label={t("shortcutTitle")}
          title={t("shortcutTitle")}
          className={iconButtonClass}
        >
          <CircleHelp aria-hidden className="h-4 w-4" />
        </button>
        <UserMenu />
      </nav>
    </header>
  );
}

export default function AppLayout() {
  // The wiki is a full-bleed reading desk with its own scroll columns.
  const fullBleed = useMatch("/app/wiki/*") !== null;
  const [helpOpen, setHelpOpen] = useState(false);
  const openHelp = useCallback(() => setHelpOpen(true), []);
  const closeHelp = useCallback(() => setHelpOpen(false), []);
  useShortcut("?", openHelp);

  return (
    <ShellProvider>
      <div className="flex h-dvh flex-col bg-bg">
        <TopBar onHelp={openHelp} />
        <main
          className={cn(
            "relative flex min-h-0 min-w-0 flex-1 flex-col overflow-y-auto",
            !fullBleed && "px-4 pb-8 md:px-6",
          )}
        >
          <Outlet />
        </main>
      </div>
      {helpOpen ? <ShortcutHelp onClose={closeHelp} /> : null}
    </ShellProvider>
  );
}
