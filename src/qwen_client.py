import os
from typing import List, Dict, Any, Optional
from openai import OpenAI
from src.ai_client_base import BaseAIClient
from dotenv import load_dotenv

class QwenClient(BaseAIClient):
    """
    Qwen (DashScope) API 客户端实现（基于 OpenAI SDK）。
    """
    
    # 默认使用新加坡地域，也可以根据需要支持环境变量配置
    DEFAULT_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    DEFAULT_MODEL = "qwen-plus"

    def __init__(
        self, 
        api_key: Optional[str] = None, 
        model: Optional[str] = None,
        base_url: Optional[str] = None
    ):
        load_dotenv()
        self.api_key = api_key or os.getenv("QWEN_API_KEY")
            
        if not self.api_key:
            raise ValueError("未检测到 QWEN_API_KEY，请设置环境变量。")
        
        self.base_url = base_url or self.DEFAULT_BASE_URL
        self.model = model or self.DEFAULT_MODEL
        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
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
            raise RuntimeError(f"Qwen API 调用失败: {e}")
