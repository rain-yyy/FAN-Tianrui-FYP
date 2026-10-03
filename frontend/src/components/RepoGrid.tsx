"use client";

import {
  BookOpen,
  Code2,
  Link2,
  Loader2,
  MessageSquare,
  Star,
} from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "@/lib/api";
import { t } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { prefetchRouteModule } from "@/router/prefetch";

interface RepoData {
  owner: string;
  name: string;
  url: string;
  taskId: string;
  description: string | null;
  stars: number | null;
}

const formatStars = (stars: number) =>
  stars >= 1000 ? `${(stars / 1000).toFixed(1)}k` : String(stars);

function RepoRow({ repo }: { repo: RepoData }) {
  return (
    <Link
      to={`/app/wiki/${repo.taskId}?repo=${encodeURIComponent(repo.url)}`}
      onMouseEnter={() => prefetchRouteModule("wiki")}
      onFocus={() => prefetchRouteModule("wiki")}
      className="flex items-center gap-4 rounded-2xl border border-line bg-panel px-5 py-4 transition-colors hover:border-accent-line"
    >
      <span
        aria-hidden
        className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-raised font-mono text-fg-muted text-sm uppercase"
      >
        {repo.name.slice(0, 2)}
      </span>
      <span className="min-w-0 sm:w-56 sm:shrink-0">
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
    </Link>
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

export default function RepoGrid({ userId }: { userId: string }) {
  const [repos, setRepos] = useState<RepoData[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const loadDashboardRepos = async () => {
      try {
        const response = await api.getDashboardRepos(userId);
        const list: RepoData[] = [];

        for (const entry of response.repos) {
          if (!entry.repo_url || !entry.task_id) continue;
          try {
            const urlObj = new URL(entry.repo_url);
            const parts = urlObj.pathname.split("/").filter(Boolean);
            if (parts.length < 2) continue;

            const g = entry.github_short_description;
            const d = entry.description;
            const description =
              typeof g === "string" && g.trim()
                ? g.trim()
                : typeof d === "string" && d.trim()
                  ? d.trim()
                  : null;

            const rawStars = entry.stargazers_count;
            const stars =
              typeof rawStars === "number" && !Number.isNaN(rawStars)
                ? rawStars
                : null;

            list.push({
              owner: parts[0],
              name: parts[1],
              url: entry.repo_url,
              taskId: entry.task_id,
              description,
              stars,
            });
          } catch {
            // ignore invalid urls
          }
        }

        setRepos(list);
      } catch (error) {
        console.error("Failed to load dashboard repositories:", error);
      } finally {
        setLoading(false);
      }
    };

    if (userId) {
      void loadDashboardRepos();
    }
  }, [userId]);

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
          <li key={`${repo.owner}/${repo.name}`}>
            <RepoRow repo={repo} />
          </li>
        ))}
      </ul>
    </section>
  );
}
