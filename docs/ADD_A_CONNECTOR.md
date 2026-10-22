# How to Add a New Connector (in under 30 minutes)

PeculiarAI uses a **Universal Connector Contract** (`connectors/base.py`). Every connector (GitHub, Gmail, Google Calendar, Google Drive, Notion, Trello, Slack, etc.) implements the same interface.

When you add a connector following this guide, **all tests for every scenario (happy path, not connected, auth expired, fake success, active context, prompt injection, hallucination canary) are generated automatically**.

---

## Step 1: Create Your Connector Class in `connectors/<name>.py`

Create a new file, e.g. `connectors/trello.py`:

```python
from typing import Dict, Any, List, Literal
from connectors.base import Connector, ToolSpec, ToolResult, HealthResult, ErrorType

TRELLO_FIXTURES = {
    "boards": [{"id": "b1", "name": "Sprint 1", "cards": ["Task A", "Task B"]}]
}

class TrelloConnector(Connector):
    name = "trello"
    aliases = ["trello", "board", "cards", "kanban", "sprint"]
    display_name = "Trello"

    def auth_state(self, user_or_chat_id: int) -> Literal["connected", "not_connected", "expired", "partial"]:
        # Check DB / env for token
        return "connected"  # or "not_connected"

    def connect_instructions(self, user_or_chat_id: int) -> str:
        return "Trello connect karne ke liye apna API key aur token chat mein bhejein."

    def tools(self) -> List[ToolSpec]:
        return [
            ToolSpec(
                name="list_trello_boards",
                description="List boards and cards in user's Trello workspace.",
                parameters={"type": "object", "properties": {}},
                kind="read"
            )
        ]

    async def execute(self, user_or_chat_id: int, tool_name: str, args: Dict[str, Any]) -> ToolResult:
        if self.auth_state(user_or_chat_id) == "not_connected":
            return ToolResult(ok=False, error={"type": ErrorType.NOT_CONNECTED.value, "message": "Trello not connected"})
        
        if tool_name == "list_trello_boards":
            boards = [b["name"] for b in TRELLO_FIXTURES["boards"]]
            return ToolResult(ok=True, data={"boards": boards}, ids=["b1"])
        
        return ToolResult(ok=False, error={"type": ErrorType.INVALID_ARGS.value, "message": "Unknown tool"})

    async def health_check(self, user_or_chat_id: int) -> HealthResult:
        return HealthResult(ok=True, connected=True, message="Trello API reachable")

    def fixtures(self) -> Dict[str, Any]:
        return TRELLO_FIXTURES

    def mock(self) -> "Connector":
        return MockTrelloConnector()


class MockTrelloConnector(TrelloConnector):
    def __init__(self, forced_state="connected"):
        self._forced_state = forced_state

    def auth_state(self, user_or_chat_id: int):
        return self._forced_state

    async def execute(self, user_or_chat_id: int, tool_name: str, args: Dict[str, Any]):
        if self._forced_state == "not_connected":
            return ToolResult(ok=False, error={"type": ErrorType.NOT_CONNECTED.value, "message": "Trello not connected"})
        if self._forced_state == "expired":
            return ToolResult(ok=False, error={"type": ErrorType.AUTH_EXPIRED.value, "message": "Trello token expired"})
        return ToolResult(ok=True, data={"boards": ["Sprint 1"]}, ids=["b1"])

    async def health_check(self, user_or_chat_id: int):
        return HealthResult(ok=True, connected=(self._forced_state == "connected"), message="Mock Trello OK")
```

---

## Step 2: Register the Connector in `connectors/registry.py`

In `connectors/registry.py`:

```python
from connectors.trello import TrelloConnector

# Register it
register_connector(TrelloConnector())
```

---

## Step 3: Add Hinglish Typo Variants in `tests/connectors/fixtures/variants.json`

Add at least 10 sample user messages to `tests/connectors/fixtures/variants.json`:

```json
"trello": [
  "trello board check kr",
  "kanban cards dikhana",
  "trello me naya card add kr",
  "sprint board khol"
]
```

---

## Step 4: Run Tests

Run the test suite:

```bash
pytest tests/connectors/test_connector_matrix.py
```

All 9 matrix scenarios will automatically execute for your new connector and update `docs/CONNECTOR_COVERAGE.md`!
