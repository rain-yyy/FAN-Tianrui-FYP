'use client';

import React, { useEffect, useState } from 'react';
import { ArrowRight, Plus, Star, GitFork, Loader2 } from 'lucide-react';
import { api, TaskStatusResponse } from '@/lib/api';
import { useNavigate } from 'react-router-dom';
import { cn } from '@/lib/utils';

interface RepoData {
  owner: string;
  name: string;
  description: string;
  stars: number;
  url: string;
  taskId: string;
}

const RepoCard = ({ repo }: { repo: RepoData }) => {
  const navigate = useNavigate();
  const [stars, setStars] = useState<number | null>(null);
  const [description, setDescription] = useState<string | null>(null);
  const [loadingMetadata, setLoadingMetadata] = useState(true);

  useEffect(() => {
    let isMounted = true;
    const fetchMetadata = async () => {
      // Avoid fetching if we already have data or if it's a private repo (simple check)
      // For now, we try to fetch public metadata from GitHub API
      // We use a simple cache key to avoid refetching in same session if possible, 
      // but here we just fetch.
      try {
        const res = await fetch(`https://api.github.com/repos/${repo.owner}/${repo.name}`);
        if (res.ok) {
          const data = await res.json();
          if (isMounted) {
            setStars(data.stargazers_count);
            setDescription(data.description);
          }
        }
      } catch (error) {
        // Ignore errors
      } finally {
        if (isMounted) setLoadingMetadata(false);
      }
    };

    fetchMetadata();
    return () => { isMounted = false; };
  }, [repo.owner, repo.name]);

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
          {description || (loadingMetadata ? <div className="animate-pulse h-4 bg-stone-100 rounded w-3/4" /> : 'No description available')}
        </div>
      </div>

      <div className="mt-auto flex items-center gap-4 text-xs text-stone-500">
        {stars !== null ? (
          <div className="flex items-center gap-1">
            <Star className="w-3 h-3 text-stone-500" />
            <span>{stars >= 1000 ? `${(stars / 1000).toFixed(1)}k` : stars}</span>
          </div>
        ) : loadingMetadata ? (
             <div className="animate-pulse h-3 bg-stone-100 rounded w-12" />
        ) : null}
      </div>
    </div>
  );
};

export default function RepoGrid({ userId, onAddRepo }: { userId: string; onAddRepo: () => void }) {
  const [repos, setRepos] = useState<RepoData[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const loadTasks = async () => {
      try {
        const response = await api.getTasks(userId);
        // Filter completed tasks and deduplicate by repo_url
        const uniqueMap = new Map<string, RepoData>();
        
        // Sort by date desc (assuming API returns in some order, or we sort manually)
        // TaskStatusResponse has created_at
        const sortedTasks = response.tasks.sort((a, b) => 
          new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
        );

        for (const task of sortedTasks) {
          if (task.status !== 'completed' && task.status !== 'cached') continue;
          if (!task.repo_url) continue;

          // Normalize URL to get owner/name
          // Try to parse: https://github.com/owner/name
          try {
            const urlObj = new URL(task.repo_url);
            const parts = urlObj.pathname.split('/').filter(Boolean);
            if (parts.length >= 2) {
              const owner = parts[0];
              const name = parts[1];
              const key = `${owner}/${name}`.toLowerCase();
              
              if (!uniqueMap.has(key)) {
                uniqueMap.set(key, {
                  owner,
                  name,
                  description: '',
                  stars: 0,
                  url: task.repo_url,
                  taskId: task.task_id // Use the most recent task ID for this repo
                });
              }
            }
          } catch (e) {
            // ignore invalid urls
          }
        }
        setRepos(Array.from(uniqueMap.values()));
      } catch (error) {
        console.error('Failed to load tasks:', error);
      } finally {
        setLoading(false);
      }
    };

    if (userId) {
      loadTasks();
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
      {/* Add Repo Card */}
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

      {/* Repo Cards */}
      {repos.map((repo) => (
        <RepoCard key={`${repo.owner}/${repo.name}`} repo={repo} />
      ))}
    </div>
  );
}
