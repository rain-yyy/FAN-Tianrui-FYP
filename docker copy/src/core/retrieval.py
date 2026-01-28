from __future__ import annotations

import hashlib
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Sequence, Tuple

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

