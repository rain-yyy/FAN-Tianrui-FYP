import os
import json
import logging
from pathlib import Path
from typing import Union, Dict, Any

# 使用通用日志记录器
logger = logging.getLogger("app.config")

# 获取项目根目录 (src 的上一级)
PROJECT_ROOT = Path(__file__).parent.parent.absolute()

# 默认配置文件路径 (环境变量优先)
CONFIG_PATH = Path(os.getenv("CONFIG_PATH", str(PROJECT_ROOT / "config" / "repo_config.json")))

def load_config(config_path: Union[str, Path] = CONFIG_PATH) -> Dict[str, Any]:
    """
    从指定路径加载 JSON 配置文件。
    """
    if isinstance(config_path, str):
        config_path = Path(config_path)
        
    if not config_path.exists():
        # 备选路径：相对于当前工作目录查找
        alt_path = Path("config/repo_config.json").absolute()
        if alt_path.exists():
            config_path = alt_path
        else:
            logger.error(f"Configuration file not found at: {config_path}")
            return {}
            
    logger.info(f"Loading configuration from: {config_path}")
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error reading config file: {e}")
        return {}

# 预加载全局配置供其他模块直接使用
CONFIG = load_config()
