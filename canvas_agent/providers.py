"""LLM provider abstraction for Anthropic and Groq backends."""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable

from .tools import TOOLS

log = logging.getLogger(__name__)


@dataclass
class ToolCall:
    """Unified representation of a tool call from any LLM provider."""
    id: str
    name: str
    input: dict


@dataclass
class LLMResponse:
    """Unified response from any LLM provider."""
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw_content: object = None  # provider-specific content for message history


# ── Anthropic ────────────────────────────────────────────────────────


class AnthropicProvider:
    """Anthropic Claude backend with streaming and tool use."""

    def __init__(self, model: str) -> None:
        import anthropic

        self._client = anthropic.Anthropic()
        self.model = model
        log.info("AnthropicProvider created — model=%s", model)

    def chat(
        self,
        *,
        system: str,
        messages: list[dict],
        on_text_chunk: Callable[[str], None] | None = None,
    ) -> LLMResponse:
        log.debug("Anthropic streaming request — model=%s, messages=%d", self.model, len(messages))
        with self._client.messages.stream(
            model=self.model,
            max_tokens=4096,
            system=system,
            tools=TOOLS,
            messages=messages,
        ) as stream:
            collected_text = ""
            for event in stream:
                if hasattr(event, "type"):
                    if event.type == "content_block_delta":
                        if hasattr(event.delta, "text"):
                            collected_text += event.delta.text
                            if on_text_chunk:
                                on_text_chunk(event.delta.text)

            final = stream.get_final_message()

        tool_calls = [
            ToolCall(id=b.id, name=b.name, input=b.input)
            for b in final.content
            if b.type == "tool_use"
        ]

        log.info("Anthropic response — text=%d chars, tool_calls=%d, stop=%s, "
                 "usage(in=%d, out=%d)",
                 len(collected_text), len(tool_calls), final.stop_reason,
                 final.usage.input_tokens, final.usage.output_tokens)
        for tc in tool_calls:
            log.debug("  tool_call: %s(%s)", tc.name, tc.input)

        return LLMResponse(
            text=collected_text,
            tool_calls=tool_calls,
            raw_content=final.content,
        )

    def build_assistant_message(self, response: LLMResponse) -> dict:
        return {"role": "assistant", "content": response.raw_content}

    def build_tool_results_message(
        self, results: list[tuple[str, str]]
    ) -> dict:
        """results is a list of (tool_call_id, json_string) tuples."""
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tid,
                    "content": data,
                }
                for tid, data in results
            ],
        }

    @property
    def is_done(self) -> bool:
        return True  # checked via stop_reason in chat(); not needed here


# ── Groq ─────────────────────────────────────────────────────────────


def _anthropic_tools_to_openai() -> list[dict]:
    """Convert Anthropic tool definitions to OpenAI/Groq format."""
    openai_tools = []
    for tool in TOOLS:
        openai_tools.append(
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": tool["input_schema"],
                },
            }
        )
    return openai_tools


_OPENAI_TOOLS = _anthropic_tools_to_openai()


class GroqProvider:
    """Groq backend with streaming and tool use (OpenAI-compatible)."""

    def __init__(self, model: str) -> None:
        from groq import Groq

        self._client = Groq()
        self.model = model
        log.info("GroqProvider created — model=%s", model)

    def chat(
        self,
        *,
        system: str,
        messages: list[dict],
        on_text_chunk: Callable[[str], None] | None = None,
    ) -> LLMResponse:
        # Build the messages list with a system message at the front
        oai_messages = [{"role": "system", "content": system}]
        oai_messages.extend(messages)
        log.debug("Groq streaming request — model=%s, messages=%d", self.model, len(oai_messages))

        stream = self._client.chat.completions.create(
            model=self.model,
            messages=oai_messages,
            tools=_OPENAI_TOOLS,
            max_tokens=4096,
            stream=True,
        )

        collected_text = ""
        # Accumulate tool calls across streamed chunks
        tool_call_accum: dict[int, dict] = {}

        for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta is None:
                continue

            # Stream text content
            if delta.content:
                collected_text += delta.content
                if on_text_chunk:
                    on_text_chunk(delta.content)

            # Accumulate tool call deltas
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tool_call_accum:
                        tool_call_accum[idx] = {
                            "id": "",
                            "name": "",
                            "arguments": "",
                        }
                    if tc_delta.id:
                        tool_call_accum[idx]["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            tool_call_accum[idx]["name"] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            tool_call_accum[idx]["arguments"] += (
                                tc_delta.function.arguments
                            )

        tool_calls = []
        # Build raw tool_calls list for the assistant message
        raw_tool_calls = []
        for idx in sorted(tool_call_accum):
            tc = tool_call_accum[idx]
            args = json.loads(tc["arguments"]) if tc["arguments"] else {}
            tool_calls.append(
                ToolCall(id=tc["id"], name=tc["name"], input=args)
            )
            raw_tool_calls.append(
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": tc["arguments"],
                    },
                }
            )

        log.info("Groq response — text=%d chars, tool_calls=%d",
                 len(collected_text), len(tool_calls))
        for tc in tool_calls:
            log.debug("  tool_call: %s(%s)", tc.name, tc.input)

        return LLMResponse(
            text=collected_text,
            tool_calls=tool_calls,
            raw_content=raw_tool_calls,
        )

    def build_assistant_message(self, response: LLMResponse) -> dict:
        msg: dict = {"role": "assistant", "content": response.text or None}
        if response.raw_content:
            msg["tool_calls"] = response.raw_content
        return msg

    def build_tool_results_message(
        self, results: list[tuple[str, str]]
    ) -> list[dict]:
        """Groq/OpenAI uses separate messages per tool result."""
        return [
            {
                "role": "tool",
                "tool_call_id": tid,
                "content": data,
            }
            for tid, data in results
        ]


# ── Factory ──────────────────────────────────────────────────────────

# Single source of truth for selectable models.
# Display label → (model ID, provider). Add or remove models here only;
# the Streamlit dropdown and provider routing are both built from this.
MODELS: dict[str, tuple[str, str]] = {
    "Claude Sonnet 4.5": ("claude-sonnet-4-5-20250929", "anthropic"),
    "Claude Haiku 4.5": ("claude-haiku-4-5-20251001", "anthropic"),
    # "Claude Opus 4.6": ("claude-opus-4-6", "anthropic"),
    "GPT-OSS 120B (Groq)": ("openai/gpt-oss-120b", "groq"),
    "GPT-OSS 20B (Groq)": ("openai/gpt-oss-20b", "groq"),
}

GROQ_MODELS = {model_id for model_id, provider in MODELS.values() if provider == "groq"}


def create_provider(model: str) -> AnthropicProvider | GroqProvider:
    """Create the right provider based on the model ID."""
    if model in GROQ_MODELS:
        return GroqProvider(model)
    return AnthropicProvider(model)
