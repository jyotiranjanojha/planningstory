"""Unified, .env-driven LLM client for IFSP V2 Core.

Supports: ollama, snowflake_cortex, nollama, openai, azure, anthropic, openvino, custom.
Switching providers is a .env-only change (LLM_PROVIDER=<provider>).
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Generator, List, Optional
from urllib import error, request

_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def strip_think_tags(text: str) -> str:
    """Remove <think>...</think> reasoning blocks emitted by Qwen3 / DeepSeek-R1 models."""
    if not text:
        return ""
    cleaned = _THINK_BLOCK_RE.sub("", text)
    if "</think>" in cleaned:
        cleaned = cleaned.split("</think>", 1)[-1]
    elif "<think>" in cleaned:
        cleaned = cleaned.split("<think>", 1)[0]
    return cleaned.strip()


def should_disable_thinking(provider: str, model: str) -> bool:
    """Disable Qwen3 chain-of-thought verbosity on OpenAI-compatible/vLLM endpoints by default."""
    flag = os.getenv("CUSTOM_LLM_DISABLE_THINKING", "auto").strip().lower()
    if flag in {"1", "true", "yes", "on"}:
        return True
    if flag in {"0", "false", "no", "off"}:
        return False
    return provider == "custom" and "qwen3" in (model or "").lower()


def _normalize_loopback(url: str) -> str:
    """Replace localhost with 127.0.0.1 to avoid 2s IPv6 ::1 fallback delays on Windows."""
    clean = (url or "").strip().rstrip("/")
    if clean.startswith("http://localhost"):
        return clean.replace("http://localhost", "http://127.0.0.1", 1)
    if clean.startswith("https://localhost"):
        return clean.replace("https://localhost", "https://127.0.0.1", 1)
    return clean


def load_llm_temperature(default: float = 0.1) -> float:
    """Read LLM_TEMPERATURE from env (default 0.1 for deterministic planning responses)."""
    raw = os.getenv("LLM_TEMPERATURE", "").strip()
    if not raw:
        return default
    try:
        val = float(raw)
    except ValueError:
        return default
    return max(0.0, min(2.0, val))


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
    token_type: Optional[str] = None
    cortex_mode: str = "rest"  # "rest" (PAT/JWT) or "sql" (SNOWFLAKE.CORTEX.COMPLETE)
    temperature: float = 0.1
    http_timeout_secs: int = 120
    ttft_timeout_secs: int = 90


_SF_CONN_LOCK = threading.Lock()
_SF_CONN: Any = None


def _parse_cortex_sql_result(raw: Any) -> Optional[str]:
    """Extract assistant text from SNOWFLAKE.CORTEX.COMPLETE() SQL output."""
    if raw is None:
        return None
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        if text.startswith("{"):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                return text
            raw = parsed
        else:
            return text
    if isinstance(raw, dict):
        choices = raw.get("choices") or []
        if choices and isinstance(choices[0], dict):
            c0 = choices[0]
            if isinstance(c0.get("messages"), str):
                return c0["messages"].strip() or None
            msg = c0.get("message")
            if isinstance(msg, dict) and isinstance(msg.get("content"), str):
                return msg["content"].strip() or None
            if isinstance(c0.get("text"), str):
                return c0["text"].strip() or None
        if isinstance(raw.get("content"), str):
            return raw["content"].strip() or None
    return str(raw).strip() or None


def _get_snowflake_connection():
    """Return a cached snowflake.connector connection (supports externalbrowser SSO)."""
    global _SF_CONN
    with _SF_CONN_LOCK:
        if _SF_CONN is not None:
            try:
                if not _SF_CONN.is_closed():
                    return _SF_CONN
            except Exception:
                _SF_CONN = None

        import snowflake.connector  # type: ignore

        account = (os.getenv("SNOWFLAKE_CORTEX_ACCOUNT") or os.getenv("SNOWFLAKE_ACCOUNT") or "").strip()
        account = account.replace("https://", "").replace("http://", "").split(".snowflakecomputing.com")[0].strip("/")
        user = (os.getenv("SNOWFLAKE_USER") or "").strip()
        authenticator = (os.getenv("SNOWFLAKE_AUTHENTICATOR") or "externalbrowser").strip()
        kwargs: Dict[str, Any] = {
            "account": account,
            "user": user,
            "authenticator": authenticator,
            "client_store_temporary_credential": True,
        }
        password = (os.getenv("SNOWFLAKE_PASSWORD") or "").strip()
        if password and authenticator.lower() == "snowflake":
            kwargs["password"] = password
        for env_key, param_key in (
            ("SNOWFLAKE_ROLE", "role"),
            ("SNOWFLAKE_WAREHOUSE", "warehouse"),
            ("SNOWFLAKE_DATABASE", "database"),
            ("SNOWFLAKE_SCHEMA", "schema"),
        ):
            val = (os.getenv(env_key) or "").strip()
            if val:
                kwargs[param_key] = val

        _SF_CONN = snowflake.connector.connect(**kwargs)
        # If no warehouse was configured in .env, auto-select the first available warehouse
        if "warehouse" not in kwargs:
            try:
                cur = _SF_CONN.cursor()
                try:
                    cur.execute("SHOW WAREHOUSES")
                    wh_row = cur.fetchone()
                    if wh_row and wh_row[0]:
                        cur.execute(f'USE WAREHOUSE "{wh_row[0]}"')
                finally:
                    cur.close()
            except Exception:
                pass
        return _SF_CONN


def cortex_sql_complete(
    messages: List[Dict[str, str]],
    model: str,
    temperature: float = 0.1,
    max_tokens: int = 500,
) -> Optional[str]:
    """Call SNOWFLAKE.CORTEX.COMPLETE(model, messages, options) via Snowflake SQL."""
    conn = _get_snowflake_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT SNOWFLAKE.CORTEX.COMPLETE(
                %s,
                PARSE_JSON(%s),
                OBJECT_CONSTRUCT('temperature', %s, 'max_tokens', %s)
            )
            """,
            (model, json.dumps(messages), float(temperature), int(max_tokens)),
        )
        row = cur.fetchone()
        return _parse_cortex_sql_result(row[0] if row else None)
    finally:
        try:
            cur.close()
        except Exception:
            pass


_DEFAULT_CORTEX_MODELS = [
    "claude-3-5-sonnet",
    "claude-3-7-sonnet",
    "llama3.3-70b",
    "llama3.1-70b",
    "mistral-large2",
    "snowflake-arctic",
    "openai-gpt-4.1",
]


def _normalize_custom_base_url(url: str) -> str:
    """Normalize custom OpenAI-compatible base URL (accepts root, /v1, or /v1/chat/completions)."""
    clean = _normalize_loopback(url)
    for suffix in ("/v1/chat/completions", "/chat/completions", "/v1"):
        if clean.lower().endswith(suffix):
            clean = clean[: -len(suffix)].rstrip("/")
    return clean


def _resolve_cortex_base_url() -> str:
    explicit = os.getenv("SNOWFLAKE_CORTEX_BASE_URL", "").strip().rstrip("/")
    if explicit:
        return explicit if explicit.startswith(("http://", "https://")) else f"https://{explicit}"
    account = (os.getenv("SNOWFLAKE_CORTEX_ACCOUNT") or os.getenv("SNOWFLAKE_ACCOUNT") or "").strip()
    if account:
        if account.startswith(("http://", "https://")):
            return account.rstrip("/")
        if "." in account:
            return f"https://{account.rstrip('/')}"
        return f"https://{account}.snowflakecomputing.com"
    return ""


def load_llm_settings() -> LLMSettings:
    """Build active LLMSettings purely from environment variables."""
    provider = os.getenv("LLM_PROVIDER", "ollama").strip().lower()
    temperature = load_llm_temperature(0.1)
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
            temperature=temperature,
            http_timeout_secs=http_timeout,
            ttft_timeout_secs=ttft_timeout,
        )

    if provider in {"snowflake_cortex", "cortex", "snowflake"}:
        model = os.getenv("SNOWFLAKE_CORTEX_MODEL", "claude-3-5-sonnet").strip()
        cortex_mode = os.getenv("SNOWFLAKE_CORTEX_MODE", "rest").strip().lower()
        if cortex_mode not in {"rest", "sql"}:
            cortex_mode = "rest"
        return LLMSettings(
            provider="snowflake_cortex",
            base_url=_resolve_cortex_base_url(),
            model=model,
            sql_model=os.getenv("SNOWFLAKE_CORTEX_SQL_MODEL", model).strip(),
            vision_model=os.getenv("SNOWFLAKE_CORTEX_VISION_MODEL", model).strip(),
            api_key=(os.getenv("SNOWFLAKE_CORTEX_TOKEN") or os.getenv("SNOWFLAKE_PAT") or "").strip() or None,
            auth_header="Authorization",
            token_type=os.getenv("SNOWFLAKE_CORTEX_TOKEN_TYPE", "PROGRAMMATIC_ACCESS_TOKEN").strip() or None,
            cortex_mode=cortex_mode,
            temperature=temperature,
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
            temperature=temperature,
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
            temperature=temperature,
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
            temperature=temperature,
            http_timeout_secs=http_timeout,
            ttft_timeout_secs=ttft_timeout,
        )

    if provider == "custom":
        model = os.getenv("CUSTOM_LLM_MODEL", "").strip()
        return LLMSettings(
            provider="custom",
            base_url=_normalize_custom_base_url(os.getenv("CUSTOM_LLM_BASE_URL", "")),
            model=model,
            sql_model=os.getenv("CUSTOM_LLM_SQL_MODEL", model).strip() or model,
            vision_model=os.getenv("CUSTOM_LLM_VISION_MODEL", model).strip() or model,
            api_key=os.getenv("CUSTOM_LLM_API_KEY", "").strip() or None,
            auth_header=os.getenv("CUSTOM_LLM_AUTH_HEADER", "Authorization").strip() or "Authorization",
            temperature=temperature,
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
        temperature=temperature,
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
        if self.settings.provider == "snowflake_cortex":
            headers["Accept"] = "application/json"
            if self.settings.token_type:
                headers["X-Snowflake-Authorization-Token-Type"] = self.settings.token_type
        return headers

    def _completions_url(self) -> str:
        if self.settings.provider == "azure":
            return f"{self.settings.base_url}/chat/completions?api-version={self.settings.api_version}"
        if self.settings.provider == "snowflake_cortex":
            return f"{self.settings.base_url}/api/v2/cortex/inference:complete"
        return f"{self.settings.base_url}/v1/chat/completions"

    def list_models(self) -> Dict[str, Any]:
        if self.settings.provider == "snowflake_cortex":
            env_models = [m.strip() for m in os.getenv("SNOWFLAKE_CORTEX_MODELS", "").split(",") if m.strip()]
            models = list(dict.fromkeys([self.settings.model] + (env_models or _DEFAULT_CORTEX_MODELS)))
            reachable = (
                bool(self.settings.base_url and os.getenv("SNOWFLAKE_USER", "").strip())
                if self.settings.cortex_mode == "sql"
                else bool(self.settings.base_url and self.settings.api_key)
            )
            return {
                "provider": "snowflake_cortex",
                "mode": self.settings.cortex_mode,
                "reachable": reachable,
                "default_model": self.settings.model,
                "models": models,
            }
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
            if self.settings.provider == "custom" and self.settings.base_url and self.settings.model:
                env_models = [m.strip() for m in os.getenv("CUSTOM_LLM_MODELS", "").split(",") if m.strip()]
                fallback_models = list(dict.fromkeys([self.settings.model] + env_models))
                return {
                    "provider": "custom",
                    "reachable": True,
                    "default_model": self.settings.model,
                    "models": fallback_models,
                }
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
        temperature: Optional[float] = None,
    ) -> Optional[str]:
        selected = (model or self.settings.model).strip()
        effective_temp = self.settings.temperature if temperature is None else float(temperature)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]
        if self.settings.provider == "snowflake_cortex" and self.settings.cortex_mode == "sql":
            try:
                return cortex_sql_complete(messages, selected, temperature=effective_temp, max_tokens=max_tokens)
            except Exception:
                return None

        payload: Dict[str, Any] = {
            "model": selected,
            "messages": messages,
            "temperature": effective_temp,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if should_disable_thinking(self.settings.provider, selected):
            payload["chat_template_kwargs"] = {"enable_thinking": False}
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
                raw_content = ((choices[0].get("message") or {}).get("content") or "").strip()
                return strip_think_tags(raw_content) or None
        except (error.URLError, TimeoutError, json.JSONDecodeError, OSError):
            return None
        return None

    def stream(
        self,
        prompt: str,
        system_prompt: str,
        model: Optional[str] = None,
        max_tokens: int = 400,
        temperature: Optional[float] = None,
    ) -> Generator[str, None, None]:
        selected = (model or self.settings.model).strip()
        effective_temp = self.settings.temperature if temperature is None else float(temperature)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]
        if self.settings.provider == "snowflake_cortex" and self.settings.cortex_mode == "sql":
            try:
                reply = cortex_sql_complete(messages, selected, temperature=effective_temp, max_tokens=max_tokens)
                if reply:
                    yield strip_think_tags(reply)
            except Exception:
                return
            return

        payload: Dict[str, Any] = {
            "model": selected,
            "messages": messages,
            "temperature": effective_temp,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if should_disable_thinking(self.settings.provider, selected):
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        req = request.Request(
            self._completions_url(),
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        started = time.perf_counter()
        first_token = False
        in_think = False
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
                    if not delta:
                        continue
                    if "</think>" in delta:
                        delta = delta.split("</think>", 1)[-1]
                        in_think = False
                    if "<think>" in delta or in_think:
                        in_think = True
                        continue
                    if delta:
                        first_token = True
                        yield delta
        except (error.URLError, TimeoutError, OSError):
            return
