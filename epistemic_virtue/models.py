"""Model access behind one small interface.

Real models go through Inspect's provider layer (``provider/model-id``), so any
provider Inspect supports works and credentials stay in environment variables.
``demo/actor`` and ``demo/guardian`` are offline stand-ins (see ``demo.py``).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
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


@dataclass
class Usage:
    """Calls made to a model and, where the provider reports them, tokens used."""

    calls: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None

    def to_dict(self) -> dict[str, int | None]:
        return asdict(self)


class Metered:
    """Wrap a client to count its calls and tokens for a run record.

    Token counts stay ``None`` for clients that do not report usage, such as
    the offline demo models.
    """

    def __init__(self, client: ModelClient) -> None:
        self._client = client
        self.name = client.name
        self.usage = Usage()

    async def complete(self, system: str, messages: list[Message]) -> str:
        text = await self._client.complete(system, messages)
        self.usage.calls += 1
        reported = getattr(self._client, "last_usage", None)
        if reported:
            self.usage.input_tokens = (self.usage.input_tokens or 0) + reported[0]
            self.usage.output_tokens = (self.usage.output_tokens or 0) + reported[1]
        return text


class InspectModelClient:
    """Adapter over an Inspect model, e.g. ``anthropic/<model-id>``."""

    def __init__(self, model: str | None = None) -> None:
        # Imported lazily so the demo path and unit tests stay light.
        from inspect_ai.model import get_model

        self._model = get_model(model)
        self.name = model or str(self._model)
        self.last_usage: tuple[int, int] | None = None

    async def complete(self, system: str, messages: list[Message]) -> str:
        from inspect_ai.model import ChatMessageAssistant, ChatMessageSystem, ChatMessageUser

        chat = [ChatMessageSystem(content=system)]
        for message in messages:
            cls = ChatMessageUser if message.role == "user" else ChatMessageAssistant
            chat.append(cls(content=message.content))
        output = await self._model.generate(chat)
        usage = output.usage
        self.last_usage = (usage.input_tokens, usage.output_tokens) if usage else None
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
