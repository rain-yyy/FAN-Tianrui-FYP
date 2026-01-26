from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

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
        统一的聊天接口。
        
        Args:
            messages: 消息列表，格式为 [{"role": "user", "content": "..."}]
            temperature: 生成温度
            max_tokens: 最大生成 token 数
            **kwargs: 其他透传给底层的参数
            
        Returns:
            str: 模型生成的文本内容
        """
        pass
