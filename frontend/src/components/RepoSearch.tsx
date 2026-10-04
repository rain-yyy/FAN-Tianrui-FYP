"use client";

import { ArrowRight, Loader2, Search } from "lucide-react";
import {
  type FormEvent,
  type KeyboardEvent,
  useId,
  useMemo,
  useState,
} from "react";
import { useNavigate } from "react-router-dom";
import {
  RepoAvatar,
  type RepoData,
  RepoSummary,
  wikiHref,
} from "@/components/RepoGrid";
import { normalizeRepoUrl } from "@/lib/api";
import { t } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { prefetchRouteModule } from "@/router/prefetch";

const MAX_RESULTS = 8;

const GITHUB_URL =
  /^(?:https?:\/\/)?(?:www\.)?github\.com\/([\w.-]+)\/([\w.-]+?)(?:\.git)?(?:[/?#].*)?$/i;
const OWNER_NAME = /^([\w.-]+)\/([\w.-]+)$/;

/** `https://github.com/a/b/tree/main`, `github.com/a/b` or `a/b`. */
function parseGithubInput(text: string) {
  const match = GITHUB_URL.exec(text) ?? OWNER_NAME.exec(text);
  if (!match) return null;
  const [, owner, name] = match;
  return { owner, name, url: `https://github.com/${owner}/${name}` };
}

type Option =
  | { kind: "repo"; repo: RepoData }
  | { kind: "generate"; owner: string; name: string; url: string };

interface RepoSearchProps {
  repos: RepoData[];
  loading: boolean;
  value: string;
  onValueChange: (value: string) => void;
  /** Starts a wiki for a repository that has none yet. */
  onGenerate: (url: string) => void;
  submitting: boolean;
}

/** Home search: filters the user's wikis, or offers to generate a new one. */
export default function RepoSearch({
  repos,
  loading,
  value,
  onValueChange,
  onGenerate,
  submitting,
}: RepoSearchProps) {
  const navigate = useNavigate();
  const listId = useId();
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const query = value.trim();

  const options = useMemo<Option[]>(() => {
    if (!query) return [];
    const parsed = parseGithubInput(query);
    if (parsed) {
      const target = normalizeRepoUrl(parsed.url);
      const known = repos.find((r) => normalizeRepoUrl(r.url) === target);
      return known
        ? [{ kind: "repo", repo: known }]
        : [{ kind: "generate", ...parsed }];
    }
    const needle = query.toLowerCase();
    return repos
      .filter((r) => `${r.owner}/${r.name}`.toLowerCase().includes(needle))
      .slice(0, MAX_RESULTS)
      .map((repo) => ({ kind: "repo", repo }));
  }, [query, repos]);

  const expanded = open && query !== "" && !(loading && options.length === 0);
  const current = Math.min(active, options.length - 1);
  const optionId = (index: number) => `${listId}-${index}`;

  const choose = (option: Option | undefined) => {
    if (!option) return;
    setOpen(false);
    if (option.kind === "generate") {
      onGenerate(option.url);
      return;
    }
    prefetchRouteModule("wiki");
    navigate(wikiHref(option.repo));
  };

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    if (options.length === 0) {
      setOpen(true);
      return;
    }
    choose(options[current]);
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!expanded) {
        setOpen(true);
        return;
      }
      const step = event.key === "ArrowDown" ? 1 : -1;
      setActive((current + step + options.length) % options.length);
    } else if (event.key === "Escape" && expanded) {
      event.preventDefault();
      setOpen(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="relative mx-auto mt-10 max-w-2xl">
      <div
        className={cn(
          "flex h-14 items-center gap-2 rounded-full border border-line bg-panel/80 pr-2 pl-6 transition-colors",
          "shadow-[0_0_48px_rgb(243_128_32/0.12)] focus-within:border-accent-line",
        )}
      >
        <input
          type="text"
          role="combobox"
          aria-label={t("dashboardPlaceholder")}
          aria-autocomplete="list"
          aria-expanded={expanded}
          aria-controls={expanded ? listId : undefined}
          aria-activedescendant={
            expanded && options.length > 0 ? optionId(current) : undefined
          }
          autoComplete="off"
          spellCheck={false}
          placeholder={t("dashboardPlaceholder")}
          className="min-w-0 flex-1 bg-transparent text-fg outline-none placeholder:text-fg-faint"
          value={value}
          onChange={(event) => {
            onValueChange(event.target.value);
            setActive(0);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onBlur={() => setOpen(false)}
          onKeyDown={handleKeyDown}
          disabled={submitting}
        />
        <button
          type="submit"
          disabled={submitting || !query}
          aria-label={t("dashboardSearchLabel")}
          className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-fg-muted transition-colors hover:text-fg disabled:opacity-50"
        >
          {submitting ? (
            <Loader2 aria-hidden className="h-5 w-5 animate-spin" />
          ) : (
            <Search aria-hidden className="h-5 w-5" />
          )}
        </button>
      </div>

      {expanded ? (
        <div className="absolute inset-x-0 top-full z-20 mt-2 overflow-hidden rounded-2xl border border-line-strong bg-overlay text-left">
          {options.length === 0 ? (
            <p className="px-5 py-4 text-fg-muted text-sm">
              {t("dashboardSearchEmpty")}
            </p>
          ) : (
            <div
              id={listId}
              role="listbox"
              aria-label={t("dashboardRepos")}
              className="max-h-[60vh] overflow-y-auto p-1.5"
            >
              {options.map((option, index) => (
                // The field keeps focus and drives the keys (aria-activedescendant).
                <div
                  key={option.kind === "repo" ? option.repo.taskId : "generate"}
                  id={optionId(index)}
                  role="option"
                  tabIndex={-1}
                  aria-selected={index === current}
                  onMouseDown={(event) => event.preventDefault()}
                  onMouseMove={() => setActive(index)}
                  onClick={() => choose(option)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") choose(option);
                  }}
                  className={cn(
                    "flex cursor-pointer items-center gap-4 rounded-xl px-4 py-3",
                    index === current && "bg-raised",
                  )}
                >
                  {option.kind === "repo" ? (
                    <RepoSummary repo={option.repo} />
                  ) : (
                    <>
                      <RepoAvatar owner={option.owner} name={option.name} />
                      <span className="min-w-0 flex-1 truncate text-fg">
                        {t("dashboardGenerateFor", {
                          repo: `${option.owner}/${option.name}`,
                        })}
                      </span>
                      <ArrowRight
                        aria-hidden
                        className="h-4 w-4 shrink-0 text-accent"
                      />
                    </>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      ) : null}
    </form>
  );
}
