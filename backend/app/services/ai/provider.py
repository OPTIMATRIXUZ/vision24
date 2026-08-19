from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Protocol

from app.config import settings


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict | None = None


@dataclass
class ToolCallReq:
    name: str
    args: dict
    id: str | None = None
    thought_signature: bytes | None = None


@dataclass
class TextPart:
    text: str


@dataclass
class ToolCallPart:
    call: ToolCallReq


@dataclass
class ToolResultPart:
    name: str
    result: dict
    id: str | None = None


@dataclass
class VideoPart:
    data: bytes
    mime_type: str = "video/mp4"


@dataclass
class ImagePart:
    data: bytes
    mime_type: str = "image/jpeg"


Part = TextPart | ToolCallPart | ToolResultPart | VideoPart | ImagePart


@dataclass
class Msg:
    role: str
    parts: list[Part]


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCallReq] = field(default_factory=list)


@dataclass
class TextDelta:

    text: str


@dataclass
class StreamDone:

    response: LLMResponse


StreamEvent = TextDelta | StreamDone


class LLMProvider(Protocol):
    def generate(
        self, *, system: str, messages: list[Msg], tools: list[ToolSpec] | None = None
    ) -> LLMResponse: ...

    def stream(
        self, *, system: str, messages: list[Msg], tools: list[ToolSpec] | None = None
    ) -> Iterator[StreamEvent]: ...


def stream_via_generate(provider: LLMProvider, **kwargs) -> Iterator[StreamEvent]:
    resp = provider.generate(**kwargs)
    if resp.text:
        yield TextDelta(resp.text)
    yield StreamDone(resp)


def _resolve(role: str) -> tuple[str, str]:
    if role == "live" and settings.live_ai_provider:
        return settings.live_ai_provider, settings.live_ai_model
    return settings.ai_provider, ""


def get_provider(role: str = "default") -> LLMProvider:
    name, model = _resolve(role)
    if name == "gemini":
        from app.services.ai.gemini_provider import GeminiProvider

        return GeminiProvider(model)
    if name == "openai":
        from app.services.ai.openai_provider import OpenAIProvider

        return OpenAIProvider(model)
    if name == "openrouter":
        from app.services.ai.openrouter_provider import OpenRouterProvider

        return OpenRouterProvider(model)
    raise RuntimeError(f"Unknown AI provider {name!r}")


def is_configured(role: str = "default") -> bool:
    name, _ = _resolve(role)
    if name == "gemini":
        return bool(settings.gemini_api_key)
    if name == "openai":
        return bool(settings.openai_api_key)
    if name == "openrouter":
        return bool(settings.openrouter_api_key)
    return False
