from __future__ import annotations

import json
import os
from typing import Any, Dict, Iterable, List, MutableMapping


import requests
import dotenv

class DeepseekAPIError(RuntimeError):
    """
    统一封装 DeepSeek API 调用期间的错误，方便调用侧捕获并打印友好的信息。
    """

class DeepseekClient:

    DEFAULT_BASE_URL = "https://api.deepseek.com"
    DEFAULT_MODEL = "deepseek-chat"

    def __init__(
        self,
        *,
        timeout: float = 60.0,
        session: requests.Session | None = None,
    ) -> None:
        dotenv.load_dotenv()
        self.api_key = os.getenv("DEEPSEEK_API_KEY")
        if not self.api_key: 
            raise ValueError(
                "未检测到 DeepSeek API Key，请设置环境变量 DEEPSEEK_API_KEY。"
            )

        self.base_url = self.DEFAULT_BASE_URL
        self.model = self.DEFAULT_MODEL
        self.timeout = timeout
        self._session = session or requests.Session()

    def chat(
        self,
        messages: Iterable[MutableMapping[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1200,
        extra_payload: Dict[str, Any] | None = None,
    ) -> str:
        """
        调用 DeepSeek Chat Completions 接口，并返回首条消息内容。
        该方法保持同步阻塞行为，便于在 CLI 与批处理脚本中复用。
        """
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": list(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if extra_payload:
            payload.update(extra_payload)

        try:
            response = self._session.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:  # pragma: no cover - 网络异常
            raise DeepseekAPIError(f"调用 DeepSeek 失败：{exc}") from exc

        if response.status_code >= 400:
            raise DeepseekAPIError(
                f"DeepSeek 返回错误（{response.status_code}）：{response.text}"
            )

        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise DeepseekAPIError(
                f"DeepSeek 返回内容无法解析为 JSON：{response.text}"
            ) from exc

        try:
            choices: List[Dict[str, Any]] = data["choices"]
            message = choices[0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise DeepseekAPIError(
                f"DeepSeek 返回格式异常：{json.dumps(data, ensure_ascii=False)}"
            ) from exc

        if not isinstance(message, str):
            raise DeepseekAPIError(f"DeepSeek 返回的消息类型非字符串：{message!r}")

        return message.strip()

__all__ = ["DeepseekClient", "DeepseekAPIError"]

