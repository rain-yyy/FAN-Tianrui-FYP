from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import sys
from pathlib import Path
import json
from typing import Dict, Any

# 获取项目根目录并添加到 sys.path
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 导入必要的模块
from scripts.setup_repository import setup_repository
from src.config import CONFIG_PATH, load_config
from src.ingestion.file_processor import generate_file_tree
from src.wiki.struct_gen import generate_wiki_structure
from src.wiki.content_gen import WikiContentGenerator
from src.clients.ai_client_factory import get_ai_client
from src.storage.r2_client import upload_wiki_to_r2

app = FastAPI(
    title="Project Wiki Generation API",
    description="将项目仓库 URL 转换为 Wiki 结构和内容的 API"
)

# 配置 CORS，允许前端跨域请求
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 开发环境下允许所有来源，生产环境应指定具体域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 默认路径配置
DEFAULT_OUTPUT_PATH: Path = PROJECT_ROOT / "wiki_structure.json"
DEFAULT_WIKI_SECTION_JSON_OUTPUT: Path = PROJECT_ROOT / "wiki_section_json"

class GenRequest(BaseModel):
    url_link: str

class GenResponse(BaseModel):
    r2_structure_url: str | None = None  # R2 中 wiki_structure.json 的 URL
    r2_content_base_url: str | None = None  # R2 中 content 目录的基础 URL
    json_wiki: str | None = None  # 保留旧字段以兼容
    json_content: str | None = None  # 保留旧字段以兼容

def run_structure_generation(
    repo_url_or_path: str, config_path: Path, output_path: Path
) -> tuple[str, Dict[str, Any]]:
    """
    根据仓库地址（Git URL 或本地路径）生成 wiki 目录结构，并保存到指定文件。
    """
    if repo_url_or_path.startswith(("http://", "https://", "git@")):
        repo_path = setup_repository(repo_url_or_path)
    else:
        repo_path = repo_url_or_path

    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在：{config_path}")

    config = load_config(str(config_path))

    file_tree = generate_file_tree(repo_path, str(config_path))

    wiki_structure = generate_wiki_structure(repo_path, file_tree)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(wiki_structure, f, indent=2, ensure_ascii=False)

    return repo_path, wiki_structure


def run_wiki_content_generation(
    repo_path: str,
    wiki_structure: Dict[str, Any],
    json_output_dir: Path,
) -> list[Path]:
    """
    调用 AI 客户端，根据 wiki 目录逐条生成内容与 Mermaid 图，并写入 JSON。
    """
    client = get_ai_client("qwen")
    generator = WikiContentGenerator(
        repo_root=repo_path,
        json_output_dir=json_output_dir,
        client=client,
    )
    return generator.generate(wiki_structure)

@app.post("/generate", response_model=GenResponse)
async def generate_wiki(request: GenRequest):
    try:
        # 获取默认路径
        config_path = CONFIG_PATH.expanduser().resolve()
        output_path = DEFAULT_OUTPUT_PATH.expanduser().resolve()
        json_output_dir = DEFAULT_WIKI_SECTION_JSON_OUTPUT.expanduser().resolve()

        # 1. 生成项目结构 (wiki_structure.json)
        # repo_url_or_path 可以是 git url 或本地路径
        repo_path, wiki_structure = run_structure_generation(
            repo_url_or_path=request.url_link,
            config_path=config_path,
            output_path=output_path
        )

        # 2. 生成 Wiki 内容和对应的 JSON 详情
        # 注意：这里会调用 AI 客户端生成内容，耗时可能较长
        run_wiki_content_generation(
            repo_path=repo_path,
            wiki_structure=wiki_structure,
            json_output_dir=json_output_dir
        )

        # 3. 上传到 R2 存储
        r2_structure_url, r2_content_base_url = upload_wiki_to_r2(
            repo_url=request.url_link,
            wiki_structure=wiki_structure,
            structure_local_path=output_path,
            content_dir=json_output_dir,
        )

        # 返回响应，优先返回 R2 URL，如果 R2 上传失败则返回本地路径
        return GenResponse(
            r2_structure_url=r2_structure_url,
            r2_content_base_url=r2_content_base_url,
            json_wiki=str(output_path) if not r2_structure_url else None,
            json_content=str(json_output_dir) if not r2_content_base_url else None,
        )
    except Exception as e:
        # 打印错误栈以便调试
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health_check():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    # 建议使用 uvicorn scripts.api:app --reload 进行开发
    uvicorn.run(app, host="0.0.0.0", port=8000)
