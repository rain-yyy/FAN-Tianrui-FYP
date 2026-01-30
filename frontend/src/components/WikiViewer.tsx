'use client';

import React, { useState, useEffect, useMemo } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import { motion, AnimatePresence } from 'framer-motion';
import { Book, ChevronRight, Menu, Loader2, FileText, ChevronLeft } from 'lucide-react';
import axios from 'axios';
import Mermaid from './Mermaid';
import ChatInterface from './ChatInterface';
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
  repoUrl?: string;  // 仓库 URL，用于 RAG 聊天功能
}

// Debug mode
const DEBUG = true;

export default function WikiViewer({ structureUrl, contentUrls, repoUrl }: WikiViewerProps) {
  const [structure, setStructure] = useState<WikiStructureItem[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [pageContent, setPageContent] = useState<WikiPageContent | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingPage, setLoadingPage] = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);



  // 🆕 添加内容解析函数
  const parseContentSafely = (rawContent: any): WikiPageContent['content'] => {
    try {
      // 如果 content 本身就是正确格式，直接返回
      if (
        rawContent &&
        typeof rawContent === 'object' &&
        typeof rawContent.intro === 'string' &&
        Array.isArray(rawContent.sections)
      ) {
        // 检查 intro 是否被意外序列化
        if (rawContent.intro.trim().startsWith('{')) {
          console.log('🔄 检测到 intro 字段可能被序列化，尝试解析...');
          try {
            const parsedIntro = JSON.parse(rawContent.intro);
            console.log('✅ intro 解析成功：', parsedIntro);
            return {
              intro: parsedIntro.intro || rawContent.intro,
              sections: parsedIntro.sections || rawContent.sections || [],
              mermaid: parsedIntro.mermaid || rawContent.mermaid || ''
            };
          } catch (e) {
            console.log('⚠️ intro 解析失败，使用原始值');
            return {
              intro: rawContent.intro,
              sections: rawContent.sections || [],
              mermaid: rawContent.mermaid || ''
            };
          }
        }
        
        return {
          intro: rawContent.intro,
          sections: rawContent.sections || [],
          mermaid: rawContent.mermaid || ''
        };
      }

      // 如果整个 content 是字符串，尝试解析
      if (typeof rawContent === 'string') {
        console.log('🔄 检测到整个 content 被序列化为字符串，尝试解析...');
        const parsed = JSON.parse(rawContent);
        console.log('✅ content 解析成功：', parsed);
        
        // 递归检查解析后的内容
        return parseContentSafely(parsed);
      }

      // 兜底返回
      console.warn('⚠️ content 格式异常，返回默认值');
      return {
        intro: '',
        sections: [],
        mermaid: ''
      };
    } catch (e) {
      console.error('❌ parseContentSafely 解析失败：', e);
      return {
        intro: '',
        sections: [],
        mermaid: ''
      };
    }
  };

  useEffect(() => {
    const fetchStructure = async () => {
      console.log('🚀 [1] 开始加载结构文件：', structureUrl);

      try {
        setLoading(true);
        const targetUrl = structureUrl;
        console.log('🧩 [1.1] 结构文件 URL：', targetUrl);
        const res = await axios.get(`${targetUrl}?t=${Date.now()}`);
        let data = res.data;

        console.log('📦 [1.2] 原始结构数据：', data);

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

        const normalized: WikiStructureItem[] = data.map((item: any) => {
          const id = item.id || item.section_id || item.filename?.replace('.json', '') || 'unknown';
          const filename = item.filename || `${id}.json`;
          return {
            id,
            title: item.title || item.name || 'Untitled',
            filename,
            children: item.children
          };
        });

        console.log('✅ [1.3] 结构数据标准化结果：', normalized);

        setStructure(normalized);

        if (normalized.length > 0) {
          console.log('🎯 [1.4] 默认选中第一个 ID：', normalized[0].id);
          setSelectedId(normalized[0].id);
        }
      } catch (err) {
        console.error('❌ [1-ERROR] 加载结构文件失败：', err);
        setError('Failed to load documentation structure.');
      } finally {
        setLoading(false);
      }
    };

    if (structureUrl) {
      fetchStructure();
    }
  }, [structureUrl]);

  useEffect(() => {
    if (!selectedId || !structure.length || !contentUrls.length) return;
    console.log('🚀 [2] 开始加载页面内容：', selectedId);

    const fetchPage = async () => {
      try {
        setLoadingPage(true);
        const item = findItemById(structure, selectedId);
        console.log('🔍 [2.1] 找到的结构项：', item);

        if (!item) throw new Error('Page not found in structure');

        // 扁平化 structure 以找到索引
        const flattenStructure = (items: WikiStructureItem[]): WikiStructureItem[] => {
          const result: WikiStructureItem[] = [];
          const traverse = (items: WikiStructureItem[]) => {
            for (const item of items) {
              result.push(item);
              if (item.children) {
                traverse(item.children);
              }
            }
          };
          traverse(items);
          return result;
        };

        const flatStructure = flattenStructure(structure);
        const index = flatStructure.findIndex(item => item.id === selectedId);

        if (index === -1 || index >= contentUrls.length) {
          console.error('❌ [2.2] 未找到匹配的内容 URL：', selectedId, contentUrls);
          throw new Error(`Content URL not found for ${selectedId}`);
        }

        const matchedUrl = contentUrls[index];
        console.log('🌐 [2.3] 直接使用后端返回的 URL（通过索引匹配）：', matchedUrl);

        const res = await axios.get<WikiPageContent>(`${matchedUrl}?t=${Date.now()}`);
        // console.log('📦 [2.6] 请求返回的原始页面内容：', res.data);
        // console.log('📦 [2.6.1] content 类型：', typeof res.data.content);
        // console.log('📦 [2.6.2] content.intro 类型：', typeof res.data.content?.intro);
        // console.log('📦 [2.6.3] content.intro 前 200 字符：', 
        //   typeof res.data.content?.intro === 'string' 
        //     ? res.data.content.intro.substring(0, 200) 
        //     : res.data.content?.intro
        // );

        // 🆕 使用安全解析函数处理 content
        const parsedContent = parseContentSafely(res.data.content);
        console.log('✅ [2.7] 解析后的 content：', parsedContent);

        const finalPageContent: WikiPageContent = {
          ...res.data,
          content: parsedContent
        };

        console.log('✅ [2.8] 最终设置的 pageContent：', finalPageContent);
        setPageContent(finalPageContent);
      } catch (err) {
        console.error('❌ [2-ERROR] 加载页面失败：', err);
        setError('Failed to load page content.');
      } finally {
        setLoadingPage(false);
      }
    };

    fetchPage();
  }, [selectedId, contentUrls, structure]);

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

  const SidebarItem = ({ item, depth = 0 }: { item: WikiStructureItem; depth?: number }) => {
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

  // 为聊天接口构建当前页面的上下文
  const currentPageContext = useMemo(() => {
    if (!pageContent) return undefined;
    
    // 构建一个简洁的上下文描述
    const contextParts = [
      `当前页面: ${pageContent.title}`,
      `路径: ${pageContent.breadcrumb}`,
    ];
    
    // 添加页面简介的前几句
    if (pageContent.content.intro) {
      const introPreview = pageContent.content.intro.substring(0, 300);
      contextParts.push(`页面简介: ${introPreview}${pageContent.content.intro.length > 300 ? '...' : ''}`);
    }
    
    // 添加关联文件信息
    if (pageContent.files && pageContent.files.length > 0) {
      contextParts.push(`关联文件: ${pageContent.files.slice(0, 5).join(', ')}`);
    }
    
    return contextParts.join('\n');
  }, [pageContent]);

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

  return (
    <>
    <div className="flex h-[80vh] w-full max-w-[1400px] bg-secondary/30 backdrop-blur-xl border border-white/10 rounded-2xl overflow-hidden shadow-2xl">
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

      <div className="md:hidden absolute top-0 left-0 right-0 h-14 bg-secondary/80 border-b border-white/10 flex items-center px-4 z-20 backdrop-blur">
        <button onClick={() => setMobileMenuOpen(true)} className="p-2 -ml-2 text-white/70">
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

      <main className="flex-1 relative overflow-hidden flex flex-col bg-transparent">
        {loadingPage ? (
          <div className="absolute inset-0 flex items-center justify-center bg-black/10 backdrop-blur-sm z-10">
            <Loader2 className="w-8 h-8 text-blue-500 animate-spin" />
          </div>
        ) : null}

        <div className="flex-1 overflow-y-auto p-4 md:p-8 custom-scrollbar scroll-smooth">
          {pageContent ? (
            <div className="max-w-4xl mx-auto space-y-8 pb-10 mt-10 md:mt-0">
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

              <div className="prose prose-invert prose-lg max-w-none prose-headings:font-bold prose-headings:text-white prose-p:text-gray-300 prose-a:text-blue-400 hover:prose-a:text-blue-300 prose-code:text-blue-200 prose-pre:bg-black/40 prose-pre:border prose-pre:border-white/10 prose-img:rounded-xl">
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  rehypePlugins={[rehypeHighlight]}
                >
                  {pageContent.content.intro}
                </ReactMarkdown>
              </div>

              {pageContent.content.mermaid && (
                <div className="my-8">
                  <div className="text-sm text-muted-foreground mb-2 font-mono uppercase tracking-widest text-xs">Workflow Diagram</div>
                  <Mermaid chart={pageContent.content.mermaid} />
                </div>
              )}

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
    
    {/* RAG 聊天组件 */}
    {repoUrl && (
      <ChatInterface
        repoUrl={repoUrl}
        currentPageContext={currentPageContext}
        currentPageTitle={pageContent?.title}
      />
    )}
    </>
  );
}