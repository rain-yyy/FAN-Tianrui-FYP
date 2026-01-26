# 使用轻量级的 Python 基础镜像
FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 安装系统依赖 (git 是必须的，用于克隆仓库)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

# 先复制依赖文件并安装，利用 Docker 缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 将所有代码文件复制进镜像
# 注意：在 docker-build 时，上下文应该是这个 docker/ 目录
COPY . .

# 暴露 API 端口
EXPOSE 8000

# 设置 PYTHONPATH，确保模块导入正常
ENV PYTHONPATH=/app

# 启动命令
CMD ["python", "scripts/api.py"]
