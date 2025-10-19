from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests


@dataclass
class YandexGPTConfig:
    api_key: str
    folder_id: str
    model: str = "yandexgpt-lite"
    completion_url: str = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
    timeout_sec: int = 60


class YandexGPTClient:
    def __init__(self, config: YandexGPTConfig) -> None:
        self.config = config

    def complete(self, prompt: str, *, temperature: float = 0.0, max_tokens: int = 800) -> str:
        headers = {
            "Authorization": f"Api-Key {self.config.api_key}",
            "x-folder-id": self.config.folder_id,
            "Content-Type": "application/json",
        }
        payload: Dict[str, Any] = {
            "modelUri": f"gpt://{self.config.folder_id}/{self.config.model}/latest",
            "completionOptions": {"stream": False, "temperature": temperature, "maxTokens": max_tokens},
            "messages": [
                {"role": "user", "text": prompt},
            ],
        }
        resp = requests.post(self.config.completion_url, headers=headers, json=payload, timeout=self.config.timeout_sec)
        try:
            resp.raise_for_status()
        except requests.HTTPError as e:
            # Вернём подробности от API для диагностики
            detail = None
            try:
                detail = resp.json()
            except Exception:
                detail = resp.text
            raise requests.HTTPError(f"{e} | details={detail}") from e
        data = resp.json()
        text: str = (
            data.get("result", {})
            .get("alternatives", [{}])[0]
            .get("message", {})
            .get("text", "")
        )
        return text


