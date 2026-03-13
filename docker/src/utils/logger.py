import logging
import sys
import os

def setup_logger(name: str = "app") -> logging.Logger:
    """
    配置并返回标准 Python 日志记录器。
    """
    logger = logging.getLogger(name)
    
    # 如果已经有处理程序，则不再添加
    if logger.handlers:
        return logger
        
    logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
    
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # 标准输出处理器
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    return logger

# 预创建全局 logger
logger = setup_logger()
