from __future__ import annotations

import hashlib
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple, Any

from langchain_core.documents import Document

Tokenizer = Callable[[str], List[str]]

_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_\u4e00-\u9fff]+", re.UNICODE)


def default_tokenizer(text: str) -> List[str]:
    """
    轻量分词实现，提取匹配的（中文，英文，数字，下划线）
    """
    if not text:
        return []
    return _TOKEN_PATTERN.findall(text.lower())


def _clone_document(doc: Document) -> Document:
    return Document(page_content=doc.page_content, metadata=dict(doc.metadata))


def compute_doc_key(doc: Document) -> str:
    """
    通过来源+内容哈希为每个文档生成稳定的唯一key，方便跨检索结果融合与去重。
    """
    meta = doc.metadata or {}
    source = meta.get("source") or meta.get("file_path") or "unknown"
    anchor = meta.get("chunk_id") or meta.get("line_start") or meta.get("page") or ""
    digest = hashlib.md5(doc.page_content.encode("utf-8")).hexdigest()[:12]
    return f"{source}|{anchor}|{digest}"


def normalize_scores(values: Sequence[float]) -> List[float]:
    """
    归一化分数，将分数范围缩放到0-1之间。
    """
    if not values:
        return []
    max_v = max(values)
    min_v = min(values)
    if math.isclose(max_v, min_v):
        return [1.0 for _ in values]
    span = max_v - min_v
    return [(val - min_v) / span for val in values]


@dataclass
class RankedCandidate:
    key: str
    category: str
    doc: Document
    dense_score: float = 0.0
    sparse_score: float = 0.0
    final_score: float = 0.0


class SparseBM25Index:
    """
    只依赖轻量分词的 BM25 实现，避免额外依赖和构建流程。
    """

    def __init__(
        self,
        documents: List[Document],
        tokenized_docs: List[List[str]],
        term_freqs: List[Counter[str]],
        idf: Dict[str, float],
        avg_doc_len: float,
        tokenizer: Tokenizer,
    ) -> None:
        self._documents = documents
        self._tokenized_docs = tokenized_docs
        self._term_freqs = term_freqs
        self._idf = idf
        self._avg_doc_len = avg_doc_len
        self._tokenizer = tokenizer
        self._k1 = 1.5
        self._b = 0.75

    @classmethod
    def build(
        cls, documents: Iterable[Document], tokenizer: Tokenizer | None = None
    ) -> "SparseBM25Index":
        tokenizer = tokenizer or default_tokenizer
        docs: List[Document] = []
        tokenized_docs: List[List[str]] = []
        term_freqs: List[Counter[str]] = []
        doc_freqs: Dict[str, int] = defaultdict(int)

        for doc in documents:
            cloned = _clone_document(doc)
            docs.append(cloned)
            tokens = tokenizer(cloned.page_content)
            tokenized_docs.append(tokens)
            freq = Counter(tokens)
            term_freqs.append(freq)
            for term in freq:
                doc_freqs[term] += 1

        if not docs:
            return cls([], [], [], {}, 0.0, tokenizer)

        total_len = sum(len(tokens) for tokens in tokenized_docs)
        avg_len = total_len / len(tokenized_docs) if tokenized_docs else 0.0
        doc_count = len(docs)
        idf: Dict[str, float] = {}
        for term, df in doc_freqs.items():
            idf[term] = math.log(1 + (doc_count - df + 0.5) / (df + 0.5))

        return cls(docs, tokenized_docs, term_freqs, idf, avg_len, tokenizer)

    def search(self, query: str, top_k: int = 20) -> List[Tuple[Document, float]]:
        if not self._documents or top_k <= 0:
            return []
        query_tokens = self._tokenizer(query)
        if not query_tokens:
            return []

        scores: List[Tuple[int, float]] = []
        for idx, freq in enumerate(self._term_freqs):
            if not freq:
                continue
            doc_len = len(self._tokenized_docs[idx]) or 1
            score = 0.0
            for term in query_tokens:
                if term not in freq:
                    continue
                idf = self._idf.get(term)
                if idf is None:
                    continue
                tf = freq[term]
                denom = tf + self._k1 * (1 - self._b + self._b * doc_len / (self._avg_doc_len or 1))
                score += idf * (tf * (self._k1 + 1) / denom)
            if score > 0:
                scores.append((idx, score))

        if not scores:
            return []

        scores.sort(key=lambda item: item[1], reverse=True)
        limited = scores[:top_k]
        return [(self._documents[idx], score) for idx, score in limited]


def _cosine(counter_a: Counter[str], counter_b: Counter[str]) -> float:
    if not counter_a or not counter_b:
        return 0.0
    shared = set(counter_a) & set(counter_b)
    if not shared:
        return 0.0
    numerator = sum(counter_a[token] * counter_b[token] for token in shared)
    norm_a = math.sqrt(sum(value * value for value in counter_a.values()))
    norm_b = math.sqrt(sum(value * value for value in counter_b.values()))
    if math.isclose(norm_a, 0.0) or math.isclose(norm_b, 0.0):
        return 0.0
    return numerator / (norm_a * norm_b)


def mmr_select(
    candidates: Sequence[RankedCandidate],
    query: str,
    *,
    top_n: int,
    lambda_mult: float = 0.5,
    tokenizer: Tokenizer | None = None,
) -> List[RankedCandidate]:
    """
    经典 MMR：兼顾单点相关性与候选间的互斥性。
    """
    if not candidates or top_n <= 0:
        return []

    tokenizer = tokenizer or default_tokenizer
    query_counter = Counter(tokenizer(query))
    doc_counters = {cand.key: Counter(tokenizer(cand.doc.page_content)) for cand in candidates}

    selected: List[RankedCandidate] = []
    remaining = list(candidates)

    while remaining and len(selected) < top_n:
        best_idx = 0
        best_score = float("-inf")

        for idx, cand in enumerate(remaining):
            relevance = cand.final_score
            if not query_counter:
                sim_to_query = relevance
            else:
                sim_to_query = _cosine(doc_counters[cand.key], query_counter)
                # 若语义分未能区分，则用最终得分兜底
                if math.isclose(sim_to_query, 0.0):
                    sim_to_query = relevance

            if not selected:
                penalty = 0.0
            else:
                penalty = max(
                    _cosine(doc_counters[cand.key], doc_counters[sel.key])
                    for sel in selected
                )

            mmr_score = lambda_mult * sim_to_query - (1 - lambda_mult) * penalty
            if mmr_score > best_score:
                best_score = mmr_score
                best_idx = idx

        selected.append(remaining.pop(best_idx))

    return selected


# ====================== 社区优先两阶段检索 ======================

@dataclass
class CommunityInfo:
    """社区信息数据类"""
    community_id: int
    summary: str
    node_ids: List[str] = field(default_factory=list)
    relevance_score: float = 0.0


class CommunityFirstRetriever:
    """
    社区优先的两阶段检索器。
    
    第一阶段：通过查询语义匹配定位相关业务社区
    第二阶段：在匹配的社区内部检索具体代码片段
    """

    def __init__(
        self,
        communities: Dict[int, List[str]],
        community_summaries: Dict[int, str],
        documents: List[Document],
        tokenizer: Tokenizer | None = None,
    ):
        """
        初始化社区优先检索器。

        Args:
            communities: 社区ID到节点ID列表的映射 {community_id: [node_id1, node_id2, ...]}
            community_summaries: 社区ID到摘要的映射 {community_id: summary_text}
            documents: 所有文档列表，每个文档的 metadata 应包含 source 字段
            tokenizer: 可选的分词器
        """
        self._communities = communities
        self._summaries = community_summaries
        self._documents = documents
        self._tokenizer = tokenizer or default_tokenizer

        # 为每个文档建立社区映射
        self._doc_to_community: Dict[str, int] = {}
        self._community_docs: Dict[int, List[Document]] = defaultdict(list)
        self._build_doc_community_mapping()

        # 为社区摘要构建 BM25 索引
        self._community_bm25: Optional[SparseBM25Index] = None
        self._community_info_list: List[CommunityInfo] = []
        self._build_community_index()

    def _build_doc_community_mapping(self) -> None:
        """构建文档到社区的映射关系"""
        # 创建节点ID到社区ID的映射
        node_to_community: Dict[str, int] = {}
        for comm_id, nodes in self._communities.items():
            for node in nodes:
                node_to_community[node] = comm_id

        # 将文档分配到对应的社区
        for doc in self._documents:
            source = doc.metadata.get("source", "")
            if not source:
                continue

            # 尝试匹配文档源路径到社区节点
            matched_community = None

            # 直接匹配文件路径
            if source in node_to_community:
                matched_community = node_to_community[source]
            else:
                # 尝试部分路径匹配
                for node_id, comm_id in node_to_community.items():
                    if source.endswith(node_id) or node_id.endswith(source):
                        matched_community = comm_id
                        break
                    # 检查函数/类节点 (格式: file_path:name)
                    if ":" in node_id:
                        file_part = node_id.split(":")[0]
                        if source.endswith(file_part) or file_part.endswith(source):
                            matched_community = comm_id
                            break

            if matched_community is not None:
                self._doc_to_community[compute_doc_key(doc)] = matched_community
                self._community_docs[matched_community].append(doc)

    def _build_community_index(self) -> None:
        """为社区摘要构建 BM25 索引"""
        if not self._summaries:
            return

        # 创建社区信息列表
        for comm_id, summary in self._summaries.items():
            nodes = self._communities.get(comm_id, [])
            self._community_info_list.append(CommunityInfo(
                community_id=comm_id,
                summary=summary,
                node_ids=nodes,
            ))

        # 创建虚拟文档用于 BM25 检索
        community_docs = [
            Document(
                page_content=info.summary,
                metadata={"community_id": info.community_id}
            )
            for info in self._community_info_list
        ]

        if community_docs:
            self._community_bm25 = SparseBM25Index.build(community_docs, self._tokenizer)

    def retrieve_communities(
        self,
        query: str,
        top_k: int = 3,
    ) -> List[CommunityInfo]:
        """
        第一阶段：检索与查询最相关的社区。

        Args:
            query: 用户查询
            top_k: 返回的社区数量

        Returns:
            按相关性排序的社区信息列表
        """
        if not self._community_bm25 or not self._community_info_list:
            return []

        # 使用 BM25 检索社区
        results = self._community_bm25.search(query, top_k=top_k)

        matched_communities = []
        for doc, score in results:
            comm_id = doc.metadata.get("community_id")
            if comm_id is not None:
                for info in self._community_info_list:
                    if info.community_id == comm_id:
                        info.relevance_score = score
                        matched_communities.append(info)
                        break

        return matched_communities

    def retrieve_from_communities(
        self,
        query: str,
        community_ids: List[int],
        top_k_per_community: int = 5,
    ) -> List[Tuple[Document, float]]:
        """
        第二阶段：从指定社区内检索文档。

        Args:
            query: 用户查询
            community_ids: 要检索的社区ID列表
            top_k_per_community: 每个社区返回的文档数量

        Returns:
            (文档, 分数) 元组列表
        """
        all_results: List[Tuple[Document, float]] = []

        for comm_id in community_ids:
            comm_docs = self._community_docs.get(comm_id, [])
            if not comm_docs:
                continue

            # 为该社区的文档构建临时 BM25 索引
            comm_bm25 = SparseBM25Index.build(comm_docs, self._tokenizer)
            results = comm_bm25.search(query, top_k=top_k_per_community)
            all_results.extend(results)

        # 按分数排序并去重
        seen_keys = set()
        unique_results = []
        for doc, score in sorted(all_results, key=lambda x: x[1], reverse=True):
            key = compute_doc_key(doc)
            if key not in seen_keys:
                seen_keys.add(key)
                unique_results.append((doc, score))

        return unique_results

    def retrieve(
        self,
        query: str,
        top_k_communities: int = 3,
        top_k_docs_per_community: int = 5,
        top_k_total: int = 10,
    ) -> List[Tuple[Document, float]]:
        """
        执行完整的两阶段检索。

        Args:
            query: 用户查询
            top_k_communities: 第一阶段选择的社区数量
            top_k_docs_per_community: 每个社区检索的文档数量
            top_k_total: 最终返回的文档总数

        Returns:
            (文档, 分数) 元组列表
        """
        # 第一阶段：检索相关社区
        relevant_communities = self.retrieve_communities(query, top_k=top_k_communities)

        if not relevant_communities:
            # 如果没有匹配到社区，回退到全局 BM25 检索
            fallback_bm25 = SparseBM25Index.build(self._documents, self._tokenizer)
            return fallback_bm25.search(query, top_k=top_k_total)

        # 第二阶段：从相关社区检索文档
        community_ids = [c.community_id for c in relevant_communities]
        results = self.retrieve_from_communities(
            query,
            community_ids,
            top_k_per_community=top_k_docs_per_community,
        )

        return results[:top_k_total]

    def hybrid_retrieve(
        self,
        query: str,
        dense_results: List[Tuple[Document, float]],
        top_k_communities: int = 3,
        top_k_docs_per_community: int = 5,
        alpha: float = 0.6,
        top_k: int = 10,
    ) -> List[RankedCandidate]:
        """
        混合检索：结合社区优先检索和稠密向量检索。

        Args:
            query: 用户查询
            dense_results: 来自向量检索的结果 [(doc, score), ...]
            top_k_communities: 第一阶段选择的社区数量
            top_k_docs_per_community: 每个社区检索的文档数量
            alpha: 稠密检索权重 (1-alpha 为稀疏检索权重)
            top_k: 返回的文档数量

        Returns:
            排序后的候选文档列表
        """
        # 社区优先检索（稀疏）
        sparse_results = self.retrieve(
            query,
            top_k_communities=top_k_communities,
            top_k_docs_per_community=top_k_docs_per_community,
            top_k_total=top_k * 2,
        )

        # 合并结果
        candidates_map: Dict[str, RankedCandidate] = {}

        # 归一化分数
        dense_scores = [s for _, s in dense_results]
        sparse_scores = [s for _, s in sparse_results]
        norm_dense = normalize_scores(dense_scores)
        norm_sparse = normalize_scores(sparse_scores)

        # 添加稠密检索结果
        for idx, (doc, _) in enumerate(dense_results):
            key = compute_doc_key(doc)
            category = doc.metadata.get("category", "unknown")
            candidates_map[key] = RankedCandidate(
                key=key,
                category=category,
                doc=doc,
                dense_score=norm_dense[idx] if idx < len(norm_dense) else 0.0,
                sparse_score=0.0,
            )

        # 添加/更新稀疏检索结果
        for idx, (doc, _) in enumerate(sparse_results):
            key = compute_doc_key(doc)
            category = doc.metadata.get("category", "unknown")
            score = norm_sparse[idx] if idx < len(norm_sparse) else 0.0

            if key in candidates_map:
                candidates_map[key].sparse_score = score
            else:
                candidates_map[key] = RankedCandidate(
                    key=key,
                    category=category,
                    doc=doc,
                    dense_score=0.0,
                    sparse_score=score,
                )

        # 计算最终分数
        for cand in candidates_map.values():
            cand.final_score = alpha * cand.dense_score + (1 - alpha) * cand.sparse_score

        # 排序并返回
        sorted_candidates = sorted(
            candidates_map.values(),
            key=lambda c: c.final_score,
            reverse=True,
        )

        return sorted_candidates[:top_k]


def create_community_retriever(
    communities: Dict[int, List[str]],
    community_summaries: Dict[int, str],
    documents: List[Document],
    tokenizer: Tokenizer | None = None,
) -> CommunityFirstRetriever:
    """
    创建社区优先检索器的工厂函数。

    Args:
        communities: 社区ID到节点ID列表的映射
        community_summaries: 社区ID到摘要的映射
        documents: 所有文档列表
        tokenizer: 可选的分词器

    Returns:
        配置好的 CommunityFirstRetriever 实例
    """
    return CommunityFirstRetriever(
        communities=communities,
        community_summaries=community_summaries,
        documents=documents,
        tokenizer=tokenizer,
    )

