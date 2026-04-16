'use client';

import React, { useEffect, useState } from 'react';
import { ArrowRight, Plus, Star, Loader2 } from 'lucide-react';
import { api, RepoGithubMetadataEntry, normalizeRepoUrl } from '@/lib/api';
import { useNavigate } from 'react-router-dom';

interface RepoData {
  owner: string;
  name: string;
  url: string;
  taskId: string;
  /** 来自 `repositories` 表（GitHub 简介优先，否则 LLM description） */
  dbDescription?: string | null;
  dbStars?: number | null;
}

const RepoCard = ({
  repo,
  githubMeta,
  metaLoading,
}: {
  repo: RepoData;
  githubMeta: RepoGithubMetadataEntry | undefined;
  metaLoading: boolean;
}) => {
  const navigate = useNavigate();
  const stars = githubMeta?.stars ?? repo.dbStars ?? null;
  const description =
    githubMeta?.description ??
    (typeof repo.dbDescription === 'string' && repo.dbDescription.trim() ? repo.dbDescription : null);

  const handleCardClick = () => {
    navigate(`/app/wiki/${repo.taskId}?repo=${encodeURIComponent(repo.url)}`);
  };

  return (
    <div
      onClick={handleCardClick}
      className="group relative flex flex-col justify-between rounded-xl bg-white border border-stone-200 p-4 hover:bg-stone-50 hover:border-stone-300 transition-all cursor-pointer h-[160px] shadow-sm"
    >
      <div>
        <div className="flex items-start justify-between gap-2">
          <div className="font-medium text-stone-900 truncate w-full pr-8">
            {repo.owner} / {repo.name}
          </div>
          <div className="absolute right-4 top-4 shrink-0 inline-flex items-center justify-center w-8 h-8 rounded-full bg-stone-100 text-stone-500 group-hover:bg-sky-100 group-hover:text-sky-800 transition-colors">
            <ArrowRight className="w-4 h-4" />
          </div>
        </div>

        <div className="mt-2 text-sm text-stone-600 line-clamp-3 h-[60px]">
          {description ||
            (metaLoading ? (
              <div className="animate-pulse h-4 bg-stone-100 rounded w-3/4" />
            ) : (
              'No description available'
            ))}
        </div>
      </div>

      <div className="mt-auto flex items-center gap-4 text-xs text-stone-500">
        {metaLoading ? (
          <div className="animate-pulse h-3 bg-stone-100 rounded w-12" />
        ) : (
          <div className="flex items-center gap-1" aria-label={stars !== null ? `Stars ${stars}` : 'Stars unknown'}>
            <Star className="w-3 h-3 text-stone-500" />
            <span>
              {stars !== null ? (stars >= 1000 ? `${(stars / 1000).toFixed(1)}k` : stars) : '—'}
            </span>
          </div>
        )}
      </div>
    </div>
  );
};

export default function RepoGrid({ userId, onAddRepo }: { userId: string; onAddRepo: () => void }) {
  const [repos, setRepos] = useState<RepoData[]>([]);
  const [loading, setLoading] = useState(true);
  const [githubMetaByUrl, setGithubMetaByUrl] = useState<Record<string, RepoGithubMetadataEntry>>({});
  const [metaLoading, setMetaLoading] = useState(true);

  useEffect(() => {
    const loadDashboardRepos = async () => {
      try {
        const response = await api.getDashboardRepos(userId);
        const list: RepoData[] = [];

        for (const entry of response.repos) {
          if (!entry.repo_url || !entry.task_id) continue;
          try {
            const urlObj = new URL(entry.repo_url);
            const parts = urlObj.pathname.split('/').filter(Boolean);
            if (parts.length < 2) continue;
            const owner = parts[0];
            const name = parts[1];
            const g = entry.github_short_description;
            const d = entry.description;
            const dbDescription =
              typeof g === 'string' && g.trim()
                ? g.trim()
                : typeof d === 'string' && d.trim()
                  ? d.trim()
                  : null;
            const rawStars = entry.stargazers_count;
            const dbStars = typeof rawStars === 'number' && !Number.isNaN(rawStars) ? rawStars : null;
            list.push({
              owner,
              name,
              url: entry.repo_url,
              taskId: entry.task_id,
              dbDescription,
              dbStars,
            });
          } catch {
            // ignore invalid urls
          }
        }

        setRepos(list);

        setMetaLoading(true);
        const urls = [...new Set(list.map((r) => normalizeRepoUrl(r.url)).filter(Boolean))];
        if (urls.length === 0) {
          setGithubMetaByUrl({});
          setMetaLoading(false);
        } else {
          try {
            const meta = await api.getRepoGithubMetadata(urls);
            setGithubMetaByUrl(meta);
          } catch (e) {
            console.error('Failed to load repo metadata:', e);
            setGithubMetaByUrl({});
          } finally {
            setMetaLoading(false);
          }
        }
      } catch (error) {
        console.error('Failed to load dashboard repositories:', error);
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
      <div className="flex justify-center py-10">
        <Loader2 className="w-8 h-8 text-sky-600 animate-spin" />
      </div>
    );
  }

  return (
    <div className="grid gap-4 grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
      <div
        onClick={onAddRepo}
        className="group flex flex-col justify-between rounded-xl bg-sky-50 border border-sky-200/80 p-4 hover:bg-sky-100/80 hover:border-sky-300 transition-all cursor-pointer h-[160px] shadow-sm"
      >
        <div>
          <div className="w-8 h-8 rounded-full bg-white border border-sky-200 flex items-center justify-center text-sky-700 mb-3 group-hover:scale-110 transition-transform shadow-sm">
            <Plus className="w-5 h-5" />
          </div>
          <div className="font-medium text-sky-950">Add repo</div>
        </div>
        <div className="self-end text-sky-700 group-hover:translate-x-1 transition-transform">
          <ArrowRight className="w-5 h-5" />
        </div>
      </div>

      {repos.map((repo) => (
        <RepoCard
          key={`${repo.owner}/${repo.name}`}
          repo={repo}
          githubMeta={githubMetaByUrl[normalizeRepoUrl(repo.url)]}
          metaLoading={metaLoading}
        />
      ))}
    </div>
  );
}
