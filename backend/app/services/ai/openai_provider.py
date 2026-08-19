import base64
import json
import logging
from collections.abc import Iterator

from openai import OpenAI

from app.config import settings
from app.services.ai.provider import (
    ImagePart,
    LLMResponse,
    Msg,
    StreamDone,
    StreamEvent,
    TextDelta,
    TextPart,
    ToolCallPart,
    ToolCallReq,
    ToolResultPart,
    ToolSpec,
)

log = logging.getLogger(__name__)

_client: OpenAI | None = None


def client() -> OpenAI:
    global _client
    if _client is None:
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not set in .env")
        _client = OpenAI(api_key=settings.openai_api_key)
    return _client


class OpenAIProvider:
    def __init__(self, model: str = ""):
        self.model = model or settings.openai_model

    def generate(
        self, *, system: str, messages: list[Msg], tools: list[ToolSpec] | None = None
    ) -> LLMResponse:
        kwargs: dict = {}
        if tools:
            kwargs["tools"] = [_to_tool(t) for t in tools]
        resp = client().chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system}, *_to_messages(messages)],
            temperature=0.2,
            **kwargs,
        )
        log.info(
            "OpenAI call: %d msgs, tools=%s, usage=%s",
            len(messages),
            bool(tools),
            resp.usage,
        )
        return _parse_response(resp)

    def stream(
        self, *, system: str, messages: list[Msg], tools: list[ToolSpec] | None = None
    ) -> Iterator[StreamEvent]:
        kwargs: dict = {}
        if tools:
            kwargs["tools"] = [_to_tool(t) for t in tools]
        resp = client().chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system}, *_to_messages(messages)],
            temperature=0.2,
            stream=True,
            stream_options={"include_usage": True},
            **kwargs,
        )
        log.info("OpenAI stream: %d msgs, tools=%s", len(messages), bool(tools))
        yield from consume_stream(resp)


def _to_tool(spec: ToolSpec) -> dict:
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.parameters or {"type": "object", "properties": {}},
        },
    }


def _to_messages(messages: list[Msg]) -> list[dict]:
    out: list[dict] = []
    for msg in messages:
        if msg.role == "tool":
            for p in msg.parts:
                if not isinstance(p, ToolResultPart):
                    raise TypeError(f"Unexpected part in tool message: {type(p)!r}")
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": p.id or p.name,
                        "content": json.dumps(p.result, ensure_ascii=False, default=str),
                    }
                )
            continue

        texts = [p.text for p in msg.parts if isinstance(p, TextPart)]
        calls = [p.call for p in msg.parts if isinstance(p, ToolCallPart)]
        images = [p for p in msg.parts if isinstance(p, ImagePart)]
        if any(not isinstance(p, (TextPart, ToolCallPart, ImagePart)) for p in msg.parts):
            raise TypeError("OpenAI provider supports text, tool-call, and image parts only")

        if msg.role == "model":
            entry: dict = {"role": "assistant", "content": "\n".join(texts) or None}
            if calls:
                entry["tool_calls"] = [
                    {
                        "id": c.id or c.name,
                        "type": "function",
                        "function": {
                            "name": c.name,
                            "arguments": json.dumps(c.args, ensure_ascii=False),
                        },
                    }
                    for c in calls
                ]
            out.append(entry)
        elif images:
            content: list[dict] = []
            text = "\n".join(texts)
            if text:
                content.append({"type": "text", "text": text})
            for img in images:
                b64 = base64.b64encode(img.data).decode()
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{img.mime_type};base64,{b64}",
                            "detail": "low",
                        },
                    }
                )
            out.append({"role": "user", "content": content})
        else:
            out.append({"role": "user", "content": "\n".join(texts)})
    return out


def _parse_response(resp) -> LLMResponse:
    message = resp.choices[0].message
    calls = [
        ToolCallReq(
            name=tc.function.name,
            args=json.loads(tc.function.arguments or "{}"),
            id=tc.id,
        )
        for tc in (message.tool_calls or [])
    ]
    return LLMResponse(text=(message.content or "").strip(), tool_calls=calls)


def consume_stream(resp) -> Iterator[StreamEvent]:
    text: list[str] = []
    partial: dict[int, dict] = {}
    for chunk in resp:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta is None:
            continue
        if delta.content:
            text.append(delta.content)
            yield TextDelta(delta.content)
        for tc in delta.tool_calls or []:
            acc = partial.setdefault(tc.index, {"id": None, "name": "", "args": ""})
            if tc.id:
                acc["id"] = tc.id
            if tc.function and tc.function.name:
                acc["name"] = tc.function.name
            if tc.function and tc.function.arguments:
                acc["args"] += tc.function.arguments

    calls: list[ToolCallReq] = []
    for _, acc in sorted(partial.items()):
        if not acc["name"]:
            continue
        try:
            args = json.loads(acc["args"] or "{}")
        except json.JSONDecodeError:
            log.warning("stream: bad tool args for %s: %r", acc["name"], acc["args"])
            args = {}
        calls.append(ToolCallReq(name=acc["name"], args=args, id=acc["id"] or acc["name"]))
    yield StreamDone(LLMResponse(text="".join(text).strip(), tool_calls=calls))
