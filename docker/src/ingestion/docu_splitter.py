import re
import json
from pathlib import Path
from typing import Optional
from langchain_community.document_loaders import TextLoader
from langchain_core.documents import Document
from src.ingestion.ts_parser import TreeSitterParser


# ---------------------------------------------------------------------------
# 语义感知文档切分器
# ---------------------------------------------------------------------------

class SemanticDocumentSplitter:
    """
    语义感知文档切分器，针对文本/Markdown 文档。

    策略（按优先级）：
    1. Markdown 标题 → 识别层级结构，按章节切块并携带 breadcrumb 元数据
    2. 超长章节 → 按空行（段落边界）递归细切，保留尾部 overlap
    3. 超长段落 → 按句子边界（. ! ? 。等）继续细切
    4. 特殊元素 → 代码块（```...```）和表格整体保留，不跨块拆分

    对于非 Markdown 纯文本，直接走段落 → 句子两级策略。
    """

    MAX_CHUNK_SIZE: int = 2000
    OVERLAP_SIZE: int = 150

    _HEADING_RE = re.compile(r'^(#{1,6})\s+(.+)$', re.MULTILINE)
    _CODE_FENCE_RE = re.compile(r'```[\s\S]*?```')
    _SENTENCE_SPLIT_RE = re.compile(r'(?<=[.!?。！？])\s+')

    # ------------------------------------------------------------------ #

    def split_text(self, text: str, metadata: dict) -> list[Document]:
        ext = Path(metadata.get("source", "")).suffix.lower()
        if ext in (".md", ".mdx", ".markdown", ".rst"):
            return self._split_markdown(text, metadata)
        return self._split_by_paragraph(text, {**metadata, "chunk_type": "plain_text"})

    # ------------------------------------------------------------------ #
    # Markdown 切分
    # ------------------------------------------------------------------ #

    def _split_markdown(self, text: str, base_meta: dict) -> list[Document]:
        # 第一步：提取代码块，用占位符保护，避免内部内容被误判为标题/段落边界
        specials: dict[str, str] = {}
        counter = [0]

        def _extract(m: re.Match) -> str:
            key = f"\x00SPECIAL{counter[0]}\x00"
            specials[key] = m.group(0)
            counter[0] += 1
            return key

        protected = self._CODE_FENCE_RE.sub(_extract, text)

        # 第二步：按标题切分为 sections
        sections = self._parse_heading_sections(protected)

        # 第三步：每个 section 还原占位符，按尺寸决定是否细切
        docs: list[Document] = []
        for sec in sections:
            content = sec["content"]
            for key, val in specials.items():
                content = content.replace(key, val)
            content = content.strip()
            if not content:
                continue

            heading_meta = {
                **base_meta,
                "chunk_type": "markdown_section",
                "section_heading": sec["heading"],
                "heading_level": sec["level"],
                "breadcrumb": " > ".join(sec["breadcrumb"]),
            }

            if len(content) <= self.MAX_CHUNK_SIZE:
                docs.append(Document(page_content=content, metadata=heading_meta))
            else:
                docs.extend(self._split_by_paragraph(content, heading_meta))

        return docs

    def _parse_heading_sections(self, text: str) -> list[dict]:
        """将文本按 Markdown 标题分割，返回带层级和 breadcrumb 的 section 列表。"""
        lines = text.splitlines(keepends=True)
        sections: list[dict] = []
        heading_stack: list[tuple[int, str]] = []   # (level, heading_text)

        current_heading = ""
        current_level = 0
        current_lines: list[str] = []

        def flush():
            content_raw = "".join(current_lines)
            breadcrumb = [h for _, h in heading_stack]
            heading_prefix = f"{'#' * current_level} {current_heading}\n\n" if current_heading else ""
            sections.append({
                "heading": current_heading,
                "level": current_level,
                "content": heading_prefix + content_raw,
                "breadcrumb": breadcrumb,
            })

        for line in lines:
            m = re.match(r'^(#{1,6})\s+(.+)$', line.rstrip())
            if m:
                flush()
                level = len(m.group(1))
                heading = m.group(2).strip()
                # 维护祖先栈
                while heading_stack and heading_stack[-1][0] >= level:
                    heading_stack.pop()
                heading_stack.append((level, heading))
                current_heading = heading
                current_level = level
                current_lines = []
            else:
                current_lines.append(line)

        flush()
        return sections

    # ------------------------------------------------------------------ #
    # 段落级切分
    # ------------------------------------------------------------------ #

    def _split_by_paragraph(self, text: str, base_meta: dict) -> list[Document]:
        """按空行段落边界切分，超长段落进一步按句子切。"""
        paragraphs = re.split(r'\n\s*\n', text)
        docs: list[Document] = []
        current_chunk = ""
        chunk_idx = 0

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            # 单个段落本身超长且不是代码块 → 按句子细切
            if len(para) > self.MAX_CHUNK_SIZE and not para.startswith("```"):
                if current_chunk:
                    docs.append(Document(
                        page_content=current_chunk.strip(),
                        metadata={**base_meta, "chunk_index": chunk_idx},
                    ))
                    chunk_idx += 1
                    current_chunk = ""
                sent_docs = self._split_by_sentence(para, base_meta, chunk_idx)
                docs.extend(sent_docs)
                chunk_idx += len(sent_docs)
                continue

            if len(current_chunk) + len(para) + 2 > self.MAX_CHUNK_SIZE:
                if current_chunk:
                    docs.append(Document(
                        page_content=current_chunk.strip(),
                        metadata={**base_meta, "chunk_index": chunk_idx},
                    ))
                    chunk_idx += 1
                    # 尾部 overlap：保留上一块末尾内容，帮助上下文连续
                    overlap = current_chunk[-self.OVERLAP_SIZE:] if len(current_chunk) > self.OVERLAP_SIZE else current_chunk
                    current_chunk = overlap + "\n\n" + para
                else:
                    current_chunk = para
            else:
                current_chunk = (current_chunk + "\n\n" + para).strip() if current_chunk else para

        if current_chunk.strip():
            docs.append(Document(
                page_content=current_chunk.strip(),
                metadata={**base_meta, "chunk_index": chunk_idx},
            ))

        return docs

    # ------------------------------------------------------------------ #
    # 句子级切分（最后手段）
    # ------------------------------------------------------------------ #

    def _split_by_sentence(self, text: str, base_meta: dict, start_idx: int) -> list[Document]:
        sentences = self._SENTENCE_SPLIT_RE.split(text)
        docs: list[Document] = []
        current = ""
        idx = start_idx

        for sent in sentences:
            sent = sent.strip()
            if not sent:
                continue
            if len(current) + len(sent) + 1 > self.MAX_CHUNK_SIZE:
                if current:
                    docs.append(Document(
                        page_content=current.strip(),
                        metadata={**base_meta, "chunk_index": idx, "chunk_type": "sentence_fallback"},
                    ))
                    idx += 1
                current = sent
            else:
                current = (current + " " + sent).strip() if current else sent

        if current.strip():
            docs.append(Document(
                page_content=current.strip(),
                metadata={**base_meta, "chunk_index": idx, "chunk_type": "sentence_fallback"},
            ))

        return docs


# ---------------------------------------------------------------------------
# 调试输出工具
# ---------------------------------------------------------------------------

def save_chunks_debug(docs: list[Document], output_path: str) -> None:
    """
    将 chunk 结果保存为 JSONL 文件，每行一个 JSON 对象，便于人工检查。
    同时生成一份简明摘要 *_summary.txt。
    """
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    with open(out, "w", encoding="utf-8") as f:
        for i, doc in enumerate(docs):
            record = {
                "chunk_id": i,
                "char_count": len(doc.page_content),
                "metadata": doc.metadata,
                "content": doc.page_content,
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    # 人类可读摘要
    summary_path = out.with_suffix(".summary.txt")
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(f"Total chunks: {len(docs)}\n")
        f.write("=" * 60 + "\n\n")
        for i, doc in enumerate(docs):
            source = doc.metadata.get("source", "unknown")
            heading = doc.metadata.get("section_heading", "")
            bc = doc.metadata.get("breadcrumb", "")
            chunk_type = doc.metadata.get("chunk_type", "")
            f.write(f"[{i:04d}] {Path(source).name}")
            if heading:
                f.write(f" | {bc} > {heading}" if bc else f" | {heading}")
            if chunk_type:
                f.write(f" ({chunk_type})")
            f.write(f"  [{len(doc.page_content)} chars]\n")
            f.write(doc.page_content[:200].replace("\n", " "))
            f.write("...\n\n" if len(doc.page_content) > 200 else "\n\n")

    print(f"[debug] Chunk 调试文件已保存至: {out}")
    print(f"[debug] 摘要文件: {summary_path}")


# ---------------------------------------------------------------------------
# Code chunk 后处理（合并过短块 / 分割过长块）
# ---------------------------------------------------------------------------

MIN_CODE_CHUNK: int = 150   # 低于此字符数的 chunk 会与同文件相邻块合并
MAX_CODE_CHUNK: int = 2000  # 超过此字符数的 chunk 会按行分割


def _split_code_by_lines(content: str, metadata: dict, max_size: int) -> list[Document]:
    """将过长的代码块按行分割，尽量保持函数/语句完整性。"""
    lines = content.splitlines(keepends=True)
    docs: list[Document] = []
    current_lines: list[str] = []
    current_size = 0
    part = 0

    for line in lines:
        if current_size + len(line) > max_size and current_lines:
            docs.append(Document(
                page_content="".join(current_lines).strip(),
                metadata={**metadata, "chunk_part": part},
            ))
            part += 1
            current_lines = [line]
            current_size = len(line)
        else:
            current_lines.append(line)
            current_size += len(line)

    if current_lines:
        docs.append(Document(
            page_content="".join(current_lines).strip(),
            metadata={**metadata, "chunk_part": part},
        ))
    return docs


def _normalize_code_chunks(raw_docs: list[Document]) -> list[Document]:
    """
    对同一文件内的 AST code chunks 做后处理：
    1. 过短（< MIN_CODE_CHUNK）的相邻块合并到下一块，避免产生碎片化的单行 chunk。
    2. 过长（> MAX_CODE_CHUNK）的块按行分割，确保 embedding 有效性。
    """
    result: list[Document] = []
    pending_content = ""
    pending_meta: dict | None = None

    def flush_pending():
        nonlocal pending_content, pending_meta
        if pending_content and pending_meta is not None:
            result.append(Document(page_content=pending_content.strip(), metadata=pending_meta))
        pending_content = ""
        pending_meta = None

    for doc in raw_docs:
        content = doc.page_content.strip()
        meta = doc.metadata
        source = meta.get("source", "")

        # 过长 → 先 flush pending，再按行分割
        if len(content) > MAX_CODE_CHUNK:
            flush_pending()
            result.extend(_split_code_by_lines(content, meta, MAX_CODE_CHUNK))
            continue

        # 过短 → 尝试与 pending 合并（仅限同一文件）
        if len(content) < MIN_CODE_CHUNK:
            if pending_meta is None:
                pending_content = content
                pending_meta = dict(meta)
            elif pending_meta.get("source") == source:
                combined = pending_content + "\n\n" + content
                if len(combined) <= MAX_CODE_CHUNK:
                    pending_content = combined
                    # 更新行范围 end_line 到最新
                    if "end_line" in meta and "end_line" in pending_meta:
                        pending_meta["end_line"] = meta["end_line"]
                else:
                    # 合并后超限，先 flush 再重新开始
                    flush_pending()
                    pending_content = content
                    pending_meta = dict(meta)
            else:
                # 不同文件，flush 后重新开始
                flush_pending()
                pending_content = content
                pending_meta = dict(meta)
            continue

        # 正常大小 → flush pending 后直接加入
        if pending_meta is not None:
            if pending_meta.get("source") == source:
                # 将 pending 追加到当前正常块前面（pending 太短，合并进来）
                combined = pending_content + "\n\n" + content
                if len(combined) <= MAX_CODE_CHUNK:
                    pending_content = ""
                    pending_meta = None
                    result.append(Document(page_content=combined.strip(), metadata=meta))
                    continue
            flush_pending()

        result.append(Document(page_content=content, metadata=meta))

    flush_pending()
    return result


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def load_and_split_docs(
    file_paths: list[str],
    debug_output_path: Optional[str] = None,
) -> list[Document]:
    """
    加载文件内容并切分为文本块。

    - 代码文件：Tree-sitter AST 感知切片
    - 文本/Markdown 文件：SemanticDocumentSplitter 语义感知切片

    Args:
        file_paths: 要处理的文件路径列表。
        debug_output_path: 若提供，将所有 chunk 保存到该 JSONL 文件（含摘要），
                           便于人工检查 chunk 内容和效果。
                           示例: "chunk_debug/chunks.jsonl"
    """
    docs: list[Document] = []
    ts_parser = TreeSitterParser()
    semantic_splitter = SemanticDocumentSplitter()

    for file_path in file_paths:
        try:
            path_obj = Path(file_path)
            extension = path_obj.suffix.lower()

            if extension in TreeSitterParser.EXTENSION_TO_LANGUAGE:
                # 代码文件：AST 感知切片
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()

                chunks = ts_parser.parse_code(content, extension)
                raw_code_docs = [
                    Document(
                        page_content=chunk.content,
                        metadata={
                            "source": file_path,
                            "chunk_type": "code_ast",
                            "start_line": chunk.start_line,
                            "end_line": chunk.end_line,
                            "node_type": chunk.node_type,
                            "name": chunk.name,
                        },
                    )
                    for chunk in chunks
                ]
                docs.extend(_normalize_code_chunks(raw_code_docs))
            else:
                # 文本文件：语义感知切片
                loader = TextLoader(file_path, encoding="utf-8")
                raw_docs = loader.load()   # 不切分，拿原始内容
                for raw_doc in raw_docs:
                    split = semantic_splitter.split_text(
                        raw_doc.page_content,
                        {**raw_doc.metadata, "source": file_path},
                    )
                    docs.extend(split)

        except Exception as e:
            import traceback
            error_details = traceback.format_exc().splitlines()[-1]
            print(f"Skipping file {file_path} due to error: {e} ({error_details})")
            continue

    print(f"Split content into {len(docs)} document chunks.")

    if debug_output_path:
        save_chunks_debug(docs, debug_output_path)

    return docs
