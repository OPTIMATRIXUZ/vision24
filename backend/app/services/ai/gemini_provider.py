import logging
from collections.abc import Iterator

from google import genai
from google.genai import types

from app.config import settings
from app.services.ai.provider import (
    ImagePart,
    LLMResponse,
    Msg,
    StreamEvent,
    TextPart,
    ToolCallPart,
    ToolCallReq,
    ToolResultPart,
    ToolSpec,
    VideoPart,
    stream_via_generate,
)

log = logging.getLogger(__name__)

_client: genai.Client | None = None


def client() -> genai.Client:
    global _client
    if _client is None:
        if not settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is not set in .env")
        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


class GeminiProvider:
    def __init__(self, model: str = ""):
        self.model = model or settings.gemini_model

    def stream(
        self, *, system: str, messages: list[Msg], tools: list[ToolSpec] | None = None
    ) -> Iterator[StreamEvent]:
        return stream_via_generate(self, system=system, messages=messages, tools=tools)

    def generate(
        self, *, system: str, messages: list[Msg], tools: list[ToolSpec] | None = None
    ) -> LLMResponse:
        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=0.2,
            media_resolution=types.MediaResolution.MEDIA_RESOLUTION_LOW,
            thinking_config=types.ThinkingConfig(thinking_budget=settings.chat_thinking_budget),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        if tools:
            config.tools = [types.Tool(function_declarations=[_to_declaration(t) for t in tools])]
        resp = client().models.generate_content(
            model=self.model,
            contents=[_to_content(m) for m in messages],
            config=config,
        )
        log.info(
            "Gemini call: %d msgs, tools=%s, usage=%s",
            len(messages),
            bool(tools),
            resp.usage_metadata,
        )
        return _parse_response(resp)


def _to_declaration(spec: ToolSpec) -> types.FunctionDeclaration:
    kwargs: dict = {"name": spec.name, "description": spec.description}
    if spec.parameters:
        kwargs["parameters_json_schema"] = spec.parameters
    return types.FunctionDeclaration(**kwargs)


def _to_content(msg: Msg) -> types.Content:
    parts: list[types.Part] = []
    for p in msg.parts:
        if isinstance(p, TextPart):
            parts.append(types.Part.from_text(text=p.text))
        elif isinstance(p, ToolCallPart):
            part = types.Part.from_function_call(name=p.call.name, args=p.call.args)
            part.thought_signature = p.call.thought_signature
            parts.append(part)
        elif isinstance(p, ToolResultPart):
            parts.append(
                types.Part.from_function_response(name=p.name, response={"result": p.result})
            )
        elif isinstance(p, (VideoPart, ImagePart)):
            parts.append(types.Part.from_bytes(data=p.data, mime_type=p.mime_type))
        else:
            raise TypeError(f"Unknown message part: {type(p)!r}")
    role = "model" if msg.role == "model" else "user"
    return types.Content(role=role, parts=parts)


def _parse_response(resp) -> LLMResponse:
    texts: list[str] = []
    calls: list[ToolCallReq] = []
    candidate = resp.candidates[0] if resp.candidates else None
    if candidate and candidate.content and candidate.content.parts:
        for part in candidate.content.parts:
            if part.function_call:
                calls.append(
                    ToolCallReq(
                        name=part.function_call.name,
                        args=dict(part.function_call.args or {}),
                        id=part.function_call.id,
                        thought_signature=part.thought_signature,
                    )
                )
            elif part.text:
                texts.append(part.text)
    return LLMResponse(text="\n".join(texts).strip(), tool_calls=calls)
