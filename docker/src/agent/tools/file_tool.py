"""
文件读取与仓库结构工具

提供以下能力：
- FileReadTool: 精准读取指定文件的特定行范围
- RepoMapTool: 获取仓库的高层结构概览
"""

from __future__ import annotations

import os
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional

from src.agent.state import ContextPiece

logger = logging.getLogger("app.agent.tools.file")


class FileReadTool:
    """
    文件读取工具
    
    支持精准读取指定文件的特定行范围，作为 Agent 获取"铁证"的最终手段。
    """
    
    def __init__(self, repo_root: str):
        """
        初始化文件读取工具
        
        Args:
            repo_root: 仓库根目录路径
        """
        self.repo_root = Path(repo_root)
        
    def execute(
        self,
        file_path: str,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
        max_lines: int = 100
    ) -> ContextPiece:
        """
        读取文件内容
        
        Args:
            file_path: 相对于仓库根目录的文件路径
            start_line: 起始行号（1-indexed），默认从头开始
            end_line: 结束行号（1-indexed），默认读取 max_lines 行
            max_lines: 最大读取行数
            
        Returns:
            ContextPiece: 包含文件内容的上下文片段
        """
        try:
            full_path = self.repo_root / file_path
            
            if not full_path.exists():
                alt_paths = list(self.repo_root.rglob(f"*{file_path}"))
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
            
            return ContextPiece(
                source="file_read",
                content=header + content,
                file_path=file_path,
                line_range=(start_idx + 1, end_idx),
                relevance_score=0.9,
                metadata={
                    "total_lines": total_lines,
                    "lines_read": len(selected_lines),
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
    
    def search_file(self, filename_pattern: str, max_results: int = 10) -> List[str]:
        """
        搜索匹配的文件路径
        
        Args:
            filename_pattern: 文件名模式（支持通配符）
            max_results: 最大返回数量
            
        Returns:
            匹配的文件路径列表
        """
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
    - 关键类和函数签名
    - 文件类型统计
    """
    
    IGNORED_DIRS = {
        '.git', 'node_modules', '__pycache__', '.venv', 'venv',
        'dist', 'build', '.next', '.cache', 'coverage',
        '.idea', '.vscode', 'target', 'out'
    }
    
    IGNORED_EXTENSIONS = {
        '.pyc', '.pyo', '.so', '.o', '.a', '.dylib',
        '.jpg', '.jpeg', '.png', '.gif', '.ico', '.svg',
        '.woff', '.woff2', '.ttf', '.eot',
        '.lock', '.log'
    }
    
    CODE_EXTENSIONS = {
        '.py', '.js', '.ts', '.tsx', '.jsx', '.java', '.go',
        '.rs', '.cpp', '.c', '.h', '.hpp', '.cs', '.rb',
        '.php', '.swift', '.kt', '.scala'
    }
    
    def __init__(self, repo_root: str):
        """
        初始化仓库结构工具
        
        Args:
            repo_root: 仓库根目录路径
        """
        self.repo_root = Path(repo_root)
    
    def execute(
        self,
        include_signatures: bool = True,
        max_depth: int = 3,
        max_files: int = 200
    ) -> ContextPiece:
        """
        生成仓库结构概览
        
        Args:
            include_signatures: 是否包含函数/类签名
            max_depth: 目录最大深度
            max_files: 最大文件数量
            
        Returns:
            ContextPiece: 包含仓库结构的上下文片段
        """
        try:
            tree_lines = ["Repository Structure:", "=" * 40]
            file_stats: Dict[str, int] = {}
            file_count = 0
            
            tree_content = self._build_tree(
                self.repo_root, 
                "", 
                0, 
                max_depth, 
                file_stats,
                max_files
            )
            tree_lines.extend(tree_content)
            
            tree_lines.append("\n" + "=" * 40)
            tree_lines.append("File Statistics:")
            for ext, count in sorted(file_stats.items(), key=lambda x: -x[1])[:15]:
                tree_lines.append(f"  {ext}: {count} files")
            
            if include_signatures:
                signatures = self._extract_key_signatures()
                if signatures:
                    tree_lines.append("\n" + "=" * 40)
                    tree_lines.append("Key Symbols:")
                    tree_lines.extend(signatures)
            
            content = "\n".join(tree_lines)
            
            return ContextPiece(
                source="repo_map",
                content=content,
                relevance_score=0.85,
                metadata={
                    "total_dirs": sum(1 for _ in self.repo_root.rglob("*") if _.is_dir()),
                    "file_stats": file_stats,
                }
            )
            
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
                icon = "📄" if ext in self.CODE_EXTENSIONS else "📋"
                lines.append(f"{prefix}{icon} {f.name}")
                displayed_files += 1
            elif displayed_files == 15:
                remaining = len(files) - 15
                if remaining > 0:
                    lines.append(f"{prefix}    ... and {remaining} more files")
                displayed_files += 1
        
        return lines
    
    def _extract_key_signatures(self, max_symbols: int = 30) -> List[str]:
        """提取关键函数和类签名"""
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
                        signatures.append(f"  [{rel_path}] {class_sig}")
                    elif stripped.startswith('def ') and ':' in stripped:
                        if not stripped.startswith('def _'):
                            func_sig = stripped.split(':')[0]
                            if len(func_sig) < 80:
                                signatures.append(f"  [{rel_path}] {func_sig}")
                    
                    if len(signatures) >= max_symbols:
                        break
                        
            except Exception:
                continue
            
            if len(signatures) >= max_symbols:
                break
        
        return signatures[:max_symbols]
    
    def get_file_list(self, extensions: Optional[List[str]] = None) -> List[str]:
        """
        获取仓库中的文件列表
        
        Args:
            extensions: 要筛选的文件扩展名列表
            
        Returns:
            文件路径列表
        """
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
