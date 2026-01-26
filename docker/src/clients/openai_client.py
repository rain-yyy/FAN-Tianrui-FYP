import os
from typing import List, Dict, Any, Optional
from openai import OpenAI
from src.clients.ai_client_base import BaseAIClient
from dotenv import load_dotenv

class OpenAIClient(BaseAIClient):
    """
    OpenAI API 客户端实现。
    """
    
    DEFAULT_MODEL = "gpt-4o-mini"

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        load_dotenv()
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("未找到 OPENAI_API_KEY，请在环境变量中设置。")
        
        self.client = OpenAI(api_key=self.api_key)
        self.model = model or self.DEFAULT_MODEL

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
            raise RuntimeError(f"OpenAI API 调用失败: {e}")
