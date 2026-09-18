import sys
from pathlib import Path

# 获取项目根目录并添加到 sys.path（必须在导入 src 模块之前）
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv("../../.env.local")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.routers import agent, chat, files, health, repos, tasks

app = FastAPI(
    title="Project Wiki Generation API",
    description="Project Wiki Generation API",
)

# 配置 CORS，允许前端跨域请求
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 开发环境下允许所有来源，生产环境应指定具体域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(tasks.router)
app.include_router(repos.router)
app.include_router(files.router)
app.include_router(chat.router)
app.include_router(agent.router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
