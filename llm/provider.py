"""
llm/provider.py - Unified LLM Provider Adapter and Resilient Execution Engine.
Single source of truth for all LLM calls (Gemini, Groq, OpenAI, Anthropic, FakeLLM).
"""

import os
import re
import json
import time
import random
import logging
import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Union, Tuple
import httpx

logger = logging.getLogger("llm.provider")

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

    DEFAULT_MODELS = ["gemini-3.1-flash-lite-preview", "gemini-3-flash-preview"]

    def __init__(self, api_key: Optional[str] = None, models: Optional[List[str]] = None):
        super().__init__("Gemini")
        self.api_key = api_key or os.getenv("TEST_LLM_KEY") or os.getenv("GEMINI_API_KEY", "")
        env_models = os.getenv("GEMINI_MODELS", "")
        if models:
            self.models = models
        elif env_models:
            self.models = [m.strip() for m in env_models.split(",") if m.strip()]
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
                    f"Gemini {target_model} rate limited (HTTP 429): {resp.text[:120]}",
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

    DEFAULT_MODELS = ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b", "llama-3.1-8b-instant"]

    def __init__(self, api_key: Optional[str] = None, models: Optional[List[str]] = None):
        super().__init__("Groq")
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
            "Authorization": f"Bearer {key_to_use}",
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
                    f"Groq {target_model} rate limited (HTTP 429): {resp.text[:120]}",
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
    Prioritizes Gemini -> Groq by default, or follows LLM_PROVIDER_CHAIN.
    """
    if os.getenv("USE_FAKE_LLM", "0") in ("1", "true"):
        return [get_fake_llm()]

    chain_env = os.getenv("LLM_PROVIDER_CHAIN", "").strip().lower()
    configured_order = [p.strip() for p in chain_env.split(",") if p.strip()] if chain_env else ["gemini", "groq"]

    provider_map: Dict[str, BaseLLMProvider] = {
        "gemini": GeminiProvider(),
        "groq": GroqProvider(),
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
        all_providers = [p for p in all_providers if p.name.lower() == provider.lower()] or all_providers

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
            candidate_models = [model] if model else prov.get_models()
            is_fallback_provider = (p_idx > 0)
            if is_fallback_provider:
                fallback_used = True

            for m_candidate in candidate_models:
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

                        # If rate limited or server error, apply backoff and retry same provider once
                        if attempt < max_retries_per_provider and isinstance(err, (RateLimitedError, ProviderTimeoutError)):
                            retry_sec = getattr(err, "retry_after", 1.0)
                            jitter = random.uniform(0.1, 0.5)
                            sleep_dur = min(max(retry_sec, 0.5) + jitter, 2.5)
                            logger.info(f"Backing off for {sleep_dur:.2f}s before retry...")
                            await asyncio.sleep(sleep_dur)
                            continue
                        else:
                            fallback_reasons.append(err_reason)
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
