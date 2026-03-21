"""
文件读取与仓库结构工具

提供以下能力：
- FileReadTool: 精准读取指定文件的特定行范围
- RepoMapTool: 获取仓库的高层结构概览（支持 Python/TS/TSX/JS）
"""

from __future__ import annotations

import os
import re
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

from src.agent.state import ContextPiece

logger = logging.getLogger("app.agent.tools.file")


class FileReadTool:
    """
    文件读取工具
    
    支持精准读取指定文件的特定行范围，作为 Agent 获取"铁证"的最终手段。
    """
    
    def __init__(self, repo_root: str):
        self.repo_root = Path(repo_root)
        
    def _resolve_safe_path(self, file_path: str) -> Tuple[Optional[Path], Optional[str]]:
        """将相对路径解析到 repo_root 内，禁止绝对路径与路径穿越。"""
        if not file_path or not str(file_path).strip():
            return None, "empty_path"
        root = self.repo_root.resolve()
        raw = Path(file_path)
        if raw.is_absolute():
            return None, "absolute_path_not_allowed"
        candidate = (root / raw).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return None, "path_escapes_repo_root"
        return candidate, None

    def execute(
        self,
        file_path: str,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
        max_lines: int = 100
    ) -> ContextPiece:
        """
        读取文件内容
        
        返回的 ContextPiece 包含精确的行范围，可直接用于证据卡片。
        """
        try:
            full_path, path_err = self._resolve_safe_path(file_path)
            if path_err:
                return ContextPiece(
                    source="file_read",
                    content=f"Invalid file path ({path_err}): {file_path}",
                    file_path=file_path,
                    relevance_score=0.0,
                    metadata={"error": path_err},
                )

            if not full_path.exists():
                basename = Path(file_path).name
                alt_paths = list(self.repo_root.rglob(f"*{basename}")) if basename else []
                if alt_paths:
                    full_path = alt_paths[0]
                    file_path = str(full_path.relative_to(self.repo_root))
                else:
                    return ContextPiece(
                        source="file_read",
                        content=f"File not found: {file_path}",
                        file_path=file_path,
                        relevance_score=0.0,
                        metadata={"error": "file_not_found"}
                    )
            
            if not full_path.is_file():
                return ContextPiece(
                    source="file_read",
                    content=f"Not a file: {file_path}",
                    file_path=file_path,
                    relevance_score=0.0,
                    metadata={"error": "not_a_file"}
                )
            
            with open(full_path, 'r', encoding='utf-8', errors='replace') as f:
                lines = f.readlines()
            
            total_lines = len(lines)
            
            if start_line is None:
                start_line = 1
            if end_line is None:
                end_line = min(start_line + max_lines - 1, total_lines)
            
            start_idx = max(0, start_line - 1)
            end_idx = min(total_lines, end_line)
            
            selected_lines = lines[start_idx:end_idx]
            
            content_lines = []
            for i, line in enumerate(selected_lines, start=start_idx + 1):
                content_lines.append(f"{i:4d} | {line.rstrip()}")
            
            content = "\n".join(content_lines)
            
            header = f"File: {file_path} (lines {start_idx + 1}-{end_idx} of {total_lines})\n"
            header += "=" * 60 + "\n"
            
            # 提取文件中的符号（用于证据卡片）
            symbols = self._extract_symbols_from_lines(selected_lines, full_path.suffix)
            
            return ContextPiece(
                source="file_read",
                content=header + content,
                file_path=file_path,
                line_range=(start_idx + 1, end_idx),
                relevance_score=0.9,
                metadata={
                    "total_lines": total_lines,
                    "lines_read": len(selected_lines),
                    "symbols_found": symbols,
                }
            )
            
        except Exception as e:
            logger.error(f"Failed to read file {file_path}: {e}")
            return ContextPiece(
                source="file_read",
                content=f"Error reading file: {str(e)}",
                file_path=file_path,
                relevance_score=0.0,
                metadata={"error": str(e)}
            )
    
    def _extract_symbols_from_lines(self, lines: List[str], extension: str) -> List[str]:
        """从代码行中提取符号"""
        symbols = []
        for line in lines:
            stripped = line.strip()
            
            if extension == '.py':
                if stripped.startswith('def ') and '(' in stripped:
                    match = re.match(r'def\s+(\w+)', stripped)
                    if match:
                        symbols.append(f"function:{match.group(1)}")
                elif stripped.startswith('class ') and ':' in stripped:
                    match = re.match(r'class\s+(\w+)', stripped)
                    if match:
                        symbols.append(f"class:{match.group(1)}")
            
            elif extension in ['.ts', '.tsx', '.js', '.jsx']:
                if 'function ' in stripped or stripped.startswith('const ') or stripped.startswith('export '):
                    match = re.match(r'(?:export\s+)?(?:const|function|let|var)\s+(\w+)', stripped)
                    if match:
                        symbols.append(f"function:{match.group(1)}")
                elif 'class ' in stripped:
                    match = re.match(r'(?:export\s+)?class\s+(\w+)', stripped)
                    if match:
                        symbols.append(f"class:{match.group(1)}")
                elif 'interface ' in stripped or 'type ' in stripped:
                    match = re.match(r'(?:export\s+)?(?:interface|type)\s+(\w+)', stripped)
                    if match:
                        symbols.append(f"type:{match.group(1)}")
        
        return symbols[:10]
    
    def search_file(self, filename_pattern: str, max_results: int = 10) -> List[str]:
        """搜索匹配的文件路径"""
        matches = []
        for path in self.repo_root.rglob(filename_pattern):
            if path.is_file():
                rel_path = str(path.relative_to(self.repo_root))
                matches.append(rel_path)
                if len(matches) >= max_results:
                    break
        return matches


class RepoMapTool:
    """
    仓库结构概览工具
    
    生成仓库的高层结构视图，包括：
    - 目录树
    - 关键类和函数签名（支持 Python, TypeScript, JavaScript, TSX, JSX）
    - 文件类型统计
    - 入口点检测
    """
    
    IGNORED_DIRS = {
        '.git', 'node_modules', '__pycache__', '.venv', 'venv',
        'dist', 'build', '.next', '.cache', 'coverage',
        '.idea', '.vscode', 'target', 'out', '.turbo',
        '.nuxt', '.output', 'vendor', 'packages/*/node_modules'
    }
    
    IGNORED_EXTENSIONS = {
        '.pyc', '.pyo', '.so', '.o', '.a', '.dylib',
        '.jpg', '.jpeg', '.png', '.gif', '.ico', '.svg',
        '.woff', '.woff2', '.ttf', '.eot',
        '.lock', '.log', '.map', '.min.js', '.min.css'
    }
    
    CODE_EXTENSIONS = {
        '.py', '.js', '.ts', '.tsx', '.jsx', '.java', '.go',
        '.rs', '.cpp', '.c', '.h', '.hpp', '.cs', '.rb',
        '.php', '.swift', '.kt', '.scala', '.vue', '.svelte'
    }
    
    # 入口点文件模式
    ENTRYPOINT_PATTERNS = [
        'main.py', 'app.py', 'index.py', '__main__.py', 'manage.py', 'wsgi.py', 'asgi.py',
        'index.ts', 'index.tsx', 'index.js', 'index.jsx', 'main.ts', 'main.tsx',
        'app.ts', 'app.tsx', 'server.ts', 'server.js',
        'page.tsx', 'layout.tsx', 'route.ts', 'route.tsx',
        'api.py', 'routes.py', 'urls.py', 'views.py',
    ]
    
    # 配置文件模式
    CONFIG_PATTERNS = [
        'package.json', 'tsconfig.json', 'next.config.js', 'next.config.ts',
        'vite.config.ts', 'vite.config.js', 'webpack.config.js',
        'pyproject.toml', 'setup.py', 'requirements.txt',
        '.env.example', 'docker-compose.yml', 'Dockerfile',
    ]
    
    def __init__(self, repo_root: str):
        self.repo_root = Path(repo_root)
        self._cache: Dict[str, ContextPiece] = {}  # key: "sig={sig}&depth={depth}"
    
    def execute(
        self,
        include_signatures: bool = True,
        max_depth: int = 3,
        max_files: int = 200
    ) -> ContextPiece:
        """
        生成仓库结构概览（带 LRU 缓存：相同参数复用结果）。
        """
        cache_key = f"sig={include_signatures}&depth={max_depth}&files={max_files}"
        if cache_key in self._cache:
            logger.info("[RepoMap] Returning cached result")
            return self._cache[cache_key]
        try:
            tree_lines = ["Repository Structure:", "=" * 50]
            file_stats: Dict[str, int] = {}
            
            tree_content = self._build_tree(
                self.repo_root, 
                "", 
                0, 
                max_depth, 
                file_stats,
                max_files
            )
            tree_lines.extend(tree_content)
            
            # 文件统计
            tree_lines.append("\n" + "=" * 50)
            tree_lines.append("File Statistics:")
            for ext, count in sorted(file_stats.items(), key=lambda x: -x[1])[:15]:
                tree_lines.append(f"  {ext}: {count} files")
            
            # 入口点检测
            entrypoints = self._detect_entrypoints()
            if entrypoints:
                tree_lines.append("\n" + "=" * 50)
                tree_lines.append("Detected Entrypoints:")
                for ep in entrypoints[:10]:
                    tree_lines.append(f"  📍 {ep}")
            
            # 配置文件
            configs = self._detect_configs()
            if configs:
                tree_lines.append("\n" + "=" * 50)
                tree_lines.append("Configuration Files:")
                for cfg in configs[:10]:
                    tree_lines.append(f"  ⚙️  {cfg}")
            
            # 关键符号签名（多语言支持）
            if include_signatures:
                signatures = self._extract_key_signatures_multilang()
                if signatures:
                    tree_lines.append("\n" + "=" * 50)
                    tree_lines.append("Key Symbols:")
                    tree_lines.extend(signatures)
            
            content = "\n".join(tree_lines)
            
            result = ContextPiece(
                source="repo_map",
                content=content,
                relevance_score=0.85,
                metadata={
                    "file_stats": file_stats,
                    "entrypoints": entrypoints[:5],
                    "configs": configs[:5],
                    "tech_stack": self._detect_tech_stack(file_stats, configs),
                }
            )
            self._cache[cache_key] = result
            return result
            
        except Exception as e:
            logger.error(f"Failed to generate repo map: {e}")
            return ContextPiece(
                source="repo_map",
                content=f"Error generating repo map: {str(e)}",
                relevance_score=0.0,
                metadata={"error": str(e)}
            )
    
    def _build_tree(
        self, 
        path: Path, 
        prefix: str, 
        depth: int, 
        max_depth: int,
        file_stats: Dict[str, int],
        max_files: int,
        current_count: int = 0
    ) -> List[str]:
        """递归构建目录树"""
        lines = []
        
        if depth > max_depth:
            return ["    " * depth + "..."]
        
        try:
            entries = sorted(path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        except PermissionError:
            return []
        
        dirs = []
        files = []
        
        for entry in entries:
            if entry.name.startswith('.') and entry.name not in {'.env.example'}:
                continue
            if entry.is_dir() and entry.name in self.IGNORED_DIRS:
                continue
            if entry.is_file() and entry.suffix.lower() in self.IGNORED_EXTENSIONS:
                continue
            
            if entry.is_dir():
                dirs.append(entry)
            else:
                files.append(entry)
        
        for d in dirs:
            lines.append(f"{prefix}📁 {d.name}/")
            sub_lines = self._build_tree(
                d, 
                prefix + "    ", 
                depth + 1, 
                max_depth,
                file_stats,
                max_files,
                current_count
            )
            lines.extend(sub_lines)
        
        displayed_files = 0
        for f in files:
            ext = f.suffix.lower() or '(no ext)'
            file_stats[ext] = file_stats.get(ext, 0) + 1
            
            if displayed_files < 15:
                # 标注入口点和配置文件
                icon = "📄"
                if f.name in self.ENTRYPOINT_PATTERNS:
                    icon = "📍"
                elif f.name in self.CONFIG_PATTERNS:
                    icon = "⚙️"
                elif ext in self.CODE_EXTENSIONS:
                    icon = "📄"
                else:
                    icon = "📋"
                    
                lines.append(f"{prefix}{icon} {f.name}")
                displayed_files += 1
            elif displayed_files == 15:
                remaining = len(files) - 15
                if remaining > 0:
                    lines.append(f"{prefix}    ... and {remaining} more files")
                displayed_files += 1
        
        return lines
    
    def _detect_entrypoints(self) -> List[str]:
        """检测入口点文件"""
        entrypoints = []
        for pattern in self.ENTRYPOINT_PATTERNS:
            for path in self.repo_root.rglob(pattern):
                if any(ignored in str(path) for ignored in self.IGNORED_DIRS):
                    continue
                rel_path = str(path.relative_to(self.repo_root))
                entrypoints.append(rel_path)
        return sorted(set(entrypoints))
    
    def _detect_configs(self) -> List[str]:
        """检测配置文件"""
        configs = []
        for pattern in self.CONFIG_PATTERNS:
            for path in self.repo_root.rglob(pattern):
                if any(ignored in str(path) for ignored in self.IGNORED_DIRS):
                    continue
                rel_path = str(path.relative_to(self.repo_root))
                configs.append(rel_path)
        return sorted(set(configs))
    
    def _detect_tech_stack(self, file_stats: Dict[str, int], configs: List[str]) -> Dict[str, str]:
        """检测技术栈"""
        stack = {}
        
        # 基于文件扩展名
        if file_stats.get('.py', 0) > 0:
            stack['backend'] = 'Python'
        if file_stats.get('.ts', 0) > 0 or file_stats.get('.tsx', 0) > 0:
            stack['frontend'] = 'TypeScript'
        elif file_stats.get('.js', 0) > 0 or file_stats.get('.jsx', 0) > 0:
            stack['frontend'] = 'JavaScript'
        
        # 基于配置文件
        config_names = [c.split('/')[-1] for c in configs]
        if 'next.config.js' in config_names or 'next.config.ts' in config_names:
            stack['framework'] = 'Next.js'
        elif 'vite.config.ts' in config_names or 'vite.config.js' in config_names:
            stack['framework'] = 'Vite'
        if 'pyproject.toml' in config_names:
            stack['build'] = 'Poetry/PEP517'
        if 'docker-compose.yml' in config_names:
            stack['deployment'] = 'Docker'
        
        return stack
    
    def _extract_key_signatures_multilang(self, max_symbols: int = 40) -> List[str]:
        """提取关键函数和类签名（多语言支持）"""
        signatures = []
        
        # Python 文件
        signatures.extend(self._extract_python_signatures(max_symbols // 2))
        
        # TypeScript/JavaScript 文件
        signatures.extend(self._extract_ts_signatures(max_symbols // 2))
        
        return signatures[:max_symbols]
    
    def _extract_python_signatures(self, max_symbols: int) -> List[str]:
        """提取 Python 签名"""
        signatures = []
        
        for py_file in list(self.repo_root.rglob("*.py"))[:50]:
            if any(ignored in str(py_file) for ignored in self.IGNORED_DIRS):
                continue
            
            try:
                rel_path = py_file.relative_to(self.repo_root)
                content = py_file.read_text(encoding='utf-8', errors='replace')
                
                for line in content.split('\n'):
                    stripped = line.strip()
                    if stripped.startswith('class ') and ':' in stripped:
                        class_sig = stripped.split(':')[0]
                        signatures.append(f"  [py] [{rel_path}] {class_sig}")
                    elif stripped.startswith('def ') and ':' in stripped:
                        if not stripped.startswith('def _'):
                            func_sig = stripped.split(':')[0]
                            if len(func_sig) < 80:
                                signatures.append(f"  [py] [{rel_path}] {func_sig}")
                    
                    if len(signatures) >= max_symbols:
                        break
                        
            except Exception:
                continue
            
            if len(signatures) >= max_symbols:
                break
        
        return signatures
    
    def _extract_ts_signatures(self, max_symbols: int) -> List[str]:
        """提取 TypeScript/JavaScript/TSX/JSX 签名"""
        signatures = []
        
        ts_patterns = ['*.ts', '*.tsx', '*.js', '*.jsx']
        ts_files = []
        for pattern in ts_patterns:
            ts_files.extend(list(self.repo_root.rglob(pattern))[:30])
        
        for ts_file in ts_files:
            if any(ignored in str(ts_file) for ignored in self.IGNORED_DIRS):
                continue
            if '.min.' in ts_file.name or '.d.ts' in ts_file.name:
                continue
            
            try:
                rel_path = ts_file.relative_to(self.repo_root)
                content = ts_file.read_text(encoding='utf-8', errors='replace')
                ext = ts_file.suffix.lower()
                lang_tag = "ts" if ext in ['.ts', '.tsx'] else "js"
                
                for line in content.split('\n'):
                    stripped = line.strip()
                    
                    # 导出的函数
                    if stripped.startswith('export function '):
                        match = re.match(r'export function (\w+)\s*\([^)]*\)', stripped)
                        if match:
                            signatures.append(f"  [{lang_tag}] [{rel_path}] export function {match.group(1)}()")
                    
                    # 导出的 const 函数
                    elif stripped.startswith('export const ') and '=>' in stripped:
                        match = re.match(r'export const (\w+)\s*=', stripped)
                        if match:
                            signatures.append(f"  [{lang_tag}] [{rel_path}] export const {match.group(1)} = () =>")
                    
                    # 导出的类
                    elif stripped.startswith('export class '):
                        match = re.match(r'export class (\w+)', stripped)
                        if match:
                            signatures.append(f"  [{lang_tag}] [{rel_path}] export class {match.group(1)}")
                    
                    # 导出的接口/类型
                    elif stripped.startswith('export interface '):
                        match = re.match(r'export interface (\w+)', stripped)
                        if match:
                            signatures.append(f"  [{lang_tag}] [{rel_path}] export interface {match.group(1)}")
                    
                    elif stripped.startswith('export type '):
                        match = re.match(r'export type (\w+)', stripped)
                        if match:
                            signatures.append(f"  [{lang_tag}] [{rel_path}] export type {match.group(1)}")
                    
                    # React 组件（const X = () => 或 function X()）
                    elif ext in ['.tsx', '.jsx']:
                        if stripped.startswith('const ') and ('= (' in stripped or '= ()' in stripped):
                            match = re.match(r'const (\w+)\s*[:=]', stripped)
                            if match and match.group(1)[0].isupper():
                                signatures.append(f"  [{lang_tag}] [{rel_path}] component {match.group(1)}")
                    
                    if len(signatures) >= max_symbols:
                        break
                        
            except Exception:
                continue
            
            if len(signatures) >= max_symbols:
                break
        
        return signatures
    
    def get_file_list(self, extensions: Optional[List[str]] = None) -> List[str]:
        """获取仓库中的文件列表"""
        files = []
        for path in self.repo_root.rglob("*"):
            if not path.is_file():
                continue
            if any(ignored in str(path) for ignored in self.IGNORED_DIRS):
                continue
            if extensions and path.suffix.lower() not in extensions:
                continue
            files.append(str(path.relative_to(self.repo_root)))
        return sorted(files)
