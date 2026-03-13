from typing import Optional, Dict, Type, Tuple
from src.clients.ai_client_base import BaseAIClient
from src.clients.openrouter_client import OpenRouterClient

class AIClientFactory:
    """
    AI 客户端工厂类，用于统一管理和获取不同的模型客户端。
    """
    
    _clients: Dict[str, Type[BaseAIClient]] = {
        "openrouter": OpenRouterClient
    }

    @staticmethod
    def get_client(provider: str, **kwargs) -> BaseAIClient:
        """
        获取指定服务商的客户端实例。
        
        Args:
            provider: 服务商名称 ('openrouter')
            **kwargs: 传递给客户端构造函数的参数
            
        Returns:
            BaseAIClient: 客户端实例
        """
        client_class = AIClientFactory._clients.get(provider.lower())
        if not client_class:
            raise ValueError(f"不支持的服务商: {provider}。可选值: {list(AIClientFactory._clients.keys())}")
        
        return client_class(**kwargs)

def get_ai_client(provider: str = "openrouter", **kwargs) -> BaseAIClient:
    """
    便捷函数，用于获取 AI 客户端。
    """
    return AIClientFactory.get_client(provider, **kwargs)

def get_model_config(config: dict, model_key: str) -> Tuple[str, str]:
    """
    从配置中获取指定用途的模型配置。
    
    Args:
        config: 配置字典
        model_key: 模型用途键名（如 'hyde_generation', 'rag_answer'）
        
    Returns:
        (provider, model_name): 提供商和模型名称
    """
    ai_models = config.get("ai_models", {})
    provider = ai_models.get("provider", "openrouter")
    models = ai_models.get("models", {})
    model_name = models.get(model_key, "anthropic/claude-3.5-sonnet")
    return provider, model_name
