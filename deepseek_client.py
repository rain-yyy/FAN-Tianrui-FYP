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
    """
    负责与 DeepSeek Chat Completions API 通信的轻量级客户端。

    该实现仅覆盖项目当前所需的最小能力：发送对话消息并取回文本结果。
    如后续需要支持流式输出或工具调用，可在此基础上继续迭代。
    """

    DEFAULT_BASE_URL = "https://api.deepseek.com"
    DEFAULT_MODEL = "deepseek-chat"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 60.0,
        session: requests.Session | None = None,
    ) -> None:
        dotenv.load_dotenv()
        self.api_key = (api_key or os.getenv("DEEPSEEK_API_KEY") or "").strip()
        if not self.api_key: 
            raise ValueError(
                "未检测到 DeepSeek API Key，请设置环境变量 DEEPSEEK_API_KEY。"
            )

        # 允许通过环境变量覆盖默认配置，便于在私有化部署时自定义 API Endpoint 与模型名。
        env_base_url = os.getenv("DEEPSEEK_BASE_URL", "").strip()
        env_model = os.getenv("DEEPSEEK_MODEL", "").strip()

        self.base_url = (base_url or env_base_url or self.DEFAULT_BASE_URL).rstrip("/")
        self.model = model or env_model or self.DEFAULT_MODEL
        self.timeout = timeout
        self._session = session or requests.Session()

    def chat(
        self,
        messages: Iterable[MutableMapping[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int = 1200,
        response_model: str | None = None,
        extra_payload: Dict[str, Any] | None = None,
    ) -> str:
        """
        调用 DeepSeek Chat Completions 接口，并返回首条消息内容。
        该方法保持同步阻塞行为，便于在 CLI 与批处理脚本中复用。
        """
        payload: Dict[str, Any] = {
            "model": response_model or self.model,
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
                headers=self._build_headers(),
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

    def _build_headers(self) -> Dict[str, str]:
        """
        构造请求头部，确保认证信息与 JSON 类型声明齐备。
        """
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }


__all__ = ["DeepseekClient", "DeepseekAPIError"]

