import logging
from collections.abc import Iterator

from openai import OpenAI

from app.config import settings
from app.services.ai.openai_provider import (
    _parse_response,
    _to_messages,
    _to_tool,
    consume_stream,
)
from app.services.ai.provider import LLMResponse, Msg, StreamEvent, ToolSpec

log = logging.getLogger(__name__)

_client: OpenAI | None = None


def client() -> OpenAI:
    global _client
    if _client is None:
        if not settings.openrouter_api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not set in .env")
        _client = OpenAI(
            api_key=settings.openrouter_api_key,
            base_url=settings.openrouter_base_url,
            default_headers={"X-Title": "Vision24"},
        )
    return _client


def _extra_body() -> dict:
    if settings.openrouter_reasoning:
        return {}
    return {"reasoning": {"enabled": False}}


class OpenRouterProvider:
    def __init__(self, model: str = ""):
        self.model = model or settings.openrouter_model

    def _kwargs(self, system: str, messages: list[Msg], tools: list[ToolSpec] | None) -> dict:
        kwargs: dict = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *_to_messages(messages)],
            "temperature": 0.2,
            "extra_body": _extra_body(),
        }
        if tools:
            kwargs["tools"] = [_to_tool(t) for t in tools]
        return kwargs

    def generate(
        self, *, system: str, messages: list[Msg], tools: list[ToolSpec] | None = None
    ) -> LLMResponse:
        resp = client().chat.completions.create(**self._kwargs(system, messages, tools))
        log.info(
            "OpenRouter call: model=%s, %d msgs, tools=%s, usage=%s",
            self.model,
            len(messages),
            bool(tools),
            resp.usage,
        )
        return _parse_response(resp)

    def stream(
        self, *, system: str, messages: list[Msg], tools: list[ToolSpec] | None = None
    ) -> Iterator[StreamEvent]:
        resp = client().chat.completions.create(
            stream=True,
            stream_options={"include_usage": True},
            **self._kwargs(system, messages, tools),
        )
        log.info(
            "OpenRouter stream: model=%s, %d msgs, tools=%s",
            self.model,
            len(messages),
            bool(tools),
        )
        yield from consume_stream(resp)
