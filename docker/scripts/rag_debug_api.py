import sys
import os
from pathlib import Path
import json
import logging

# 获取项目根目录并添加到 sys.path
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
# 尝试加载 .env 文件，根据你的环境可能需要调整路径
load_dotenv(PROJECT_ROOT / ".env")

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Any, Optional

from src.agent.tools.rag_tool import RAGSearchTool
from src.agent.state import ContextPiece

# 初始化日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("rag_debug_api")

app = FastAPI(title="RAG Tool Debug API")

class RAGRequest(BaseModel):
    query: str
    vector_store_path: str
    top_k: int = 20

@app.post("/debug/rag")
async def debug_rag(request: RAGRequest):
    """
    调试 RAG 工具的中间逻辑和最终输出
    """
    try:
        # 确保路径存在
        if not os.path.exists(request.vector_store_path):
            raise HTTPException(status_code=404, detail=f"Vector store path not found: {request.vector_store_path}")

        # 初始化工具
        tool = RAGSearchTool(vector_store_path=request.vector_store_path)
        
        # 执行检索
        # 注意：execute 现在内部会自动处理 HyDE 逻辑
        result: ContextPiece = tool.execute(query=request.query, top_k=request.top_k)
        
        # 构造返回结果，包含一些元数据以便观察中间逻辑
        return {
            "query": request.query,
            "vector_store_path": request.vector_store_path,
            "result": {
                "source": result.source,
                "content": result.content,
                "relevance_score": result.relevance_score,
                "metadata": result.metadata
            }
        }
    except Exception as e:
        logger.exception("RAG debug failed")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
