"""
llm/provider.py - Unified LLM Provider Adapter and Resilient Execution Engine.
Single source of truth for all LLM calls (Gemini, Groq, OpenAI, Anthropic, FakeLLM).
"""

import os
import re
import json
import time
import hashlib
import random
import logging
import asyncio
from dotenv import load_dotenv
from core.json_safe import safe_json_dumps

load_dotenv()
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Union, Tuple
import httpx

logger = logging.getLogger("llm.provider")

# In-memory per-credential and per-model circuit breaker cooldowns (e.g. 10 minutes)
_PROVIDER_COOLDOWNS: Dict[str, float] = {}
_MODEL_COOLDOWNS: Dict[Tuple[str, str], float] = {}


def reset_provider_cooldowns() -> None:
    """Clear provider and model circuit breaker cooldowns (used in test harness / test isolation)."""
    _PROVIDER_COOLDOWNS.clear()
    _MODEL_COOLDOWNS.clear()


def _credential_cooldown_key(prov: "BaseLLMProvider") -> str:
    """Build an isolated circuit-breaker key per provider credential."""
    raw_key = getattr(prov, "api_key", "") or ""
    if raw_key:
        key_fp = hashlib.sha256(str(raw_key).encode()).hexdigest()[:12]
        return f"{prov.name}:{key_fp}"
    return prov.name

# ---------------------------------------------------------------------------
# Typed Exceptions
# ---------------------------------------------------------------------------

class LLMError(Exception):
    """Base exception for all LLM adapter operations."""
    def __init__(
        self,
        message: str,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        status_code: Optional[int] = None,
        error_type: str = "llm_error"
    ):
        super().__init__(message)
        self.message = message
        self.provider = provider
        self.model = model
        self.status_code = status_code
        self.error_type = error_type


class ProviderTimeoutError(LLMError):
    """Provider timed out during HTTP request."""
    def __init__(self, message: str, provider: Optional[str] = None, model: Optional[str] = None):
        super().__init__(message, provider, model, status_code=408, error_type="provider_timeout")


class RateLimitedError(LLMError):
    """Provider returned HTTP 429 Too Many Requests."""
    def __init__(self, message: str, provider: Optional[str] = None, model: Optional[str] = None, retry_after: float = 1.5):
        super().__init__(message, provider, model, status_code=429, error_type="rate_limited")
        self.retry_after = retry_after


class ToolExecutionError(Exception):
    """Tool execution failed during function calling."""
    def __init__(self, message: str, tool_name: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.tool_name = tool_name
        self.error_type = "tool_failed"


class DatabaseUnavailableError(Exception):
    """Database connection failed or timed out."""
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message
        self.error_type = "db_unavailable"


class ValidationError(Exception):
    """Tool argument or parameter schema validation error."""
    def __init__(self, message: str, field_name: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.field_name = field_name
        self.error_type = "validation_error"


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass
class ToolCall:
    """Normalized tool call returned by an LLM."""
    id: str
    name: str
    arguments: Union[Dict[str, Any], str]

    def to_dict(self) -> Dict[str, Any]:
        raw_args = json.dumps(self.arguments) if isinstance(self.arguments, dict) else str(self.arguments)
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": raw_args
            }
        }


@dataclass
class UsageInfo:
    """Token usage metadata."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def to_dict(self) -> Dict[str, int]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens
        }


@dataclass
class LLMResponse:
    """
    Normalized response returned by any provider.
    Accessible both as attributes (resp.text) and dictionary keys (resp['text']).
    """
    text: Optional[str] = None
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    usage: Dict[str, int] = field(default_factory=lambda: {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})
    provider: str = ""
    model: str = ""
    error: Optional[str] = None
    fallback_used: bool = False
    fallback_reason: Optional[str] = None
    raw_response: Optional[Dict[str, Any]] = None

    @property
    def content(self) -> str:
        return self.text or ""

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def __setitem__(self, key: str, value: Any) -> None:
        setattr(self, key, value)

    def __contains__(self, key: str) -> bool:
        return hasattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "tool_calls": self.tool_calls,
            "usage": self.usage,
            "provider": self.provider,
            "model": self.model,
            "error": self.error,
            "fallback_used": self.fallback_used,
            "fallback_reason": self.fallback_reason,
        }


# ---------------------------------------------------------------------------
# Abstract Provider Interface
# ---------------------------------------------------------------------------

class BaseLLMProvider(ABC):
    """Abstract base class for all LLM providers."""

    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Union[str, Dict[str, Any]]] = None,
        temperature: float = 0.2,
        timeout: float = 30.0,
        model: Optional[str] = None,
        response_format: Optional[Dict[str, Any]] = None,
        client: Optional[httpx.AsyncClient] = None,
        **kwargs
    ) -> LLMResponse:
        """Execute a chat completion request and return normalized LLMResponse."""
        pass

    @abstractmethod
    def get_models(self) -> List[str]:
        """Return configured model list in priority order."""
        pass


# ---------------------------------------------------------------------------
# Helper: XML Tool Call Parser
# ---------------------------------------------------------------------------

def parse_xml_tool_calls(text: str) -> List[Dict[str, Any]]:
    """Parse XML-style function calls emitted in free text by fallback models."""
    if not text:
        return []
    calls = []
    pattern = re.compile(r"<function=([a-zA-Z0-9_-]+)>(.*?)</function>", re.DOTALL)
    for m in pattern.finditer(text):
        fname = m.group(1).strip()
        body = m.group(2).strip()
        try:
            parsed_args = json.loads(body)
        except Exception:
            parsed_args = {"raw": body}
        calls.append({
            "id": f"call_xml_{int(time.time() * 1000)}_{len(calls)}",
            "type": "function",
            "function": {
                "name": fname,
                "arguments": json.dumps(parsed_args) if isinstance(parsed_args, dict) else str(parsed_args)
            }
        })
    return calls


# ---------------------------------------------------------------------------
# Gemini Provider Implementation
# ---------------------------------------------------------------------------

class GeminiProvider(BaseLLMProvider):
    """
    Google Gemini Provider using Generative Language OpenAI-compatible endpoint.
    Primary tier with 1M token context and native function calling.
    """

    DEFAULT_MODELS = ["gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3-flash-preview"]

    def __init__(self, api_key: Optional[str] = None, models: Optional[List[str]] = None, name: str = "Gemini", backup_key: Optional[str] = None):
        super().__init__(name)
        self.api_key = api_key or os.getenv("TEST_LLM_KEY") or os.getenv("GEMINI_API_KEY", "")
        self.backup_key = backup_key or os.getenv("GEMINI_API_KEY_BACKUP", "")
        env_models = os.getenv("GEMINI_MODELS", "")
        if models:
            self.models = models
        elif env_models:
            parsed_models = [m.strip() for m in env_models.split(",") if m.strip()]
            # Preserve configured order while keeping default Gemini fallback models available
            self.models = parsed_models + [m for m in self.DEFAULT_MODELS if m not in parsed_models]
        else:
            self.models = list(self.DEFAULT_MODELS)
        self.endpoint = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"

    def get_models(self) -> List[str]:
        return self.models

    def is_configured(self) -> bool:
        return bool(self.api_key)

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Union[str, Dict[str, Any]]] = None,
        temperature: float = 0.2,
        timeout: float = 30.0,
        model: Optional[str] = None,
        response_format: Optional[Dict[str, Any]] = None,
        client: Optional[httpx.AsyncClient] = None,
        **kwargs
    ) -> LLMResponse:
        if not self.is_configured():
            raise LLMError("Gemini API key is not configured", provider=self.name, status_code=401)

        target_model = model or self.models[0]
        payload: Dict[str, Any] = {
            "model": target_model,
            "messages": messages,
            "temperature": temperature,
        }

        if tools:
            payload["tools"] = tools
            if tool_choice:
                payload["tool_choice"] = tool_choice

        if response_format:
            payload["response_format"] = response_format

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        should_close_client = False
        if client is None:
            client = httpx.AsyncClient(timeout=timeout)
            should_close_client = True

        try:
            resp = await client.post(self.endpoint, headers=headers, json=payload)
            if resp.status_code == 429:
                retry_after = 1.5
                try:
                    retry_after = float(resp.headers.get("retry-after", "1.5"))
                except Exception:
                    pass
                raise RateLimitedError(
                    f"Gemini {target_model} rate limited (HTTP 429): {resp.text[:500]}",
                    provider=self.name,
                    model=target_model,
                    retry_after=retry_after
                )
            if resp.status_code >= 500:
                raise LLMError(
                    f"Gemini {target_model} server error ({resp.status_code}): {resp.text[:120]}",
                    provider=self.name,
                    model=target_model,
                    status_code=resp.status_code
                )
            if resp.status_code != 200:
                raise LLMError(
                    f"Gemini {target_model} returned status {resp.status_code}: {resp.text[:120]}",
                    provider=self.name,
                    model=target_model,
                    status_code=resp.status_code
                )

            data = resp.json()
            choice = data["choices"][0]
            choice_msg = choice.get("message", {})
            text = choice_msg.get("content") or ""
            tool_calls = choice_msg.get("tool_calls") or []

            # Check for XML tool call fallback if content has them and no tool_calls emitted
            if not tool_calls and text:
                xml_calls = parse_xml_tool_calls(text)
                if xml_calls:
                    tool_calls = xml_calls

            usage_raw = data.get("usage", {})
            usage = {
                "prompt_tokens": usage_raw.get("prompt_tokens", 0),
                "completion_tokens": usage_raw.get("completion_tokens", 0),
                "total_tokens": usage_raw.get("total_tokens", 0)
            }

            return LLMResponse(
                text=text,
                tool_calls=tool_calls,
                usage=usage,
                provider=self.name,
                model=target_model,
                raw_response=data
            )

        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                f"Gemini request timed out after {timeout}s: {exc}",
                provider=self.name,
                model=target_model
            )
        except (RateLimitedError, ProviderTimeoutError, LLMError):
            raise
        except Exception as exc:
            raise LLMError(
                f"Gemini request error: {str(exc)}",
                provider=self.name,
                model=target_model
            )
        finally:
            if should_close_client:
                await client.aclose()


# ---------------------------------------------------------------------------
# Groq Provider Implementation
# ---------------------------------------------------------------------------

class GroqProvider(BaseLLMProvider):
    """
    Groq Cloud Provider using OpenAI-compatible endpoint.
    Ultra-low latency fallback tier with Llama / Qwen / Mistral architectures.
    """

    DEFAULT_MODELS = [
        "openai/gpt-oss-120b",
        "openai/gpt-oss-20b",
        "qwen/qwen3.8-27b",
    ]

    def __init__(self, api_key: Optional[str] = None, models: Optional[List[str]] = None, name: str = "Groq"):
        super().__init__(name)
        self.api_key = api_key or os.getenv("TEST_LLM_KEY") or os.getenv("GROQ_API_KEY", "")
        self.backup_key = os.getenv("GROQ_API_KEY_BACKUP", "")
        env_models = os.getenv("GROQ_MODELS", "")
        if models:
            self.models = models
        elif env_models:
            self.models = [m.strip() for m in env_models.split(",") if m.strip()]
        else:
            self.models = list(self.DEFAULT_MODELS)
        self.endpoint = "https://api.groq.com/openai/v1/chat/completions"

    def get_models(self) -> List[str]:
        return self.models

    def is_configured(self) -> bool:
        return bool(self.api_key)

    @staticmethod
    def _slim_tool_schemas(selected_tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Creates shallow copies of tool definitions with compact descriptions to fit strict TPM budgets."""
        slimmed = []
        for t in selected_tools:
            if not isinstance(t, dict) or "function" not in t:
                slimmed.append(t)
                continue
            fn = dict(t["function"])
            desc = str(fn.get("description") or "")
            if len(desc) > 120:
                fn["description"] = desc[:117] + "..."
            params = fn.get("parameters")
            if isinstance(params, dict) and isinstance(params.get("properties"), dict):
                new_props = {}
                for pk, pv in params["properties"].items():
                    if isinstance(pv, dict):
                        pv_copy = dict(pv)
                        p_desc = str(pv_copy.get("description") or "")
                        if len(p_desc) > 85:
                            pv_copy["description"] = p_desc[:82] + "..."
                        new_props[pk] = pv_copy
                    else:
                        new_props[pk] = pv
                new_params = dict(params)
                new_params["properties"] = new_props
                fn["parameters"] = new_params
            t_copy = dict(t)
            t_copy["function"] = fn
            slimmed.append(t_copy)
        return slimmed

    @classmethod
    def _build_groq_payload(
        cls,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        tool_choice: Optional[Union[str, Dict[str, Any]]],
        target_model: str,
        temperature: float,
        response_format: Optional[Dict[str, Any]],
        max_tools: int = 12,
        max_est_tokens: int = 6000,
        max_hist_turns: int = 4,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": target_model,
            "messages": [dict(m) for m in (messages or [])],
            "temperature": temperature,
        }

        selected_tools: List[Dict[str, Any]] = []
        if tools and len(tools) > max_tools:
            try:
                from agent.tools import select_semantic_tools_for_budget
                selected_tools = select_semantic_tools_for_budget(messages, tools, max_tools=max_tools)
            except Exception:
                selected_tools = list(tools[:max_tools])
        elif tools:
            selected_tools = list(tools)

        slim_tools = cls._slim_tool_schemas(selected_tools) if selected_tools else []
        if slim_tools:
            payload["tools"] = slim_tools
            if tool_choice:
                payload["tool_choice"] = tool_choice

        if response_format:
            payload["response_format"] = response_format

        def _est_tokens(p: Dict[str, Any]) -> float:
            try:
                raw_len = len(safe_json_dumps({"m": p["messages"], "t": p.get("tools", [])}, ensure_ascii=False))
            except Exception:
                raw_len = sum(len(str(m)) for m in p["messages"]) + len(str(p.get("tools", [])))
            return (raw_len / 3.1) + 300.0

        if _est_tokens(payload) <= max_est_tokens:
            return payload

        # 1. Trim older completed conversation turns while preserving system prompt & active turn/tool results
        msgs = payload["messages"]
        if len(msgs) > 2 and msgs[0].get("role") == "system":
            sys_msg = msgs[0]
            rest = msgs[1:]
            active_tail_start = max(0, len(rest) - 1)
            for idx in range(len(rest) - 1, -1, -1):
                if rest[idx].get("role") == "user":
                    active_tail_start = idx
                    break
            prior = rest[:active_tail_start]
            active_tail = rest[active_tail_start:]
            kept_prior = prior[-max_hist_turns:] if max_hist_turns > 0 else []
            compact_prior = []
            for pm in kept_prior:
                pm_c = dict(pm)
                if isinstance(pm_c.get("content"), str) and len(pm_c["content"]) > 180:
                    pm_c["content"] = pm_c["content"][:177] + "..."
                compact_prior.append(pm_c)
            payload["messages"] = [sys_msg] + compact_prior + active_tail

        # 2. Compact verbose instructions outside CONTEXT: inside a copy of system message while preserving rules & context
        if _est_tokens(payload) > max_est_tokens and payload["messages"] and payload["messages"][0].get("role") == "system":
            sys_copy = dict(payload["messages"][0])
            sys_str = str(sys_copy.get("content") or "")
            if slim_tools:
                sel_names = ", ".join(
                    sorted(t.get("function", {}).get("name", "") for t in slim_tools if t.get("function", {}).get("name"))
                )
                sys_str = re.sub(
                    r"Your capabilities are strictly limited to your registered tools:[^\n]*",
                    f"Your capabilities are strictly limited to your registered tools: {sel_names}.",
                    sys_str,
                )
            if "\nCONTEXT:\n" in sys_str:
                pre_ctx, ctx_tail = sys_str.split("\nCONTEXT:\n", 1)
                pre_ctx = re.sub(r"\s*\(e\.g\.[^)]{12,}\)", "", pre_ctx)
                # Preserve preference lock, current time, grounding, temporal, and security rules within ~5200 chars
                if len(pre_ctx) > 5200:
                    pre_lines = []
                    total_pre = 0
                    for ln in pre_ctx.splitlines():
                        ln_s = ln.rstrip()
                        if total_pre + len(ln_s) + 1 <= 5200 or any(
                            k in ln_s for k in ("PREFERENCE LOCK", "LOCKED ", "Current Time:", "Security Boundary", "<untrusted_content", "Grounding & Truth")
                        ):
                            pre_lines.append(ln_s)
                            total_pre += len(ln_s) + 1
                    pre_ctx = "\n".join(pre_lines)
                sys_str = f"{pre_ctx}\nCONTEXT:\n{ctx_tail}"
            # If still oversized, trim non-essential middle lines cleanly while keeping memory/schedule/user query
            max_sys_chars = max(4500, int((max_est_tokens - 2200) * 3.0))
            if len(sys_str) > max_sys_chars:
                if "\nCONTEXT:\n" in sys_str:
                    p_head, p_ctx = sys_str.split("\nCONTEXT:\n", 1)
                    head_budget = max(2200, max_sys_chars - len(p_ctx) - 32)
                    if len(p_head) > head_budget:
                        p_head = p_head[:head_budget].rsplit("\n", 1)[0]
                    sys_str = f"{p_head}\nCONTEXT:\n{p_ctx[:max(2000, max_sys_chars - len(p_head) - 16)]}"
                else:
                    sys_str = sys_str[:max_sys_chars]
            sys_copy["content"] = sys_str
            payload["messages"][0] = sys_copy

        return payload

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Union[str, Dict[str, Any]]] = None,
        temperature: float = 0.2,
        timeout: float = 30.0,
        model: Optional[str] = None,
        response_format: Optional[Dict[str, Any]] = None,
        client: Optional[httpx.AsyncClient] = None,
        use_backup_key: bool = False,
        **kwargs
    ) -> LLMResponse:
        key_to_use = (self.backup_key if use_backup_key and self.backup_key else self.api_key)
        if not key_to_use:
            raise LLMError("Groq API key is not configured", provider=self.name, status_code=401)

        target_model = model or self.models[0]
        payload = self._build_groq_payload(
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            target_model=target_model,
            temperature=temperature,
            response_format=response_format,
            max_tools=12,
            max_est_tokens=6000,
            max_hist_turns=4,
        )

        headers = {
            "Authorization": f"Bearer {key_to_use}",
            "Content-Type": "application/json"
        }

        should_close_client = False
        if client is None:
            client = httpx.AsyncClient(timeout=timeout)
            should_close_client = True

        try:
            resp = await client.post(self.endpoint, headers=headers, json=payload)
            if resp.status_code == 413 and (len(payload.get("tools", [])) > 8 or len(payload.get("messages", [])) > 2):
                logger.warning(f"Groq {target_model} hit 413; compacting payload to <=8 semantic tools and retrying once.")
                payload = self._build_groq_payload(
                    messages=messages,
                    tools=tools,
                    tool_choice=tool_choice,
                    target_model=target_model,
                    temperature=temperature,
                    response_format=response_format,
                    max_tools=8,
                    max_est_tokens=4600,
                    max_hist_turns=2,
                )
                resp = await client.post(self.endpoint, headers=headers, json=payload)

            if resp.status_code == 429:
                retry_after = 1.5
                try:
                    retry_after = float(resp.headers.get("retry-after", "1.5"))
                except Exception:
                    pass
                raise RateLimitedError(
                    f"Groq {target_model} rate limited (HTTP 429): {resp.text[:500]}",
                    provider=self.name,
                    model=target_model,
                    retry_after=retry_after
                )
            if resp.status_code >= 500:
                raise LLMError(
                    f"Groq {target_model} server error ({resp.status_code}): {resp.text[:120]}",
                    provider=self.name,
                    model=target_model,
                    status_code=resp.status_code
                )
            if resp.status_code != 200:
                raise LLMError(
                    f"Groq {target_model} returned status {resp.status_code}: {resp.text[:120]}",
                    provider=self.name,
                    model=target_model,
                    status_code=resp.status_code
                )

            data = resp.json()
            choice = data["choices"][0]
            choice_msg = choice.get("message", {})
            text = choice_msg.get("content") or ""
            tool_calls = choice_msg.get("tool_calls") or []

            if not tool_calls and text:
                xml_calls = parse_xml_tool_calls(text)
                if xml_calls:
                    tool_calls = xml_calls

            usage_raw = data.get("usage", {})
            usage = {
                "prompt_tokens": usage_raw.get("prompt_tokens", 0),
                "completion_tokens": usage_raw.get("completion_tokens", 0),
                "total_tokens": usage_raw.get("total_tokens", 0)
            }

            return LLMResponse(
                text=text,
                tool_calls=tool_calls,
                usage=usage,
                provider=self.name,
                model=target_model,
                raw_response=data
            )

        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                f"Groq request timed out after {timeout}s: {exc}",
                provider=self.name,
                model=target_model
            )
        except (RateLimitedError, ProviderTimeoutError, LLMError):
            raise
        except Exception as exc:
            raise LLMError(
                f"Groq request error: {str(exc)}",
                provider=self.name,
                model=target_model
            )
        finally:
            if should_close_client:
                await client.aclose()


# ---------------------------------------------------------------------------
# OpenAI Provider Stub & Integration Guide
# ---------------------------------------------------------------------------

class OpenAIProvider(BaseLLMProvider):
    """
    OpenAI Provider Stub.
    
    HOW TO ENABLE & CONFIGURE OPENAI:
    1. Set OPENAI_API_KEY in your .env or Render dashboard.
    2. Add 'openai' to LLM_PROVIDER_CHAIN (e.g. LLM_PROVIDER_CHAIN="openai,gemini,groq").
    3. Optionally customize OPENAI_MODELS (e.g. "gpt-4o,gpt-4o-mini").
    4. The implementation below uses standard OpenAI /v1/chat/completions endpoint.
    """

    DEFAULT_MODELS = ["gpt-4o", "gpt-4o-mini"]

    def __init__(self, api_key: Optional[str] = None, models: Optional[List[str]] = None):
        super().__init__("OpenAI")
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        env_models = os.getenv("OPENAI_MODELS", "")
        if models:
            self.models = models
        elif env_models:
            self.models = [m.strip() for m in env_models.split(",") if m.strip()]
        else:
            self.models = list(self.DEFAULT_MODELS)
        self.endpoint = "https://api.openai.com/v1/chat/completions"

    def get_models(self) -> List[str]:
        return self.models

    def is_configured(self) -> bool:
        return bool(self.api_key)

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Union[str, Dict[str, Any]]] = None,
        temperature: float = 0.2,
        timeout: float = 30.0,
        model: Optional[str] = None,
        response_format: Optional[Dict[str, Any]] = None,
        client: Optional[httpx.AsyncClient] = None,
        **kwargs
    ) -> LLMResponse:
        if not self.is_configured():
            raise LLMError("OpenAI API key is not configured. Set OPENAI_API_KEY in .env.", provider=self.name, status_code=401)

        target_model = model or self.models[0]
        payload: Dict[str, Any] = {
            "model": target_model,
            "messages": messages,
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = tools
            if tool_choice:
                payload["tool_choice"] = tool_choice
        if response_format:
            payload["response_format"] = response_format

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        should_close = False
        if client is None:
            client = httpx.AsyncClient(timeout=timeout)
            should_close = True

        try:
            resp = await client.post(self.endpoint, headers=headers, json=payload)
            if resp.status_code == 429:
                raise RateLimitedError(f"OpenAI 429: {resp.text[:100]}", provider=self.name, model=target_model)
            if resp.status_code != 200:
                raise LLMError(f"OpenAI error {resp.status_code}: {resp.text[:100]}", provider=self.name, model=target_model, status_code=resp.status_code)
            
            data = resp.json()
            choice = data["choices"][0]
            choice_msg = choice.get("message", {})
            return LLMResponse(
                text=choice_msg.get("content") or "",
                tool_calls=choice_msg.get("tool_calls") or [],
                usage=data.get("usage", {}),
                provider=self.name,
                model=target_model,
                raw_response=data
            )
        except httpx.TimeoutException:
            raise ProviderTimeoutError(f"OpenAI timeout after {timeout}s", provider=self.name, model=target_model)
        finally:
            if should_close:
                await client.aclose()


# ---------------------------------------------------------------------------
# Anthropic Provider Stub & Integration Guide
# ---------------------------------------------------------------------------

class AnthropicProvider(BaseLLMProvider):
    """
    Anthropic Claude Provider Stub.

    HOW TO ENABLE & CONFIGURE ANTHROPIC:
    1. Set ANTHROPIC_API_KEY in your .env or Render dashboard.
    2. Add 'anthropic' to LLM_PROVIDER_CHAIN (e.g. LLM_PROVIDER_CHAIN="anthropic,gemini,groq").
    3. Anthropic Messages API requires:
       - Header 'x-api-key: <key>'
       - Header 'anthropic-version: 2023-06-01'
       - System prompt passed as top-level 'system' parameter rather than inside 'messages'.
       - Tools defined using Anthropic tool format ({name, description, input_schema}).
    """

    DEFAULT_MODELS = ["claude-3-5-sonnet-20241022", "claude-3-5-haiku-20241022"]

    def __init__(self, api_key: Optional[str] = None, models: Optional[List[str]] = None):
        super().__init__("Anthropic")
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        env_models = os.getenv("ANTHROPIC_MODELS", "")
        if models:
            self.models = models
        elif env_models:
            self.models = [m.strip() for m in env_models.split(",") if m.strip()]
        else:
            self.models = list(self.DEFAULT_MODELS)
        self.endpoint = "https://api.anthropic.com/v1/messages"

    def get_models(self) -> List[str]:
        return self.models

    def is_configured(self) -> bool:
        return bool(self.api_key)

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Union[str, Dict[str, Any]]] = None,
        temperature: float = 0.2,
        timeout: float = 30.0,
        model: Optional[str] = None,
        response_format: Optional[Dict[str, Any]] = None,
        client: Optional[httpx.AsyncClient] = None,
        **kwargs
    ) -> LLMResponse:
        if not self.is_configured():
            raise LLMError("Anthropic API key is not configured. Set ANTHROPIC_API_KEY in .env.", provider=self.name, status_code=401)

        target_model = model or self.models[0]
        # Separate system messages from user/assistant messages for Claude Messages API
        system_text = ""
        claude_messages = []
        for m in messages:
            if m.get("role") == "system":
                system_text += (m.get("content") or "") + "\n"
            else:
                claude_messages.append(m)

        payload: Dict[str, Any] = {
            "model": target_model,
            "max_tokens": 4096,
            "messages": claude_messages,
            "temperature": temperature,
        }
        if system_text.strip():
            payload["system"] = system_text.strip()

        # Transform tools to Anthropic format
        if tools:
            anthropic_tools = []
            for t in tools:
                fn = t.get("function", {})
                anthropic_tools.append({
                    "name": fn.get("name"),
                    "description": fn.get("description", ""),
                    "input_schema": fn.get("parameters", {"type": "object", "properties": {}})
                })
            payload["tools"] = anthropic_tools

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json"
        }

        should_close = False
        if client is None:
            client = httpx.AsyncClient(timeout=timeout)
            should_close = True

        try:
            resp = await client.post(self.endpoint, headers=headers, json=payload)
            if resp.status_code == 429:
                raise RateLimitedError(f"Anthropic 429: {resp.text[:100]}", provider=self.name, model=target_model)
            if resp.status_code != 200:
                raise LLMError(f"Anthropic error {resp.status_code}: {resp.text[:100]}", provider=self.name, model=target_model, status_code=resp.status_code)
            
            data = resp.json()
            out_text = ""
            tool_calls = []
            for block in data.get("content", []):
                if block.get("type") == "text":
                    out_text += block.get("text", "")
                elif block.get("type") == "tool_use":
                    tool_calls.append({
                        "id": block.get("id"),
                        "type": "function",
                        "function": {
                            "name": block.get("name"),
                            "arguments": json.dumps(block.get("input", {}))
                        }
                    })

            return LLMResponse(
                text=out_text,
                tool_calls=tool_calls,
                usage={
                    "prompt_tokens": data.get("usage", {}).get("input_tokens", 0),
                    "completion_tokens": data.get("usage", {}).get("output_tokens", 0),
                    "total_tokens": data.get("usage", {}).get("input_tokens", 0) + data.get("usage", {}).get("output_tokens", 0)
                },
                provider=self.name,
                model=target_model,
                raw_response=data
            )
        except httpx.TimeoutException:
            raise ProviderTimeoutError(f"Anthropic timeout after {timeout}s", provider=self.name, model=target_model)
        finally:
            if should_close:
                await client.aclose()


# ---------------------------------------------------------------------------
# FakeLLM Provider (Deterministic Test Harness)
# ---------------------------------------------------------------------------

class FakeLLMProvider(BaseLLMProvider):
    """
    Deterministic scripted LLM Provider for unit and hermetic testing.
    Can script exact tool calls, poisoned dates, malformed JSON, and exceptions.
    """

    def __init__(self):
        super().__init__("FakeLLM")
        self.scripted_queue: List[Union[LLMResponse, Exception]] = []
        self.history: List[Dict[str, Any]] = []

    def get_models(self) -> List[str]:
        return ["fake-model-v1"]

    def queue_response(self, text: Optional[str] = None, tool_calls: Optional[List[Dict[str, Any]]] = None) -> None:
        self.scripted_queue.append(LLMResponse(
            text=text,
            tool_calls=tool_calls or [],
            usage={"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            provider=self.name,
            model="fake-model-v1"
        ))

    def queue_error(self, exc: Exception) -> None:
        self.scripted_queue.append(exc)

    def queue_poisoned_tool_call(self, tool_name: str, arguments: Dict[str, Any]) -> None:
        self.queue_response(
            text="",
            tool_calls=[{
                "id": f"call_fake_{int(time.time() * 1000)}",
                "type": "function",
                "function": {
                    "name": tool_name,
                    "arguments": json.dumps(arguments)
                }
            }]
        )

    def clear(self) -> None:
        self.scripted_queue.clear()
        self.history.clear()

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Union[str, Dict[str, Any]]] = None,
        temperature: float = 0.2,
        timeout: float = 30.0,
        model: Optional[str] = None,
        response_format: Optional[Dict[str, Any]] = None,
        client: Optional[httpx.AsyncClient] = None,
        **kwargs
    ) -> LLMResponse:
        self.history.append({
            "messages": messages,
            "tools": tools,
            "tool_choice": tool_choice,
            "model": model,
            "temperature": temperature
        })
        if not self.scripted_queue:
            if response_format and response_format.get("type") == "json_object":
                return LLMResponse(
                    text=json.dumps({
                        "claims_action_done": False,
                        "which_action": None,
                        "claims_external_fact": False,
                        "sourced_by_tool": True,
                        "is_grounded": True,
                        "final_reply": "FakeLLM default scripted response.",
                        "reason": "Default hermetic test response"
                    }),
                    tool_calls=[],
                    provider=self.name,
                    model="fake-model-v1"
                )
            return LLMResponse(
                text="FakeLLM default scripted response.",
                tool_calls=[],
                provider=self.name,
                model="fake-model-v1"
            )
        item = self.scripted_queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


# ---------------------------------------------------------------------------
# Global Registry & Resilient Orchestration Engine
# ---------------------------------------------------------------------------

_FAKE_PROVIDER_INSTANCE: Optional[FakeLLMProvider] = None

def get_fake_llm() -> FakeLLMProvider:
    global _FAKE_PROVIDER_INSTANCE
    if _FAKE_PROVIDER_INSTANCE is None:
        _FAKE_PROVIDER_INSTANCE = FakeLLMProvider()
    return _FAKE_PROVIDER_INSTANCE


def reset_fake_llm() -> None:
    global _FAKE_PROVIDER_INSTANCE
    if _FAKE_PROVIDER_INSTANCE is not None:
        _FAKE_PROVIDER_INSTANCE.clear()
        _FAKE_PROVIDER_INSTANCE = None


def get_available_providers() -> List[BaseLLMProvider]:
    """
    Build ordered provider chain based on environment configuration.
    Prioritizes Gemini -> GeminiBackup -> Groq -> GroqBackup by default, or follows LLM_PROVIDER_CHAIN.
    """
    if os.getenv("USE_FAKE_LLM", "0") in ("1", "true"):
        return [get_fake_llm()]

    chain_env = os.getenv("LLM_PROVIDER_CHAIN", "").strip().lower()
    gemini_primary_key = (os.getenv("TEST_LLM_KEY") or os.getenv("GEMINI_API_KEY", "")).strip()
    gemini_backup_key = os.getenv("GEMINI_API_KEY_BACKUP", "").strip()
    groq_primary_key = (os.getenv("TEST_LLM_KEY") or os.getenv("GROQ_API_KEY", "")).strip()
    groq_backup_key = os.getenv("GROQ_API_KEY_BACKUP", "").strip()

    default_order = ["gemini"]
    if gemini_backup_key and gemini_backup_key != gemini_primary_key:
        default_order.append("gemini_backup")
    default_order.append("groq")
    if groq_backup_key and groq_backup_key != groq_primary_key:
        default_order.append("groq_backup")

    configured_order = [p.strip() for p in chain_env.split(",") if p.strip()] if chain_env else default_order

    gemini_backup_obj = GeminiProvider(api_key=gemini_backup_key, name="GeminiBackup") if gemini_backup_key else None
    groq_backup_obj = GroqProvider(api_key=groq_backup_key, name="GroqBackup") if groq_backup_key else None

    provider_map: Dict[str, BaseLLMProvider] = {
        "gemini": GeminiProvider(),
        "gemini_backup": gemini_backup_obj,
        "groq": GroqProvider(),
        "groq_backup": groq_backup_obj,
        "openai": OpenAIProvider(),
        "anthropic": AnthropicProvider(),
        "fake": get_fake_llm()
    }

    providers = []
    for p_name in configured_order:
        p_obj = provider_map.get(p_name)
        if p_obj and (p_obj.name == "FakeLLM" or getattr(p_obj, "is_configured", lambda: True)()):
            providers.append(p_obj)

    # Always ensure at least Groq or Gemini if keys are present
    if not providers:
        gemini = GeminiProvider()
        if gemini.is_configured():
            providers.append(gemini)
        groq = GroqProvider()
        if groq.is_configured():
            providers.append(groq)

    return providers


async def chat(
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    tool_choice: Optional[Union[str, Dict[str, Any]]] = None,
    temperature: float = 0.2,
    timeout: float = 30.0,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    response_format: Optional[Dict[str, Any]] = None,
    max_retries_per_provider: int = 1,
    client: Optional[httpx.AsyncClient] = None,
    require_tool_on_fallback: bool = False,
    tier: str = "primary",
    **kwargs
) -> LLMResponse:
    """
    ONE centralized entry point for all LLM calls.
    
    Implements:
    1. Provider & model chain resolution from config.
    2. Retry same provider once with exponential backoff + jitter on 429/5xx/timeout.
    3. Seamless failover to fallback providers when retries fail.
    4. Structured tracking of fallback_used, fallback_reason, latency, and tokens.
    5. Automatic tool_choice='required' retry if fallback model drops tool call when required.
    """
    all_providers = get_available_providers()
    if not all_providers:
        raise LLMError("No LLM provider is configured. Please set GEMINI_API_KEY or GROQ_API_KEY in .env.", status_code=503)

    if provider:
        matched = [p for p in all_providers if p.name.lower() == provider.lower()]
        others = [p for p in all_providers if p.name.lower() != provider.lower()]
        all_providers = matched + others if matched else all_providers

    last_error: Optional[Exception] = None
    fallback_used = False
    fallback_reasons: List[str] = []
    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_tokens = 0

    should_close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=timeout)
        should_close_client = True

    try:
        primary_provider_name = all_providers[0].name

        for p_idx, prov in enumerate(all_providers):
            cred_key = _credential_cooldown_key(prov)
            # Check per-credential circuit breaker cooldown for quota-exhausted keys
            cooldown_until = _PROVIDER_COOLDOWNS.get(cred_key, 0)
            if cooldown_until > time.time() and len(all_providers) > 1:
                logger.info(f"{prov.name} is in quota cooldown ({int(cooldown_until - time.time())}s left). Skipping to next provider.")
                fallback_used = True
                fallback_reasons.append(f"{prov.name} quota cooldown")
                continue

            # Staging Mode provider failover simulation
            staging_mode = os.getenv("STAGING_MODE", "").strip() == "1"
            force_fail = os.getenv("FORCE_PROVIDER_FAIL", "").strip().lower()
            if staging_mode and force_fail and (force_fail in prov.name.lower() or force_fail == "all"):
                logger.warning(f"FORCE_PROVIDER_FAIL active: Simulating failure for {prov.name}")
                fallback_reasons.append(f"FORCE_PROVIDER_FAIL: {prov.name} simulated failure")
                last_error = RateLimitedError(f"Simulated failure for {prov.name}", provider=prov.name)
                continue

            if model and model in prov.get_models():
                candidate_models = [model]
            else:
                candidate_models = list(prov.get_models())
                if tier == "fast" and len(candidate_models) > 1:
                    if isinstance(prov, GeminiProvider):
                        fast_first = [m for m in candidate_models if "lite" in m.lower()]
                        mid = [m for m in candidate_models if "preview" in m.lower() and m not in fast_first]
                        rest = [m for m in candidate_models if m not in fast_first and m not in mid]
                        candidate_models = fast_first + mid + rest
                    elif isinstance(prov, GroqProvider):
                        fast_first = [m for m in candidate_models if "20b" in m.lower()]
                        rest = [m for m in candidate_models if m not in fast_first]
                        candidate_models = fast_first + rest
            is_fallback_provider = (p_idx > 0)
            if is_fallback_provider:
                fallback_used = True

            skip_current_provider = False
            quota_exhausted_models = 0
            for m_candidate in candidate_models:
                if skip_current_provider:
                    break
                m_cooldown_until = _MODEL_COOLDOWNS.get((cred_key, m_candidate), 0)
                if m_cooldown_until > time.time() and (len(candidate_models) > 1 or len(all_providers) > 1):
                    quota_exhausted_models += 1
                    fallback_used = True
                    fallback_reasons.append(f"{prov.name} {m_candidate} model quota cooldown")
                    continue
                # Per-provider retry attempt with backoff
                for attempt in range(max_retries_per_provider + 1):
                    try:
                        logger.info(f"LLM call -> Provider={prov.name}, Model={m_candidate}, Attempt={attempt + 1}")
                        resp = await prov.chat(
                            messages=messages,
                            tools=tools,
                            tool_choice=tool_choice,
                            temperature=temperature,
                            timeout=timeout,
                            model=m_candidate,
                            response_format=response_format,
                            client=client,
                            **kwargs
                        )

                        # Enforce tool requirement if verifier says one was required and model returned no tools
                        if require_tool_on_fallback and tools and not resp.tool_calls and tool_choice != "none":
                            logger.info(f"{prov.name} returned no tool call when required. Retrying once with tool_choice='required'...")
                            try:
                                resp = await prov.chat(
                                    messages=messages,
                                    tools=tools,
                                    tool_choice="required",
                                    temperature=temperature,
                                    timeout=timeout,
                                    model=m_candidate,
                                    response_format=response_format,
                                    client=client,
                                    **kwargs
                                )
                            except Exception as forced_err:
                                logger.warning(f"Forced tool call retry failed: {forced_err}")

                        # Successful completion
                        resp.fallback_used = fallback_used
                        if fallback_used and fallback_reasons:
                            resp.fallback_reason = " | ".join(fallback_reasons)

                        # Accumulate token metrics
                        u = resp.usage
                        resp.usage = {
                            "prompt_tokens": total_prompt_tokens + u.get("prompt_tokens", 0),
                            "completion_tokens": total_completion_tokens + u.get("completion_tokens", 0),
                            "total_tokens": total_tokens + u.get("total_tokens", 0)
                        }
                        return resp

                    except (RateLimitedError, ProviderTimeoutError, LLMError) as err:
                        last_error = err
                        err_reason = f"{prov.name} {m_candidate} (Attempt {attempt+1}): {str(err)}"
                        logger.warning(err_reason)
                        fallback_reasons.append(err_reason)

                        status_code = getattr(err, "status_code", None)
                        err_str = str(err).lower()

                        # Instant fast-failover on invalid API key (401) scoped strictly to this credential
                        if status_code == 401 or "invalid_api_key" in err_str:
                            logger.warning(f"{prov.name} API key invalid (401): fast-failing credential immediately.")
                            _PROVIDER_COOLDOWNS[cred_key] = time.time() + 3600.0
                            skip_current_provider = True
                            break

                        # Instant fast-failover on 5xx server errors (e.g. 503 high demand spike / 502 / 504)
                        if status_code and status_code in (500, 502, 503, 504):
                            logger.warning(f"{prov.name} server error {status_code}: fast-failing provider immediately to next provider.")
                            skip_current_provider = True
                            break

                        # Per-model / per-credential quota cooldown (never poison separate backup credentials or 10m-blackout transient RPM!)
                        is_quota_msg = (status_code == 429 or isinstance(err, RateLimitedError)) and any(
                            q in err_str for q in ("insufficient_quota", "check your plan and billing details", "exceeded your current quota")
                        )
                        if is_quota_msg:
                            retry_m = re.search(r"retry\s+in\s+(\d+(?:\.\d+)?)\s*s", err_str)
                            parsed_retry = float(retry_m.group(1)) if retry_m else getattr(err, "retry_after", 0.0)
                            is_daily_or_billing = any(
                                d in err_str for d in ("perday", "per_day", "requests per day", "limit: 0", "insufficient_quota", "billing")
                            )
                            is_transient_rpm = any(
                                r in err_str for r in ("perminute", "per_minute", "rpm", "tpm")
                            ) or (0 < parsed_retry <= 90.0 and not is_daily_or_billing)

                            if is_transient_rpm:
                                cool_secs = min(max(parsed_retry, 20.0), 65.0)
                                logger.warning(f"{prov.name} model {m_candidate} transient RPM/TPM 429: cooling model for {cool_secs:.0f}s.")
                                _MODEL_COOLDOWNS[(cred_key, m_candidate)] = time.time() + cool_secs
                            else:
                                logger.warning(f"{prov.name} model {m_candidate} daily/billing quota exhausted (429): cooling down model/credential and trying next candidate.")
                                _MODEL_COOLDOWNS[(cred_key, m_candidate)] = time.time() + 300.0
                                quota_exhausted_models += 1
                                if quota_exhausted_models >= len(candidate_models):
                                    _PROVIDER_COOLDOWNS[cred_key] = time.time() + 180.0
                            break

                        # Instant fast-failover to next candidate model on TPM limit or 400 tool/request error
                        if (status_code == 429 or isinstance(err, RateLimitedError)) and "tpm" in err_str:
                            logger.warning(f"{prov.name} model {m_candidate} TPM rate limited (429): fast-failing to next candidate model.")
                            break

                        if status_code and status_code in (400, 404, 413):
                            logger.warning(f"{prov.name} model {m_candidate} returned {status_code}. Fast-skipping to next candidate model.")
                            break

                        # If rate limited or server error, apply backoff and retry same provider once
                        if attempt < max_retries_per_provider and isinstance(err, (RateLimitedError, ProviderTimeoutError)):
                            retry_sec = getattr(err, "retry_after", 1.0)
                            jitter = random.uniform(0.1, 0.5)
                            sleep_dur = min(max(retry_sec, 0.5) + jitter, 2.5)
                            logger.info(f"Backing off for {sleep_dur:.2f}s before retry...")
                            await asyncio.sleep(sleep_dur)
                            continue
                        else:
                            break  # Fall back to next model / provider

                    except Exception as unk_err:
                        last_error = unk_err
                        err_reason = f"{prov.name} {m_candidate} unexpected error: {repr(unk_err)}"
                        logger.error(err_reason)
                        fallback_reasons.append(err_reason)
                        break

        # If every candidate provider and model failed
        logger.error(f"All LLM candidates exhausted. Last error: {last_error}")
        if isinstance(last_error, (RateLimitedError, ProviderTimeoutError, LLMError)):
            raise last_error
        raise LLMError(f"All LLM candidate providers failed. Reasons: {' | '.join(fallback_reasons)}", status_code=503)

    finally:
        if should_close_client:
            await client.aclose()


def chat_sync(*args, **kwargs) -> LLMResponse:
    """Synchronous helper wrapper for chat()."""
    return asyncio.run(chat(*args, **kwargs))
