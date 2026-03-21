from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Iterator, Generator

class BaseAIClient(ABC):
    """
    AI 客户端基类，定义统一的调用接口。
    """
    
    @abstractmethod
    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> str:
        """
        统一的聊天接口（阻塞式）。
        
        Args:
            messages: 消息列表，格式为 [{"role": "user", "content": "..."}]
            temperature: 生成温度
            max_tokens: 最大生成 token 数
            **kwargs: 其他透传给底层的参数
            
        Returns:
            str: 模型生成的文本内容
        """
        pass
    
    def stream_chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> Generator[str, None, None]:
        """
        流式聊天接口，逐 token 返回生成内容。
        
        Args:
            messages: 消息列表，格式为 [{"role": "user", "content": "..."}]
            temperature: 生成温度
            max_tokens: 最大生成 token 数
            **kwargs: 其他透传给底层的参数
            
        Yields:
            str: 每次生成的增量文本片段 (delta)
        """
        # 默认实现：回退到阻塞式调用，一次性返回全部内容
        result = self.chat(messages, temperature, max_tokens, **kwargs)
        yield result
    
    def supports_streaming(self) -> bool:
        """
        检查客户端是否支持真正的流式输出。
        
        Returns:
            bool: True 表示支持流式，False 表示回退到阻塞式
        """
        return False
