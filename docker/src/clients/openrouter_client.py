import os
import logging
from typing import List, Dict, Any, Optional, Generator
from openai import OpenAI
from src.clients.ai_client_base import BaseAIClient
from dotenv import load_dotenv

logger = logging.getLogger("app.clients.openrouter")


class OpenRouterClient(BaseAIClient):
    """
    OpenRouter API 客户端实现（基于 OpenAI SDK）。
    
    支持阻塞式和流式两种调用模式。
    """
    
    DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
    DEFAULT_MODEL = "qwen/qwen3-235b-a22b-2507"

    def __init__(
        self, 
        api_key: Optional[str] = None, 
        model: Optional[str] = None,
        base_url: Optional[str] = None
    ):
        load_dotenv()
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY")
        if not self.api_key:
            raise ValueError("未检测到 OPENROUTER_API_KEY，请设置环境变量。")
        
        self.base_url = base_url or self.DEFAULT_BASE_URL
        self.model = model or self.DEFAULT_MODEL
        self.client = OpenAI(
            api_key=self.api_key, 
            base_url=self.base_url,
            default_headers={
                "HTTP-Referer": "https://github.com/FAN-Tianrui-FYP",
                "X-Title": "FYP Wiki Generator"
            }
        )

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> str:
        """阻塞式聊天接口"""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=False,
                **kwargs
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            raise RuntimeError(f"OpenRouter API 调用失败: {e}")
    
    def stream_chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> Generator[str, None, None]:
        """
        流式聊天接口，逐 token 返回生成内容。
        
        使用 OpenAI SDK 的流式 API，支持真正的增量输出。
        
        Yields:
            str: 每次生成的增量文本片段 (delta)
        """
        try:
            stream = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
                **kwargs
            )
            
            for chunk in stream:
                if chunk.choices and len(chunk.choices) > 0:
                    delta = chunk.choices[0].delta
                    if delta and delta.content:
                        yield delta.content
                        
        except Exception as e:
            logger.error(f"OpenRouter 流式调用失败: {e}")
            raise RuntimeError(f"OpenRouter 流式 API 调用失败: {e}")
    
    def supports_streaming(self) -> bool:
        """OpenRouter 支持流式输出"""
        return True
