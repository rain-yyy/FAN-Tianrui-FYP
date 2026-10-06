"use client";

import {
  BookOpen,
  Code2,
  Link2,
  Loader2,
  MessageSquare,
  Star,
} from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import type { DashboardRepoEntry } from "@/lib/api";
import { t } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { prefetchRouteModule } from "@/router/prefetch";

export interface RepoData {
  owner: string;
  name: string;
  url: string;
  taskId: string;
  description: string | null;
  stars: number | null;
}

const trimmed = (value: unknown) =>
  typeof value === "string" && value.trim() ? value.trim() : null;

/** Dashboard entries → rows; skips entries without a usable GitHub path. */
export function parseRepos(entries: DashboardRepoEntry[]): RepoData[] {
  const list: RepoData[] = [];
  for (const entry of entries) {
    if (!entry.repo_url || !entry.task_id) continue;
    let parts: string[];
    try {
      parts = new URL(entry.repo_url).pathname.split("/").filter(Boolean);
    } catch {
      continue;
    }
    if (parts.length < 2) continue;
    const stars = entry.stargazers_count;
    list.push({
      owner: parts[0],
      name: parts[1],
      url: entry.repo_url,
      taskId: entry.task_id,
      description:
        trimmed(entry.github_short_description) ?? trimmed(entry.description),
      stars: typeof stars === "number" && !Number.isNaN(stars) ? stars : null,
    });
  }
  return list;
}

export const wikiHref = (repo: RepoData) =>
  `/app/wiki/${repo.taskId}?repo=${encodeURIComponent(repo.url)}`;

const formatStars = (stars: number) =>
  stars >= 1000 ? `${(stars / 1000).toFixed(1)}k` : String(stars);

/** Owner avatar from GitHub; two letters of the name when it fails to load. */
export function RepoAvatar({ owner, name }: { owner: string; name: string }) {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return (
      <span
        aria-hidden
        className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-raised font-mono text-fg-muted text-xs uppercase"
      >
        {name.slice(0, 2)}
      </span>
    );
  }
  return (
    // biome-ignore lint/performance/noImgElement: tiny external avatar, next/image would need a remote pattern
    <img
      src={`https://github.com/${encodeURIComponent(owner)}.png?size=64`}
      alt=""
      width={32}
      height={32}
      loading="lazy"
      onError={() => setFailed(true)}
      className="h-8 w-8 shrink-0 rounded-lg bg-raised"
    />
  );
}

/** Avatar, name over owner, description and stars: shared by list and search. */
export function RepoSummary({ repo }: { repo: RepoData }) {
  return (
    <>
      <RepoAvatar owner={repo.owner} name={repo.name} />
      <span className="min-w-0 sm:w-48 sm:shrink-0">
        <span className="block truncate font-medium text-fg">{repo.name}</span>
        <span className="block truncate text-fg-muted text-sm">
          {repo.owner}
        </span>
      </span>
      <span className="line-clamp-2 hidden min-w-0 flex-1 text-fg-muted text-sm sm:block">
        {repo.description || t("dashboardNoDescription")}
      </span>
      {repo.stars !== null ? (
        <span className="ml-auto inline-flex shrink-0 items-center gap-1.5 font-mono text-fg-muted text-sm tabular-nums">
          <Star aria-hidden className="h-3.5 w-3.5" />
          {formatStars(repo.stars)}
        </span>
      ) : null}
    </>
  );
}

const FEATURES = [
  { icon: BookOpen, label: "featureRead" },
  { icon: Code2, label: "featureTrace" },
  { icon: MessageSquare, label: "featureAsk" },
  { icon: Link2, label: "featureShare" },
] as const;

/** Shown while the user has no repositories yet. */
function FeatureCards() {
  return (
    <div className="mx-auto mt-6 hidden max-w-3xl grid-cols-4 gap-4 pb-8 md:grid">
      {FEATURES.map(({ icon: Icon, label }, index) => (
        <div
          key={label}
          className={cn(
            "flex aspect-square flex-col items-center justify-center gap-3 rounded-2xl border border-line bg-panel text-center",
            index % 2 === 1 && "translate-y-6 border-accent-line",
          )}
        >
          <Icon aria-hidden className="h-7 w-7 text-accent" strokeWidth={1.5} />
          <span className="text-fg-muted text-sm">{t(label)}</span>
        </div>
      ))}
    </div>
  );
}

export default function RepoGrid({
  repos,
  loading,
}: {
  repos: RepoData[];
  loading: boolean;
}) {
  if (loading) {
    return (
      <div className="relative flex justify-center py-12">
        <Loader2 aria-hidden className="h-6 w-6 animate-spin text-fg-muted" />
      </div>
    );
  }

  if (repos.length === 0) return <FeatureCards />;

  return (
    <section className="relative mx-auto mt-16 max-w-3xl pb-8">
      <h2 className="mb-3 font-mono text-fg-faint text-xs uppercase tracking-wider">
        {t("dashboardRepos")}
      </h2>
      <ul className="space-y-3">
        {repos.map((repo) => (
          <li key={repo.taskId}>
            <Link
              to={wikiHref(repo)}
              onMouseEnter={() => prefetchRouteModule("wiki")}
              onFocus={() => prefetchRouteModule("wiki")}
              className="flex items-center gap-4 rounded-2xl border border-line bg-panel px-5 py-4 transition-colors hover:border-accent-line"
            >
              <RepoSummary repo={repo} />
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
