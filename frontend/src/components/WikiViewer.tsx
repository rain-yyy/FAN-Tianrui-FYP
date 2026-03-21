'use client';

import React, { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import { motion, AnimatePresence } from 'framer-motion';
import { Book, ChevronRight, Menu, Loader2, FileText, ChevronLeft } from 'lucide-react';
import Mermaid from './Mermaid';
import ChatInterface from './ChatInterface';
import { cn } from '@/lib/utils';
import 'highlight.js/styles/github-dark.css';

// Simple in-memory cache for wiki content
const contentCache = new Map<string, { content: WikiPageContent; timestamp: number }>();
const CACHE_TTL_MS = 5 * 60 * 1000; // 5 minutes

const getCachedContent = (key: string): WikiPageContent | null => {
  const cached = contentCache.get(key);
  if (!cached) return null;
  if (Date.now() - cached.timestamp > CACHE_TTL_MS) {
    contentCache.delete(key);
    return null;
  }
  return cached.content;
};

const setCachedContent = (key: string, content: WikiPageContent): void => {
  // Limit cache size
  if (contentCache.size > 50) {
    const oldestKey = contentCache.keys().next().value;
    if (oldestKey) contentCache.delete(oldestKey);
  }
  contentCache.set(key, { content, timestamp: Date.now() });
};

// --- Types ---

interface Section {
  heading: string;
  body: string;
}

interface WikiPageContent {
  section_id: string;
  title: string;
  breadcrumb: string;
  files: string[];
  content: {
    intro: string;
    sections: Section[];
    mermaid?: string;
  };
}

interface WikiStructureItem {
  id: string;
  title: string;
  filename?: string;
  children?: WikiStructureItem[];
}

interface WikiViewerProps {
  userId: string;
  structureUrl: string;
  contentUrls: string[];
  repoUrl?: string;
  initialChatId?: string;
  onChatLoaded?: () => void;
}

// --- Helper Components ---

const SidebarItem = React.memo(({ 
  item, 
  depth = 0, 
  selectedId, 
  onSelect 
}: { 
  item: WikiStructureItem; 
  depth?: number;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) => {
  const isSelected = selectedId === item.id;
  const hasChildren = item.children && item.children.length > 0;

  return (
    <li>
      <button
        onClick={() => onSelect(item.id)}
        className={cn(
          "w-[90%] text-left px-3 py-2 rounded-lg text-sm transition-all duration-200 flex items-center gap-2",
          isSelected
            ? "bg-fuchsia-500/15 text-fuchsia-200 font-medium border border-fuchsia-300/25"
            : "text-zinc-400 hover:bg-white/5 hover:text-white border border-transparent",
          depth > 0 && "ml-4 border-l border-white/15"
        )}
        aria-label={`Select ${item.title}`}
      >
        {hasChildren ? <Book className="w-4 h-4 shrink-0" /> : <FileText className="w-4 h-4 shrink-0 opacity-70" />}
        <span className="truncate">{item.title}</span>
      </button>
      {hasChildren && (
        <ul className="mt-1 space-y-1">
          {item.children!.map(child => (
            <SidebarItem 
              key={child.id} 
              item={child} 
              depth={depth + 1} 
              selectedId={selectedId}
              onSelect={onSelect}
            />
          ))}
        </ul>
      )}
    </li>
  );
});

SidebarItem.displayName = 'SidebarItem';

// --- Utils ---

const transformUrl = (url: string) => {
  if (!url) return url;
  if (url.includes('r2.cloudflarestorage.com')) {
    try {
      const urlObj = new URL(url);
      return `https://cityu-fyp.livelive.fun${urlObj.pathname}`;
    } catch {
      return url;
    }
  }
  return url;
};

const extractFilename = (url: string): string | null => {
  if (!url) return null;
  try {
    const urlObj = new URL(url);
    const parts = urlObj.pathname.split('/');
    return parts[parts.length - 1] || null;
  } catch {
    const parts = url.split('/');
    return parts[parts.length - 1] || null;
  }
};

const parseContentSafely = (rawContent: unknown): WikiPageContent['content'] => {
  try {
    if (
      rawContent &&
      typeof rawContent === 'object' &&
      'intro' in rawContent &&
      typeof (rawContent as { intro: unknown }).intro === 'string' &&
      'sections' in rawContent &&
      Array.isArray((rawContent as { sections: unknown }).sections)
    ) {
      const contentObj = rawContent as { intro: string; sections: Section[]; mermaid?: string };
      if (contentObj.intro.trim().startsWith('{')) {
        try {
          const parsedIntro = JSON.parse(contentObj.intro) as { intro?: string; sections?: Section[]; mermaid?: string };
          return {
            intro: parsedIntro.intro || contentObj.intro,
            sections: parsedIntro.sections || contentObj.sections || [],
            mermaid: parsedIntro.mermaid || contentObj.mermaid || ''
          };
        } catch {
          return {
            intro: contentObj.intro,
            sections: contentObj.sections || [],
            mermaid: contentObj.mermaid || ''
          };
        }
      }
      return {
        intro: contentObj.intro,
        sections: contentObj.sections || [],
        mermaid: contentObj.mermaid || ''
      };
    }

    if (typeof rawContent === 'string') {
      const parsed = JSON.parse(rawContent);
      return parseContentSafely(parsed);
    }

    return { intro: '', sections: [], mermaid: '' };
  } catch {
    return { intro: '', sections: [], mermaid: '' };
  }
};

// --- Main Component ---

export default function WikiViewer({ userId, structureUrl, contentUrls, repoUrl, initialChatId, onChatLoaded }: WikiViewerProps) {
  const [structure, setStructure] = useState<WikiStructureItem[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [pageContent, setPageContent] = useState<WikiPageContent | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingPage, setLoadingPage] = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSelectId = useCallback((id: string) => {
    setSelectedId(id);
    setMobileMenuOpen(false);
  }, []);

  // Track if structure has been loaded to avoid re-fetching
  const structureLoadedRef = useRef(false);
  const lastStructureUrlRef = useRef<string>('');

  useEffect(() => {
    // Only fetch structure once per URL change
    if (structureLoadedRef.current && lastStructureUrlRef.current === structureUrl) {
      return;
    }

    const fetchStructure = async () => {
      try {
        setLoading(true);
        const targetUrl = transformUrl(structureUrl);
        // Use browser cache, don't add timestamp
        const res = await fetch(targetUrl, {
          cache: 'default',
        });
        if (!res.ok) throw new Error('Failed to fetch structure');
        let data = await res.json();

        if (!Array.isArray(data)) {
          if (data.toc && Array.isArray(data.toc)) {
            data = data.toc;
          } else if (data.pages && Array.isArray(data.pages)) {
            data = data.pages;
          } else {
            data = Object.keys(data).map(key => ({
              id: key,
              title: typeof data[key] === 'string' ? data[key] : (data[key].title || key)
            }));
          }
        }

        const normalized: WikiStructureItem[] = (data as { id?: string; section_id?: string; filename?: string; title?: string; name?: string; children?: WikiStructureItem[] }[]).map((item) => {
          const id = item.id || item.section_id || item.filename?.replace('.json', '') || 'unknown';
          const filename = item.filename || `${id}.json`;
          return {
            id,
            title: item.title || item.name || 'Untitled',
            filename,
            children: item.children
          };
        });

        setStructure(normalized);
        structureLoadedRef.current = true;
        lastStructureUrlRef.current = structureUrl;
        
        // Only set initial selectedId if none is set
        if (normalized.length > 0 && !selectedId) {
          setSelectedId(normalized[0].id);
        }
      } catch {
        setError('Failed to load documentation structure.');
      } finally {
        setLoading(false);
      }
    };

    if (structureUrl) fetchStructure();
  }, [structureUrl]); // Removed selectedId from dependencies

  const findItemById = useCallback((items: WikiStructureItem[], id: string): WikiStructureItem | null => {
    for (const item of items) {
      if (item.id === id) return item;
      if (item.children) {
        const found = findItemById(item.children, id);
        if (found) return found;
      }
    }
    return null;
  }, []);

  useEffect(() => {
    if (!selectedId || !structure.length || !contentUrls.length) return;

    const fetchPage = async () => {
      try {
        const item = findItemById(structure, selectedId);
        if (!item) throw new Error('Page not found in structure');

        const expectedFilename = item.filename || `${item.id}.json`;
        const expectedFilenameWithoutExt = expectedFilename.replace('.json', '');

        let matchedUrl: string | undefined;
        for (const url of contentUrls) {
          const urlFilename = extractFilename(url);
          const urlFilenameWithoutExt = urlFilename?.replace('.json', '');

          if (
            urlFilename === expectedFilename ||
            urlFilenameWithoutExt === expectedFilenameWithoutExt ||
            urlFilenameWithoutExt === item.id
          ) {
            matchedUrl = url;
            break;
          }
        }

        if (!matchedUrl) throw new Error(`Content URL not found for ${expectedFilename}`);

        const transformedUrl = transformUrl(matchedUrl);
        const cacheKey = `${transformedUrl}`;

        // Check cache first
        const cachedContent = getCachedContent(cacheKey);
        if (cachedContent) {
          setPageContent(cachedContent);
          return;
        }

        // Cache miss, fetch from network
        setLoadingPage(true);
        const res = await fetch(transformedUrl, {
          cache: 'default', // Use browser cache
        });
        if (!res.ok) throw new Error('Failed to fetch page content');
        const data = await res.json();

        const parsedContent = parseContentSafely(data.content);
        const pageData: WikiPageContent = { ...data, content: parsedContent };
        
        // Store in cache
        setCachedContent(cacheKey, pageData);
        setPageContent(pageData);
      } catch {
        setError('Failed to load page content.');
      } finally {
        setLoadingPage(false);
      }
    };

    fetchPage();
  }, [selectedId, contentUrls, structure, findItemById]);

  const currentPageContext = useMemo(() => {
    if (!pageContent) return undefined;
    
    // Build a compact context with only essential information
    const contextParts: string[] = [];
    
    // Page title and path (most important)
    contextParts.push(`Page: ${pageContent.title}`);
    if (pageContent.breadcrumb) {
      contextParts.push(`Path: ${pageContent.breadcrumb}`);
    }
    
    // Related files (important for code understanding)
    if (pageContent.files?.length > 0) {
      const topFiles = pageContent.files.slice(0, 3);
      contextParts.push(`Related files: ${topFiles.join(', ')}`);
    }
    
    // Only include intro if it's short and meaningful (avoid long intros)
    if (pageContent.content.intro) {
      const firstSentence = pageContent.content.intro.split(/[.!?。！？]\s*/)[0];
      if (firstSentence && firstSentence.length < 150) {
        contextParts.push(`Summary: ${firstSentence.trim()}`);
      }
    }
    
    // Section headings as keywords (compact)
    if (pageContent.content.sections?.length > 0) {
      const headings = pageContent.content.sections
        .slice(0, 4)
        .map(s => s.heading)
        .join(', ');
      if (headings) {
        contextParts.push(`Sections: ${headings}`);
      }
    }
    
    return contextParts.join('\n');
  }, [pageContent]);

  if (loading && !structure.length) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <Loader2 className="w-8 h-8 text-fuchsia-300 animate-spin" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center min-h-[400px] text-red-400">
        <p>{error}</p>
      </div>
    );
  }

  return (
    <>
      <div className="flex h-[80vh] w-full max-w-[1400px] bg-[linear-gradient(150deg,rgba(21,14,42,0.72),rgba(10,10,24,0.72))] backdrop-blur-2xl border border-white/15 rounded-3xl overflow-hidden shadow-[0_30px_100px_rgba(76,29,149,0.35)]">
        <aside className="hidden md:flex w-64 lg:w-72 flex-col border-r border-white/15 bg-black/25">
          <div className="p-4 border-b border-white/15">
            <h2 className="font-semibold text-white/90 flex items-center gap-2">
              <Book className="w-5 h-5 text-fuchsia-300" />
              Documentation
            </h2>
          </div>
          <nav className="flex-1 overflow-y-auto p-3 custom-scrollbar">
            <ul className="space-y-1">
              {structure.map(item => (
                <SidebarItem 
                  key={item.id} 
                  item={item} 
                  selectedId={selectedId} 
                  onSelect={handleSelectId} 
                />
              ))}
            </ul>
          </nav>
        </aside>

        <div className="md:hidden absolute top-0 left-0 right-0 h-14 bg-black/45 border-b border-white/15 flex items-center px-4 z-20 backdrop-blur-xl">
          <button 
            onClick={() => setMobileMenuOpen(true)} 
            className="p-2 -ml-2 text-zinc-200"
            aria-label="Open menu"
          >
            <Menu className="w-6 h-6" />
          </button>
          <span className="ml-2 font-medium truncate">{pageContent?.title || 'Loading...'}</span>
        </div>

        <AnimatePresence>
          {mobileMenuOpen && (
            <>
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                onClick={() => setMobileMenuOpen(false)}
                className="absolute inset-0 bg-black/60 z-30 md:hidden backdrop-blur-sm"
              />
              <motion.aside
                initial={{ x: '-100%' }}
                animate={{ x: 0 }}
                exit={{ x: '-100%' }}
                className="absolute top-0 bottom-0 left-0 w-64 bg-[linear-gradient(160deg,rgba(16,11,31,0.98),rgba(9,8,20,0.98))] border-r border-white/15 z-40 md:hidden flex flex-col"
              >
                <div className="p-4 border-b border-white/15 flex justify-between items-center">
                  <h2 className="font-semibold text-white/90">Documentation</h2>
                  <button onClick={() => setMobileMenuOpen(false)} aria-label="Close menu">
                    <ChevronLeft className="w-5 h-5" />
                  </button>
                </div>
                <nav className="flex-1 overflow-y-auto p-3">
                  <ul className="space-y-1">
                    {structure.map(item => (
                      <SidebarItem 
                        key={item.id} 
                        item={item} 
                        selectedId={selectedId} 
                        onSelect={handleSelectId} 
                      />
                    ))}
                  </ul>
                </nav>
              </motion.aside>
            </>
          )}
        </AnimatePresence>

        <main className="flex-1 relative overflow-hidden flex flex-col bg-transparent">
          {loadingPage && (
            <div className="absolute inset-0 flex items-center justify-center bg-black/30 backdrop-blur-sm z-10">
              <Loader2 className="w-8 h-8 text-fuchsia-300 animate-spin" />
            </div>
          )}

          <div className="flex-1 overflow-y-auto p-4 md:p-8 custom-scrollbar scroll-smooth">
            {pageContent ? (
              <div className="max-w-4xl mx-auto space-y-8 pb-10 mt-10 md:mt-0">
                <div className="space-y-2 border-b border-white/15 pb-6">
                  <div className="flex items-center gap-2 text-sm text-zinc-400 mb-2">
                    {pageContent.breadcrumb.split('/').map((part, i, arr) => (
                      <React.Fragment key={i}>
                        <span className={i === arr.length - 1 ? "text-fuchsia-200" : ""}>{part.trim()}</span>
                        {i < arr.length - 1 && <ChevronRight className="w-3 h-3 opacity-50" />}
                      </React.Fragment>
                    ))}
                  </div>
                  <h1 className="text-3xl md:text-4xl font-bold text-white tracking-tight">
                    {pageContent.title}
                  </h1>
                </div>

                <div className="prose prose-invert prose-lg max-w-none prose-headings:font-bold prose-headings:text-white prose-p:text-zinc-300 prose-a:text-fuchsia-300 hover:prose-a:text-fuchsia-200 prose-code:text-cyan-200 prose-pre:bg-black/45 prose-pre:border prose-pre:border-white/15 prose-img:rounded-xl">
                  <ReactMarkdown
                    remarkPlugins={[remarkGfm]}
                    rehypePlugins={[rehypeHighlight]}
                  >
                    {pageContent.content.intro}
                  </ReactMarkdown>
                </div>

                {pageContent.content.mermaid && (
                  <div className="my-8">
                    <div className="text-sm text-zinc-400 mb-2 font-mono uppercase tracking-widest text-xs">Workflow Diagram</div>
                    <Mermaid chart={pageContent.content.mermaid} />
                  </div>
                )}

                <div className="space-y-12">
                  {pageContent.content.sections.map((section, idx) => (
                    <section key={idx} className="scroll-mt-20">
                      <h2 className="text-2xl font-bold text-white mb-4 flex items-center gap-2 group">
                        <span className="w-1 h-6 bg-gradient-to-b from-fuchsia-400 to-cyan-400 rounded-full group-hover:h-8 transition-all duration-300"/>
                        {section.heading}
                      </h2>
                      <div className="prose prose-invert prose-lg max-w-none prose-p:text-zinc-300 prose-pre:bg-black/45 prose-pre:border prose-pre:border-white/15">
                        <ReactMarkdown
                          remarkPlugins={[remarkGfm]}
                          rehypePlugins={[rehypeHighlight]}
                        >
                          {section.body}
                        </ReactMarkdown>
                      </div>
                    </section>
                  ))}
                </div>
              </div>
            ) : (
              <div className="flex flex-col items-center justify-center h-full text-zinc-400">
                <FileText className="w-12 h-12 mb-4 opacity-20" />
                <p>Select a page to view content</p>
              </div>
            )}
          </div>
        </main>
      </div>
      
      {repoUrl && (
        <ChatInterface
          userId={userId}
          repoUrl={repoUrl}
          currentPageContext={currentPageContext}
          currentPageTitle={pageContent?.title}
          initialChatId={initialChatId}
          onChatLoaded={onChatLoaded}
        />
      )}
    </>
  );
}
