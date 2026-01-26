import os
import json
from typing import List, Dict, Any, Optional
from openai import OpenAI
from src.clients.ai_client_base import BaseAIClient
from dotenv import load_dotenv

class DeepseekAPIError(RuntimeError):
    """
    统一封装 DeepSeek API 调用期间的错误。
    """
    pass

class DeepseekClient(BaseAIClient):
    """
    DeepSeek API 客户端实现（基于 OpenAI SDK）。
    """
    
    DEFAULT_BASE_URL = "https://api.deepseek.com"
    DEFAULT_MODEL = "deepseek-chat"

    def __init__(
        self, 
        api_key: Optional[str] = None, 
        model: Optional[str] = None,
        base_url: Optional[str] = None
    ):
        load_dotenv()
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY")
        if not self.api_key:
            raise ValueError("未检测到 DEEPSEEK_API_KEY，请设置环境变量。")
        
        self.base_url = base_url or self.DEFAULT_BASE_URL
        self.model = model or self.DEFAULT_MODEL
        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.2,
        max_tokens: Optional[int] = 1200,
        **kwargs
    ) -> str:
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            raise DeepseekAPIError(f"DeepSeek API 调用失败: {e}")

    # 为了保持向后兼容，保留 chat 方法的别名（如果需要）
    # 目前 chat 方法签名已经与 BaseAIClient 一致，但默认参数有所不同
