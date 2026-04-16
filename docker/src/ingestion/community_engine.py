import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any, DefaultDict, Dict, List, Optional, Set, Tuple

import igraph as ig
import leidenalg
import networkx as nx

from src.clients.ai_client_factory import get_ai_client, get_model_config
from src.config import CONFIG

logger = logging.getLogger("app.ingestion.community_engine")


class CommunityEngine:
    """
    负责对代码图谱进行社区发现并生成业务摘要。
    """

    def __init__(self, graph: nx.DiGraph):
        self.nx_graph = graph
        self.communities = {}
        self.community_summaries = {}

    @staticmethod
    def _community_detection_config() -> Dict[str, Any]:
        raw = CONFIG.get("community_detection") or {}
        if not isinstance(raw, dict):
            return {}
        return raw

    @staticmethod
    def _resolve_file_for_node(node_id: str, data: Dict[str, Any]) -> str:
        if data.get("type") == "file":
            return node_id
        file_attr = data.get("file")
        if isinstance(file_attr, str) and file_attr:
            return file_attr
        if ":" in node_id:
            return node_id.rsplit(":", 1)[0]
        return node_id

    def _expand_files_to_all_nodes(self, files: Set[str]) -> List[str]:
        members: List[str] = []
        for n, d in self.nx_graph.nodes(data=True):
            if self._resolve_file_for_node(n, d) in files:
                members.append(n)
        return members

    def _run_leiden_on_igraph(
        self,
        g: ig.Graph,
        resolution_parameter: float,
        *,
        weighted: bool,
        seed: Optional[int] = None,
    ) -> Any:
        """
        ModularityVertexPartition 不支持 resolution_parameter。
        带权文件级图使用 RBConfigurationVertexPartition，以便用 resolution 控制社区规模。
        """
        opts: Dict[str, Any] = {}
        if seed is not None:
            opts["seed"] = seed
        if weighted and g.ecount() > 0 and "weight" in g.es.attributes():
            return leidenalg.find_partition(
                g,
                leidenalg.RBConfigurationVertexPartition,
                weights="weight",
                resolution_parameter=resolution_parameter,
                **opts,
            )
        return leidenalg.find_partition(
            g,
            leidenalg.ModularityVertexPartition,
            **opts,
        )

    def _run_leiden_full_graph(self, resolution_parameter: float) -> Dict[int, List[str]]:
        undirected_nx = self.nx_graph.to_undirected()
        nodes = list(undirected_nx.nodes())
        if not nodes:
            return {}

        g = ig.Graph.TupleList(undirected_nx.edges(), directed=False)
        for node in nodes:
            if node not in g.vs["name"]:
                g.add_vertex(name=node)

        det_cfg = self._community_detection_config()
        seed_raw = det_cfg.get("leiden_seed")
        seed = int(seed_raw) if seed_raw is not None else None
        partition = self._run_leiden_on_igraph(
            g,
            resolution_parameter=resolution_parameter,
            weighted=False,
            seed=seed,
        )
        self.communities = {}
        for idx, community in enumerate(partition):
            self.communities[idx] = [g.vs[node_idx]["name"] for node_idx in community]
        return self.communities

    def _run_leiden_file_collapsed(
        self,
        resolution_parameter: float,
        call_weight: float,
        dir_chain_weight: float,
    ) -> Dict[int, List[str]]:
        """
        在「文件级」图上做 Leiden，再把社区展开回原始（文件 + 符号）节点。

        缓解混合粒度图上的模块度碎片化，并与 Microsoft GraphRAG 文档中「提高主连通分量/
        调整 resolution」的思路一致：用跨文件调用为强边、同目录链式弱边补足连通性。
        """
        und = self.nx_graph.to_undirected()
        all_files: Set[str] = set()
        for n, d in self.nx_graph.nodes(data=True):
            all_files.add(self._resolve_file_for_node(n, d))

        edge_weights: DefaultDict[Tuple[str, str], float] = defaultdict(float)

        for u, v in und.edges():
            du = self.nx_graph.nodes[u]
            dv = self.nx_graph.nodes[v]
            fu = self._resolve_file_for_node(u, du)
            fv = self._resolve_file_for_node(v, dv)
            if fu == fv:
                continue
            a, b = (fu, fv) if fu <= fv else (fv, fu)
            edge_weights[(a, b)] += call_weight

        # Hub dampening ─────────────────────────────────────────────────
        # Files like utils / helpers / logger accumulate very high cross-file
        # degree and act as super-nodes that pull semantically unrelated
        # modules into the same community.  We apply a sqrt-decay factor on
        # edges incident to any file whose degree exceeds 3× the mean, so
        # their cohesive effect is softened rather than eliminated.
        if edge_weights:
            file_degree: Dict[str, int] = {}
            for (fa, fb) in edge_weights:
                file_degree[fa] = file_degree.get(fa, 0) + 1
                file_degree[fb] = file_degree.get(fb, 0) + 1

            degrees = list(file_degree.values())
            mean_deg = sum(degrees) / len(degrees)
            hub_threshold = max(mean_deg * 3.0, 5)

            for key in list(edge_weights.keys()):
                ka, kb = key
                max_deg = max(file_degree.get(ka, 1), file_degree.get(kb, 1))
                if max_deg > hub_threshold:
                    edge_weights[key] *= (hub_threshold / max_deg) ** 0.5

        # Directory chain ────────────────────────────────────────────────
        # The chain only helps isolate files that have no call/import
        # connections; for files that already participate in the semantic
        # graph the chain edge is noise that overrides structural signals.
        # So: only add a chain edge between a consecutive pair when at
        # least one of the two files is otherwise unconnected.
        connected_files: Set[str] = set()
        for (fa, fb) in edge_weights:
            connected_files.add(fa)
            connected_files.add(fb)

        by_dir: DefaultDict[str, List[str]] = defaultdict(list)
        for fpath in all_files:
            parent = str(Path(fpath).parent)
            by_dir[parent].append(fpath)

        for files_in_dir in by_dir.values():
            if len(files_in_dir) < 2:
                continue
            files_sorted = sorted(files_in_dir)
            for i in range(len(files_sorted) - 1):
                a, b = files_sorted[i], files_sorted[i + 1]
                if a in connected_files and b in connected_files:
                    continue  # both already wired via semantic edges
                key = (a, b) if a <= b else (b, a)
                edge_weights[key] += dir_chain_weight

        if not all_files:
            return {}

        names = sorted(all_files)
        name_to_idx = {n: i for i, n in enumerate(names)}
        g = ig.Graph(n=len(names), directed=False)
        g.vs["name"] = names

        for (a, b), w in edge_weights.items():
            if w <= 0:
                continue
            g.add_edge(name_to_idx[a], name_to_idx[b], weight=w)

        if g.ecount() == 0:
            expanded = self._expand_files_to_all_nodes(set(all_files))
            self.communities = {0: expanded}
            return self.communities

        det_cfg = self._community_detection_config()
        seed_raw = det_cfg.get("leiden_seed")
        seed = int(seed_raw) if seed_raw is not None else None
        partition = self._run_leiden_on_igraph(
            g,
            resolution_parameter=resolution_parameter,
            weighted=True,
            seed=seed,
        )

        comm_to_files: DefaultDict[int, Set[str]] = defaultdict(set)
        for vi, comm_id in enumerate(partition.membership):
            comm_to_files[comm_id].add(g.vs[vi]["name"])

        communities_expanded: Dict[int, List[str]] = {}
        for new_id, (_, files) in enumerate(sorted(comm_to_files.items(), key=lambda x: x[0])):
            communities_expanded[new_id] = self._expand_files_to_all_nodes(set(files))

        self.communities = communities_expanded
        return self.communities

    def _distinct_files_in_community(self, nodes: List[str]) -> Set[str]:
        files: Set[str] = set()
        for node in nodes:
            d = self.nx_graph.nodes[node]
            files.add(self._resolve_file_for_node(node, d))
        return files

    def _prefix_key_for_file(self, file_path: str, prefix_parts: int) -> str:
        parts = file_path.split("/")
        if len(parts) <= 1:
            return parts[0] if parts else ""
        return "/".join(parts[: min(prefix_parts, len(parts))])

    def _merge_target_score(
        self,
        seed_file: str,
        target_nodes: List[str],
        prefix_parts: int,
    ) -> int:
        target_files = self._distinct_files_in_community(target_nodes)
        seed_pref = self._prefix_key_for_file(seed_file, prefix_parts)
        score = 0
        sp = seed_file.split("/")
        for tf in target_files:
            op = tf.split("/")
            k = 0
            for a, b in zip(sp, op):
                if a == b:
                    k += 1
                else:
                    break
            score = max(score, k)
        if any(self._prefix_key_for_file(tf, prefix_parts) == seed_pref for tf in target_files):
            score += 5
        return score

    def _merge_tiny_communities(
        self,
        min_distinct_files: int,
        prefix_parts: int,
    ) -> None:
        """
        将「只有一个可区分文件」的社区并入与之路径前缀最相近的社区，减少无检索意义的单文件社区数量。
        """
        if min_distinct_files <= 1 or not self.communities:
            return

        by_id = dict(self.communities)
        tiny: List[int] = []
        robust: List[int] = []
        for cid, nodes in by_id.items():
            df = self._distinct_files_in_community(nodes)
            if len(df) < min_distinct_files:
                tiny.append(cid)
            else:
                robust.append(cid)

        if not tiny or not robust:
            return

        redirect: Dict[int, int] = {}
        for cid in tiny:
            files = self._distinct_files_in_community(by_id[cid])
            if not files:
                continue
            seed_file = sorted(files)[0]
            best_rid: Optional[int] = None
            best_score = -1
            best_size = -1
            for rid in robust:
                if rid == cid:
                    continue
                s = self._merge_target_score(seed_file, by_id[rid], prefix_parts)
                size = len(by_id[rid])
                if s > best_score or (s == best_score and size > best_size):
                    best_score = s
                    best_size = size
                    best_rid = rid
            if best_rid is not None:
                redirect[cid] = best_rid

        if not redirect:
            return

        merged: DefaultDict[int, List[str]] = defaultdict(list)
        for cid, nodes in by_id.items():
            target = redirect.get(cid, cid)
            merged[target].extend(nodes)

        reindexed: Dict[int, List[str]] = {}
        for new_id, (_, nodes) in enumerate(sorted(merged.items(), key=lambda x: x[0])):
            seen: Set[str] = set()
            deduped: List[str] = []
            for n in nodes:
                if n not in seen:
                    seen.add(n)
                    deduped.append(n)
            reindexed[new_id] = deduped

        self.communities = reindexed
        self.community_summaries = {}

    def run_leiden(self) -> Dict[int, List[str]]:
        """
        运行 Leiden 算法进行社区划分。
        """
        cfg = self._community_detection_config()
        resolution = float(cfg.get("resolution_parameter", 0.55))
        use_collapse = bool(cfg.get("file_level_collapse", True))
        call_w = float(cfg.get("cross_file_call_weight", 1.0))
        dir_w = float(cfg.get("same_directory_chain_weight", 0.22))
        merge_min_files = int(cfg.get("merge_min_distinct_files", 2))
        merge_prefix = int(cfg.get("merge_prefix_parts", 2))

        if use_collapse:
            self._run_leiden_file_collapsed(
                resolution_parameter=resolution,
                call_weight=call_w,
                dir_chain_weight=dir_w,
            )
        else:
            self._run_leiden_full_graph(resolution_parameter=resolution)

        self._merge_tiny_communities(
            min_distinct_files=merge_min_files,
            prefix_parts=merge_prefix,
        )

        logger.info(
            "Leiden 完成: %s 个社区（file_level_collapse=%s, resolution=%s）",
            len(self.communities),
            use_collapse,
            resolution,
        )
        return self.communities

    def generate_summaries(self):
        """
        为每个社区生成业务摘要。
        """
        provider, model = get_model_config(CONFIG, "community_summary")
        client = get_ai_client(provider, model=model)

        for comm_id, nodes in self.communities.items():
            node_details = []
            for node in nodes:
                data = self.nx_graph.nodes[node]
                node_type = data.get("type", "unknown")
                if node_type in ("file", "class", "function"):
                    node_details.append(f"- {node} ({node_type})")

            if not node_details:
                for node in nodes[:40]:
                    node_details.append(f"- {node}")

            if not node_details:
                continue

            context = "\n".join(node_details[:40])

            prompt = f"""You are a senior software architect. The following list groups code entities that belong to one logical business community in the repository.

Write a brief summary (at most ~100 words) of what this community is responsible for and how it fits into the overall project.

Entity list:
{context}

Return only the summary text, in English."""

            try:
                summary = client.chat([{"role": "user", "content": prompt}])
                self.community_summaries[comm_id] = summary
            except Exception as e:
                logger.error(f"Error generating summary for community {comm_id}: {e}")
                self.community_summaries[comm_id] = "Unable to generate summary."

        return self.community_summaries

    def get_node_community(self, node_id: str) -> Optional[int]:
        for comm_id, nodes in self.communities.items():
            if node_id in nodes:
                return comm_id
        return None

    def save_results(self, output_path: str):
        results = {
            "communities": self.communities,
            "summaries": self.community_summaries,
        }
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
