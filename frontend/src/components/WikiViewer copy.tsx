'use client';

import React, { useState, useEffect } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import { motion, AnimatePresence } from 'framer-motion';
import { Book, ChevronRight, Menu, Loader2, FileText, ChevronLeft } from 'lucide-react';
import axios from 'axios';
import Mermaid from './Mermaid';
import { cn } from '@/lib/utils';
import 'highlight.js/styles/github-dark.css'; // Import highlight.js style

// Types based on example.json and expected structure
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
  id: string; // Filename without extension or unique ID
  title: string;
  filename?: string; // Optional if id is filename
  children?: WikiStructureItem[]; // For nested structure
}

// Flexible type for structure response
type WikiStructure = WikiStructureItem[] | { [key: string]: any };

interface WikiViewerProps {
  structureUrl: string;
  contentUrls: string[];
}

// Debug mode
const DEBUG = true;

export default function WikiViewer({ structureUrl, contentUrls }: WikiViewerProps) {
  const [structure, setStructure] = useState<WikiStructureItem[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [pageContent, setPageContent] = useState<WikiPageContent | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingPage, setLoadingPage] = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Helper to transform R2 URLs to custom domain
  const transformUrl = (url: string) => {
    if (!url) return url;
    if (url.includes('r2.cloudflarestorage.com')) {
      try {
        const urlObj = new URL(url);
        // Extract pathname and add custom domain prefix
        return `https://cityu-fyp.livelive.fun${urlObj.pathname}`;
      } catch (e) {
        console.warn('Failed to transform URL:', url);
        return url;
      }
    }
    return url;
  };

  // Helper to extract filename from URL
  const extractFilename = (url: string): string | null => {
    if (!url) return null;
    try {
      const urlObj = new URL(url);
      const pathname = urlObj.pathname;
      // Extract filename from path (e.g., /Folo/20260127/sections/infrastructure.json -> infrastructure.json)
      const parts = pathname.split('/');
      return parts[parts.length - 1] || null;
    } catch (e) {
      // If URL parsing fails, try to extract from string directly
      const parts = url.split('/');
      return parts[parts.length - 1] || null;
    }
  };

  // Fetch Structure
  useEffect(() => {
    const fetchStructure = async () => {
      try {
        setLoading(true);
        // Transform URL and add timestamp
        const targetUrl = transformUrl(structureUrl);
        const res = await axios.get(`${targetUrl}?t=${Date.now()}`);
        let data = res.data;

        if (DEBUG) console.log('Wiki Structure:', data);

        // Normalize structure to array if it's not
        // This is a guess-work fallback, normally we expect array of items
        if (!Array.isArray(data)) {
             // If it's an object, try to convert keys to items or find a 'toc' or 'pages' key
             if (data.toc && Array.isArray(data.toc)) {
                 data = data.toc;
             } else if (data.pages && Array.isArray(data.pages)) {
                 data = data.pages;
             } else {
                 // Fallback: assume flat object where keys are IDs and values are titles or objects
                 data = Object.keys(data).map(key => ({
                     id: key,
                     title: typeof data[key] === 'string' ? data[key] : (data[key].title || key)
                 }));
             }
        }
        
        // Ensure we have valid items
        const normalized: WikiStructureItem[] = data.map((item: any) => {
          // Determine id: prefer id, then section_id, then filename without extension
          const id = item.id || item.section_id || item.filename?.replace('.json', '') || 'unknown';
          
          // Determine filename: prefer filename, then construct from id or section_id
          const filename = item.filename || 
                          (item.id ? `${item.id}.json` : null) || 
                          (item.section_id ? `${item.section_id}.json` : null);
          
          return {
            id,
            title: item.title || item.name || 'Untitled',
            filename,
            children: item.children
          };
        });

        setStructure(normalized);
        
        // Select first item by default
        if (normalized.length > 0) {
          setSelectedId(normalized[0].id);
        }
      } catch (err) {
        console.error('Failed to load wiki structure:', err);
        setError('Failed to load documentation structure.');
      } finally {
        setLoading(false);
      }
    };

    if (structureUrl) {
      fetchStructure();
    }
  }, [structureUrl]);

  // Fetch Page Content
  useEffect(() => {
    if (!selectedId || !structure.length || !contentUrls.length) return;

    const fetchPage = async () => {
      try {
        setLoadingPage(true);
        const item = findItemById(structure, selectedId);
        if (!item) throw new Error('Page not found in structure');

        // Construct expected filename patterns
        // Try multiple matching strategies:
        // 1. Use filename if present
        // 2. Use id + .json
        // 3. Match by filename without extension
        const expectedFilename = item.filename || `${item.id}.json`;
        const expectedFilenameWithoutExt = expectedFilename.replace('.json', '');
        const itemIdWithoutExt = item.id.replace('.json', '');
        
        // Find URL in contentUrls by matching filename
        // Extract filename from each URL and compare
        let matchedUrl: string | undefined;
        for (const url of contentUrls) {
          const urlFilename = extractFilename(url);
          if (!urlFilename) continue;
          
          const urlFilenameWithoutExt = urlFilename.replace('.json', '');
          
          // Try multiple matching strategies
          if (
            urlFilename === expectedFilename ||
            urlFilename === `${item.id}.json` ||
            urlFilenameWithoutExt === expectedFilenameWithoutExt ||
            urlFilenameWithoutExt === itemIdWithoutExt ||
            urlFilenameWithoutExt === item.id ||
            urlFilenameWithoutExt === item.filename?.replace('.json', '')
          ) {
            matchedUrl = url;
            break;
          }
        }

        if (!matchedUrl) {
          throw new Error(`Content URL not found for ${expectedFilename} (id: ${item.id})`);
        }

        // Transform R2 URL to custom domain URL
        const transformedUrl = transformUrl(matchedUrl);

        const res = await axios.get<WikiPageContent>(`${transformedUrl}?t=${Date.now()}`);
        if (DEBUG) console.log('Wiki Page Content:', res.data);
        setPageContent(res.data);
        setMobileMenuOpen(false); // Close mobile menu on selection
      } catch (err) {
        console.error('Failed to load page content:', err);
        // Don't set global error, just page error logic if needed
      } finally {
        setLoadingPage(false);
      }
    };

    fetchPage();
  }, [selectedId, contentUrls, structure]);

  // Helper to find item in potentially nested structure
  const findItemById = (items: WikiStructureItem[], id: string): WikiStructureItem | null => {
    for (const item of items) {
      if (item.id === id) return item;
      if (item.children) {
        const found = findItemById(item.children, id);
        if (found) return found;
      }
    }
    return null;
  };

  // Render Sidebar Item
  const SidebarItem = ({ item, depth = 0 }: { item: WikiStructureItem, depth?: number }) => {
    const isSelected = selectedId === item.id;
    const hasChildren = item.children && item.children.length > 0;
    
    return (
      <li>
        <button
          onClick={() => setSelectedId(item.id)}
          className={cn(
            "w-full text-left px-3 py-2 rounded-lg text-sm transition-all duration-200 flex items-center gap-2",
            isSelected 
              ? "bg-primary/20 text-blue-300 font-medium" 
              : "text-muted-foreground hover:bg-white/5 hover:text-white",
            depth > 0 && "ml-4 border-l border-white/10"
          )}
        >
          {hasChildren ? <Book className="w-4 h-4 shrink-0" /> : <FileText className="w-4 h-4 shrink-0 opacity-70" />}
          <span className="truncate">{item.title}</span>
        </button>
        {hasChildren && (
          <ul className="mt-1 space-y-1">
            {item.children!.map(child => (
              <SidebarItem key={child.id} item={child} depth={depth + 1} />
            ))}
          </ul>
        )}
      </li>
    );
  };

  if (loading && !structure.length) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <Loader2 className="w-8 h-8 text-blue-500 animate-spin" />
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
  // console.log(pageContent.content)

  return (
    <div className="flex h-[80vh] w-full max-w-[1400px] bg-secondary/30 backdrop-blur-xl border border-white/10 rounded-2xl overflow-hidden shadow-2xl">
      {/* Sidebar - Desktop */}
      <aside className="hidden md:flex w-64 lg:w-72 flex-col border-r border-white/10 bg-black/20">
        <div className="p-4 border-b border-white/10">
          <h2 className="font-semibold text-white/90 flex items-center gap-2">
            <Book className="w-5 h-5 text-blue-400" />
            Documentation
          </h2>
        </div>
        <nav className="flex-1 overflow-y-auto p-3 custom-scrollbar">
          <ul className="space-y-1">
            {structure.map(item => (
              <SidebarItem key={item.id} item={item} />
            ))}
          </ul>
        </nav>
      </aside>

      {/* Mobile Header */}
      <div className="md:hidden absolute top-0 left-0 right-0 h-14 bg-secondary/80 border-b border-white/10 flex items-center px-4 z-20 backdrop-blur">
        <button onClick={() => setMobileMenuOpen(true)} className="p-2 -ml-2 text-white/70">
          <Menu className="w-6 h-6" />
        </button>
        <span className="ml-2 font-medium truncate">{pageContent?.title || 'Loading...'}</span>
      </div>

      {/* Mobile Sidebar */}
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
              className="absolute top-0 bottom-0 left-0 w-64 bg-secondary border-r border-white/10 z-40 md:hidden flex flex-col"
            >
              <div className="p-4 border-b border-white/10 flex justify-between items-center">
                 <h2 className="font-semibold text-white/90">Documentation</h2>
                 <button onClick={() => setMobileMenuOpen(false)}>
                   <ChevronLeft className="w-5 h-5" />
                 </button>
              </div>
              <nav className="flex-1 overflow-y-auto p-3">
                <ul className="space-y-1">
                  {structure.map(item => (
                    <SidebarItem key={item.id} item={item} />
                  ))}
                </ul>
              </nav>
            </motion.aside>
          </>
        )}
      </AnimatePresence>

      {/* Main Content */}
      <main className="flex-1 relative overflow-hidden flex flex-col bg-transparent">
        {loadingPage ? (
          <div className="absolute inset-0 flex items-center justify-center bg-black/10 backdrop-blur-sm z-10">
            <Loader2 className="w-8 h-8 text-blue-500 animate-spin" />
          </div>
        ) : null}

        <div className="flex-1 overflow-y-auto p-4 md:p-8 custom-scrollbar scroll-smooth">
          {pageContent ? (
            <div className="max-w-4xl mx-auto space-y-8 pb-10 mt-10 md:mt-0">
              {/* Header */}
              <div className="space-y-2 border-b border-white/10 pb-6">
                <div className="flex items-center gap-2 text-sm text-muted-foreground mb-2">
                   {pageContent.breadcrumb.split('/').map((part, i, arr) => (
                     <React.Fragment key={i}>
                       <span className={i === arr.length - 1 ? "text-blue-400" : ""}>{part.trim()}</span>
                       {i < arr.length - 1 && <ChevronRight className="w-3 h-3 opacity-50" />}
                     </React.Fragment>
                   ))}
                </div>
                <h1 className="text-3xl md:text-4xl font-bold text-white tracking-tight">
                  {pageContent.title}
                </h1>
              </div>

              {/* Intro */}
              <div className="prose prose-invert prose-lg max-w-none prose-headings:font-bold prose-headings:text-white prose-p:text-gray-300 prose-a:text-blue-400 hover:prose-a:text-blue-300 prose-code:text-blue-200 prose-pre:bg-black/40 prose-pre:border prose-pre:border-white/10 prose-img:rounded-xl">
                <ReactMarkdown 
                  remarkPlugins={[remarkGfm]}
                  rehypePlugins={[rehypeHighlight]}
                >
                  {pageContent.content.intro}
                </ReactMarkdown>
              </div>

              {/* Mermaid Diagram */}
              {pageContent.content.mermaid && (
                <div className="my-8">
                  <div className="text-sm text-muted-foreground mb-2 font-mono uppercase tracking-widest text-xs">Workflow Diagram</div>
                  <Mermaid chart={pageContent.content.mermaid} />
                </div>
              )}

              {/* Sections */}
              <div className="space-y-12">
                {pageContent.content.sections.map((section, idx) => (
                  <section key={idx} className="scroll-mt-20">
                    <h2 className="text-2xl font-bold text-white mb-4 flex items-center gap-2 group">
                      <span className="w-1 h-6 bg-blue-500 rounded-full group-hover:h-8 transition-all duration-300"/>
                      {section.heading}
                    </h2>
                    <div className="prose prose-invert prose-lg max-w-none prose-p:text-gray-300 prose-pre:bg-black/40 prose-pre:border prose-pre:border-white/10">
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
             <div className="flex flex-col items-center justify-center h-full text-muted-foreground">
               <FileText className="w-12 h-12 mb-4 opacity-20" />
               <p>Select a page to view content</p>
             </div>
          )}
        </div>
      </main>
    </div>
  );
}
