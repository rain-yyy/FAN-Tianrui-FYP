from typing import Optional, Dict, Type
from src.ai_client_base import BaseAIClient
from src.openai_client import OpenAIClient
from src.deepseek_client import DeepseekClient
from src.qwen_client import QwenClient

class AIClientFactory:
    """
    AI 客户端工厂类，用于统一管理和获取不同的模型客户端。
    """
    
    _clients: Dict[str, Type[BaseAIClient]] = {
        "openai": OpenAIClient,
        "deepseek": DeepseekClient,
        "qwen": QwenClient
    }

    @staticmethod
    def get_client(provider: str, **kwargs) -> BaseAIClient:
        """
        获取指定服务商的客户端实例。
        
        Args:
            provider: 服务商名称 ('openai', 'deepseek', 'qwen')
            **kwargs: 传递给客户端构造函数的参数
            
        Returns:
            BaseAIClient: 客户端实例
        """
        client_class = AIClientFactory._clients.get(provider.lower())
        if not client_class:
            raise ValueError(f"不支持的服务商: {provider}。可选值: {list(AIClientFactory._clients.keys())}")
        
        return client_class(**kwargs)

def get_ai_client(provider: str = "openai", **kwargs) -> BaseAIClient:
    """
    便捷函数，用于获取 AI 客户端。
    """
    return AIClientFactory.get_client(provider, **kwargs)
