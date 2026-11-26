"""Unified, .env-driven LLM client for IFSP V2 Core.

Supports: ollama, nollama, openai, azure, anthropic, openvino, custom.
Switching providers is a .env-only change (LLM_PROVIDER=<provider>).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Generator, Optional
from urllib import error, request


def _normalize_loopback(url: str) -> str:
    """Replace localhost with 127.0.0.1 to avoid 2s IPv6 ::1 fallback delays on Windows."""
    clean = (url or "").strip().rstrip("/")
    if clean.startswith("http://localhost"):
        return clean.replace("http://localhost", "http://127.0.0.1", 1)
    if clean.startswith("https://localhost"):
        return clean.replace("https://localhost", "https://127.0.0.1", 1)
    return clean


@dataclass(frozen=True)
class LLMSettings:
    provider: str
    base_url: str
    model: str
    sql_model: str
    vision_model: str
    api_key: Optional[str] = None
    auth_header: str = "Authorization"
    api_version: Optional[str] = None
    http_timeout_secs: int = 120
    ttft_timeout_secs: int = 90


def load_llm_settings() -> LLMSettings:
    """Build active LLMSettings purely from environment variables."""
    provider = os.getenv("LLM_PROVIDER", "ollama").strip().lower()
    http_timeout = max(15, int(os.getenv("STREAM_LLM_HTTP_TIMEOUT_SECS", "120")))
    ttft_timeout = max(10, int(os.getenv("STREAM_LLM_TTFT_TIMEOUT_SECS", "90")))

    if provider == "ollama":
        model = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:latest").strip()
        return LLMSettings(
            provider="ollama",
            base_url=_normalize_loopback(os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")),
            model=model,
            sql_model=os.getenv("OLLAMA_SQL_MODEL", model).strip(),
            vision_model=os.getenv("OLLAMA_VISION_MODEL", "gemma3:latest").strip(),
            http_timeout_secs=http_timeout,
            ttft_timeout_secs=ttft_timeout,
        )

    if provider == "openai":
        model = os.getenv("OPENAI_MODEL", "gpt-4o").strip()
        return LLMSettings(
            provider="openai",
            base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").strip().rstrip("/"),
            model=model,
            sql_model=model,
            vision_model=os.getenv("OPENAI_VISION_MODEL", model).strip(),
            api_key=os.getenv("OPENAI_API_KEY", "").strip() or None,
            http_timeout_secs=http_timeout,
            ttft_timeout_secs=ttft_timeout,
        )

    if provider == "azure":
        endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "").strip().rstrip("/")
        deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT", "").strip()
        return LLMSettings(
            provider="azure",
            base_url=f"{endpoint}/openai/deployments/{deployment}" if endpoint and deployment else "",
            model=deployment,
            sql_model=deployment,
            vision_model=os.getenv("AZURE_OPENAI_VISION_DEPLOYMENT", deployment).strip(),
            api_key=os.getenv("AZURE_OPENAI_API_KEY", "").strip() or None,
            auth_header="api-key",
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-01").strip(),
            http_timeout_secs=http_timeout,
            ttft_timeout_secs=ttft_timeout,
        )

    if provider == "anthropic":
        model = os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022").strip()
        return LLMSettings(
            provider="anthropic",
            base_url=os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1").strip().rstrip("/"),
            model=model,
            sql_model=model,
            vision_model=os.getenv("ANTHROPIC_VISION_MODEL", model).strip(),
            api_key=os.getenv("ANTHROPIC_API_KEY", "").strip() or None,
            auth_header="x-api-key",
            http_timeout_secs=http_timeout,
            ttft_timeout_secs=ttft_timeout,
        )

    if provider == "custom":
        model = os.getenv("CUSTOM_LLM_MODEL", "").strip()
        return LLMSettings(
            provider="custom",
            base_url=_normalize_loopback(os.getenv("CUSTOM_LLM_BASE_URL", "")),
            model=model,
            sql_model=model,
            vision_model=model,
            api_key=os.getenv("CUSTOM_LLM_API_KEY", "").strip() or None,
            http_timeout_secs=http_timeout,
            ttft_timeout_secs=ttft_timeout,
        )

    # Default: nollama
    model = os.getenv("NOLLAMA_MODEL", "Qwen2.5-Coder-7B-Instruct@GPU").strip()
    return LLMSettings(
        provider="nollama",
        base_url=_normalize_loopback(os.getenv("NOLLAMA_BASE_URL", "http://127.0.0.1:8000")),
        model=model,
        sql_model=model,
        vision_model=os.getenv("NOLLAMA_VISION_MODEL", model).strip(),
        http_timeout_secs=http_timeout,
        ttft_timeout_secs=ttft_timeout,
    )


class UnifiedLLMClient:
    """Thin, stateless HTTP client for chat completions and streaming."""

    def __init__(self, settings: Optional[LLMSettings] = None) -> None:
        self.settings = settings or load_llm_settings()

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.settings.api_key:
            if self.settings.auth_header == "Authorization":
                headers["Authorization"] = f"Bearer {self.settings.api_key}"
            else:
                headers[self.settings.auth_header] = self.settings.api_key
        if self.settings.provider == "anthropic":
            headers["anthropic-version"] = "2023-06-01"
        return headers

    def _completions_url(self) -> str:
        if self.settings.provider == "azure":
            return f"{self.settings.base_url}/chat/completions?api-version={self.settings.api_version}"
        return f"{self.settings.base_url}/v1/chat/completions"

    def list_models(self) -> Dict[str, Any]:
        req = request.Request(
            f"{self.settings.base_url}/v1/models",
            headers=self._headers(),
            method="GET",
        )
        try:
            with request.urlopen(req, timeout=10) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            models = [m.get("id", "").strip() for m in body.get("data", []) if m.get("id")]
            return {
                "provider": self.settings.provider,
                "reachable": True,
                "default_model": self.settings.model,
                "models": models,
            }
        except (error.URLError, TimeoutError, json.JSONDecodeError, OSError):
            return {
                "provider": self.settings.provider,
                "reachable": False,
                "default_model": self.settings.model,
                "models": [],
            }

    def chat(
        self,
        prompt: str,
        system_prompt: str,
        model: Optional[str] = None,
        max_tokens: int = 400,
        temperature: float = 0.2,
    ) -> Optional[str]:
        payload = {
            "model": (model or self.settings.model).strip(),
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        req = request.Request(
            self._completions_url(),
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self.settings.http_timeout_secs) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            choices = body.get("choices") or []
            if choices:
                return ((choices[0].get("message") or {}).get("content") or "").strip() or None
        except (error.URLError, TimeoutError, json.JSONDecodeError, OSError):
            return None
        return None

    def stream(
        self,
        prompt: str,
        system_prompt: str,
        model: Optional[str] = None,
        max_tokens: int = 400,
        temperature: float = 0.2,
    ) -> Generator[str, None, None]:
        payload = {
            "model": (model or self.settings.model).strip(),
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        req = request.Request(
            self._completions_url(),
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        started = time.perf_counter()
        first_token = False
        try:
            with request.urlopen(req, timeout=self.settings.http_timeout_secs) as resp:
                for raw_line in resp:
                    if (not first_token) and ((time.perf_counter() - started) > self.settings.ttft_timeout_secs):
                        break
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line or line == "data: [DONE]" or not line.startswith("data: "):
                        continue
                    try:
                        chunk = json.loads(line[6:])
                    except json.JSONDecodeError:
                        continue
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = (choices[0].get("delta") or {}).get("content") or ""
                    if delta:
                        first_token = True
                        yield delta
        except (error.URLError, TimeoutError, OSError):
            return
