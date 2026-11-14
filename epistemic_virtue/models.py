"""Model access behind one small interface.

Real models go through Inspect's provider layer (``provider/model-id``), so any
provider Inspect supports works and credentials stay in environment variables.
``demo/actor`` and ``demo/guardian`` are offline stand-ins (see ``demo.py``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Protocol

DEMO_ACTOR = "demo/actor"
DEMO_GUARDIAN = "demo/guardian"
MODEL_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/\-]{0,199}$")


@dataclass(frozen=True)
class Message:
    role: Literal["user", "assistant"]
    content: str


class ModelClient(Protocol):
    """Anything that can turn a system prompt and a conversation into text."""

    name: str

    async def complete(self, system: str, messages: list[Message]) -> str: ...


class InspectModelClient:
    """Adapter over an Inspect model, e.g. ``anthropic/<model-id>``."""

    def __init__(self, model: str | None = None) -> None:
        # Imported lazily so the demo path and unit tests stay light.
        from inspect_ai.model import get_model

        self._model = get_model(model)
        self.name = model or str(self._model)

    async def complete(self, system: str, messages: list[Message]) -> str:
        from inspect_ai.model import ChatMessageAssistant, ChatMessageSystem, ChatMessageUser

        chat = [ChatMessageSystem(content=system)]
        for message in messages:
            cls = ChatMessageUser if message.role == "user" else ChatMessageAssistant
            chat.append(cls(content=message.content))
        output = await self._model.generate(chat)
        return output.completion


def get_client(model_id: str) -> ModelClient:
    """Resolve a model id to a client. Raises ``ValueError`` on a malformed id."""
    if not MODEL_ID_PATTERN.match(model_id):
        raise ValueError(f"Invalid model id: {model_id!r}")
    if model_id.startswith("demo/"):
        from epistemic_virtue.demo import DemoActor, DemoGuardian

        clients = {DEMO_ACTOR: DemoActor, DEMO_GUARDIAN: DemoGuardian}
        if model_id not in clients:
            raise ValueError(
                f"Unknown demo model {model_id!r}; use {DEMO_ACTOR} or {DEMO_GUARDIAN}"
            )
        return clients[model_id]()
    return InspectModelClient(model_id)
