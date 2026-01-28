import os
from pathlib import Path
import json

# 获取项目根目录 (src 的上一级)
PROJECT_ROOT = Path(__file__).parent.parent.absolute()

# 配置文件路径
CONFIG_PATH = PROJECT_ROOT / "config" / "repo_config.json"

def load_config(config_path: str | Path = CONFIG_PATH) -> dict:
    """
    加载配置文件，默认为项目根目录下的 config/repo_config.json
    """
    if isinstance(config_path, str):
        config_path = Path(config_path)
        
    if not config_path.exists():
        # 如果找不到，尝试相对于当前工作目录查找 (兼容性处理)
        alt_path = Path("config/repo_config.json").absolute()
        if alt_path.exists():
            config_path = alt_path
        else:
            raise FileNotFoundError(f"配置文件未找到: {config_path}")
            
    print(f"Loading configuration from: {config_path}")
    with open(config_path, 'r', encoding='utf-8') as f:
        return json.load(f)

# 预加载全局配置供其他模块直接使用
try:
    CONFIG = load_config()
except Exception as e:
    print(f"Warning: Failed to load config: {e}")
    CONFIG = {}
