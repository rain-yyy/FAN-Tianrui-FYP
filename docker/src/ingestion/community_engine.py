import networkx as nx
import leidenalg
import igraph as ig
import logging
from typing import List, Dict, Any, Optional
from src.clients.ai_client_factory import get_ai_client, get_model_config
from src.config import CONFIG
import json

logger = logging.getLogger("app.ingestion.community_engine")

class CommunityEngine:
    """
    负责对代码图谱进行社区发现并生成业务摘要。
    """

    def __init__(self, graph: nx.DiGraph):
        self.nx_graph = graph
        self.communities = {}
        self.community_summaries = {}

    def run_leiden(self) -> Dict[int, List[str]]:
        """
        运行 Leiden 算法进行社区划分。
        """
        # 将 NetworkX 图转换为 igraph
        # Leiden 算法通常在无向图上表现更好，用于发现聚类
        undirected_nx = self.nx_graph.to_undirected()
        
        # 过滤掉孤立节点（可选）
        nodes = list(undirected_nx.nodes())
        if not nodes:
            return {}
            
        g = ig.Graph.TupleList(undirected_nx.edges(), directed=False)
        # igraph 可能不包含所有孤立节点，需要手动处理
        for node in nodes:
            if node not in g.vs['name']:
                g.add_vertex(name=node)

        # 运行 Leiden 算法
        partition = leidenalg.find_partition(g, leidenalg.ModularityVertexPartition)
        
        self.communities = {}
        for idx, community in enumerate(partition):
            self.communities[idx] = [g.vs[node_idx]['name'] for node_idx in community]
            
        return self.communities

    def generate_summaries(self):
        """
        为每个社区生成业务摘要。
        """
        provider, model = get_model_config(CONFIG, "community_summary")
        client = get_ai_client(provider, model=model)
        
        for comm_id, nodes in self.communities.items():
            # 准备社区上下文
            # 只选取有代表性的节点（如文件和类）
            node_details = []
            for node in nodes:
                data = self.nx_graph.nodes[node]
                node_type = data.get("type", "unknown")
                if node_type in ["file", "class"]:
                    node_details.append(f"- {node} ({node_type})")
            
            if not node_details:
                continue

            context = "\n".join(node_details[:30]) # 限制数量避免过长
            
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
            "summaries": self.community_summaries
        }
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
