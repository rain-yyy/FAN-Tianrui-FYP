"use client";

import { History, LayoutGrid, LogOut, type LucideIcon } from "lucide-react";
import Image from "next/image";
import { useEffect, useId, useRef, useState } from "react";
import { Link, NavLink, Outlet, useMatch } from "react-router-dom";
import { ShellProvider, useShellRepo } from "@/layouts/ShellContext";
import { t } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { useAuth } from "@/providers/AuthProvider";
import { prefetchRouteModule } from "@/router/prefetch";

type PrefetchKey = Parameters<typeof prefetchRouteModule>[0];

interface RailLinkProps {
  to: string;
  icon: LucideIcon;
  label: string;
  title: string;
  prefetch?: PrefetchKey;
}

const railItemClass = (isActive: boolean) =>
  cn(
    "relative flex flex-col items-center justify-center gap-1 rounded-md",
    "h-12 w-12 md:h-14 md:w-14 text-xs font-medium transition-colors",
    isActive
      ? "bg-rail-raised text-rail-ink"
      : "text-rail-muted hover:bg-rail-raised/60 hover:text-rail-ink",
  );

function RailLink({ to, icon: Icon, label, title, prefetch }: RailLinkProps) {
  return (
    <NavLink
      to={to}
      title={title}
      onMouseEnter={prefetch ? () => prefetchRouteModule(prefetch) : undefined}
      onFocus={prefetch ? () => prefetchRouteModule(prefetch) : undefined}
      className={({ isActive }) => railItemClass(isActive)}
    >
      {({ isActive }) => (
        <>
          {isActive ? (
            <span
              aria-hidden
              className="absolute inset-y-2 -left-1 w-0.5 rounded-full bg-rail-ink md:-left-2"
            />
          ) : null}
          <Icon aria-hidden className="h-5 w-5" strokeWidth={1.75} />
          <span className="leading-none">{label}</span>
        </>
      )}
    </NavLink>
  );
}

function RepoMark() {
  const repo = useShellRepo();
  const isWiki = useMatch("/app/wiki/*") !== null;
  if (!repo) return null;

  const name = repo.fullName.split("/").pop() ?? repo.fullName;
  return (
    <Link
      to={repo.href}
      title={repo.fullName}
      aria-label={t("shellCurrentRepo", { repo: repo.fullName })}
      aria-current={isWiki ? "page" : undefined}
      className={railItemClass(isWiki)}
    >
      <span
        aria-hidden
        className="flex h-6 w-6 items-center justify-center rounded-sm border border-rail-line font-mono text-xs uppercase"
      >
        {name.slice(0, 2)}
      </span>
      <span className="max-w-full truncate px-1 font-mono leading-none">
        {name}
      </span>
    </Link>
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
          "flex h-9 w-9 items-center justify-center rounded-full border font-mono text-sm uppercase transition-colors",
          open
            ? "border-rail-ink bg-rail-raised text-rail-ink"
            : "border-rail-line text-rail-muted hover:border-rail-muted hover:text-rail-ink",
        )}
      >
        {identity.slice(0, 1)}
      </button>

      {open ? (
        <div
          id={menuId}
          role="menu"
          aria-label={t("userMenu")}
          className="absolute top-full right-0 z-50 mt-2 w-64 border border-n-3 bg-sheet text-ink shadow-[0_1px_0_var(--color-n-2)] md:top-auto md:right-auto md:bottom-0 md:left-full md:mt-0 md:ml-3"
        >
          <div className="border-n-2 border-b px-4 py-3">
            <p className="text-n-6 text-xs">{t("signedInAs")}</p>
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
            className="flex w-full items-center gap-2 px-4 py-2.5 text-left text-sm hover:bg-n-1 focus-visible:bg-n-1"
          >
            <LogOut aria-hidden className="h-4 w-4 text-n-6" />
            {t("signOut")}
          </button>
        </div>
      ) : null}
    </div>
  );
}

function Rail() {
  return (
    <header className="z-40 flex h-14 shrink-0 items-center gap-2 bg-rail px-2 text-rail-ink md:h-dvh md:w-20 md:flex-col md:gap-3 md:px-0 md:py-3">
      <Link
        to="/app/dashboard"
        aria-label={t("shellHome")}
        title="GitReader"
        className="flex shrink-0 flex-col items-center gap-1 rounded-md p-1 md:w-full"
      >
        <span className="flex h-9 w-9 items-center justify-center rounded-full bg-sheet">
          <Image src="/logo.png" alt="" width={28} height={28} priority />
        </span>
        <span className="hidden font-serif text-xs tracking-wide md:block">
          GitReader
        </span>
      </Link>

      <div
        aria-hidden
        className="mx-1 h-8 w-px bg-rail-line md:mx-0 md:h-px md:w-10"
      />

      <nav
        aria-label={t("shellNav")}
        className="flex flex-1 items-center gap-1 md:flex-none md:flex-col"
      >
        <RepoMark />
        <RailLink
          to="/app/dashboard"
          icon={LayoutGrid}
          label={t("navRepos")}
          title={t("dashboard")}
          prefetch="dashboard"
        />
        <RailLink
          to="/app/history"
          icon={History}
          label={t("history")}
          title={t("history")}
          prefetch="history"
        />
      </nav>

      <div className="md:mt-auto">
        <UserMenu />
      </div>
    </header>
  );
}

export default function AppLayout() {
  // The wiki is a full-bleed reading desk with its own scroll columns.
  const fullBleed = useMatch("/app/wiki/*") !== null;
  return (
    <ShellProvider>
      <div className="flex h-dvh flex-col bg-paper md:flex-row">
        <a
          href="#main"
          className="sr-only z-50 bg-rail px-3 py-2 text-rail-ink text-sm focus:not-sr-only focus:fixed focus:top-2 focus:left-2"
        >
          {t("skipToContent")}
        </a>
        <Rail />
        <main
          id="main"
          tabIndex={-1}
          className={cn(
            "relative flex min-h-0 min-w-0 flex-1 flex-col overflow-y-auto",
            !fullBleed && "p-4 md:p-8",
          )}
        >
          <Outlet />
        </main>
      </div>
    </ShellProvider>
  );
}
