"""
PeculiarAI Test Harness: FakeLLM Provider
Mocks LLM API endpoints (Gemini, Groq, OpenAI) to return scripted tool calls,
poisoned cutoff dates (2024/2025), malformed arguments, or HTTP errors.
"""
import json
from typing import List, Dict, Any, Optional, Callable

class FakeLLM:
    """
    Scriptable mock LLM provider.
    """
    def __init__(self):
        self.scripted_turns: List[Dict[str, Any]] = []
        self.calls_recorded: List[Dict[str, Any]] = []

    def queue_text_response(self, text: str):
        """Enqueues a simple direct text reply."""
        self.scripted_turns.append({
            "type": "text",
            "content": text
        })

    def queue_tool_call(self, tool_name: str, arguments: Dict[str, Any], reply_after_tools: Optional[str] = None):
        """Enqueues a tool invocation turn followed optionally by a conversational response."""
        self.scripted_turns.append({
            "type": "tool_call",
            "name": tool_name,
            "arguments": arguments,
            "reply_after_tools": reply_after_tools or "Done!"
        })

    def queue_poisoned_reminder(self, title: str = "Movie", due_date: str = "2025-01-24", due_time: str = "16:30", duration: int = 30):
        """Specifically enqueues a reminder tool call with a poisoned pretraining cutoff date."""
        self.queue_tool_call("create_reminder", {
            "title": title,
            "due_date": due_date,
            "due_time": due_time,
            "duration_minutes": duration
        })

    def queue_malformed_tool_call(self, tool_name: str = "create_reminder", malformed_args: Optional[Dict[str, Any]] = None):
        """Enqueues a tool call with missing fields or corrupt types."""
        args = malformed_args or {"title": None, "duration_minutes": "thirty minutes"}
        self.queue_tool_call(tool_name, args)

    def queue_error(self, status_code: int = 429, error_message: str = "Rate limit exceeded"):
        """Enqueues an HTTP error response."""
        self.scripted_turns.append({
            "type": "error",
            "status_code": status_code,
            "message": error_message
        })

    async def mock_post(self, url, headers=None, json_data=None, **kwargs):
        """Mock method for httpx.AsyncClient.post."""
        self.calls_recorded.append({
            "url": str(url),
            "headers": headers,
            "payload": json_data
        })

        if not self.scripted_turns:
            # Default empty text response
            return FakeHTTPResponse(200, {
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": "Default test response."
                    }
                }],
                "usage": {"total_tokens": 150}
            })

        turn = self.scripted_turns.pop(0)

        if turn["type"] == "error":
            return FakeHTTPResponse(turn["status_code"], {"error": {"message": turn["message"]}})

        elif turn["type"] == "tool_call":
            return FakeHTTPResponse(200, {
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{
                            "id": "call_fake_12345",
                            "type": "function",
                            "function": {
                                "name": turn["name"],
                                "arguments": json.dumps(turn["arguments"])
                            }
                        }]
                    }
                }],
                "usage": {"total_tokens": 200}
            })

        else: # text
            return FakeHTTPResponse(200, {
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": turn["content"]
                    }
                }],
                "usage": {"total_tokens": 100}
            })


class FakeHTTPResponse:
    def __init__(self, status_code: int, data: Dict[str, Any]):
        self.status_code = status_code
        self._data = data
        self.text = json.dumps(data)

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            req = httpx.Request("POST", "https://api.mock.test")
            raise httpx.HTTPStatusError(f"HTTP {self.status_code} Error", request=req, response=self)

