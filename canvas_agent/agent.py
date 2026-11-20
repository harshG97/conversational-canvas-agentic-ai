"""Agentic conversation loop: user ↔ LLM ↔ Canvas LMS tools."""

from __future__ import annotations

import logging
import os
from typing import Callable

from .canvas_client import CanvasClient
from .dates import date_reference
from .providers import GroqProvider, create_provider
from .tool_handler import ToolHandler
from datetime import datetime
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")

_SYSTEM_PROMPT_TEMPLATE = """\
You are a helpful academic assistant connected to the user's Canvas LMS.
You can look up their courses, upcoming assignments, quizzes, and deadlines.
You can also add deadlines to the user's Google Calendar.

Guidelines:
- When the user asks about upcoming deadlines without specifying a course,
  use the get_all_upcoming_deadlines tool to check every enrolled course.
- As much as possible use get_all_upcoming_deadlines for general deadline questions.
- All due dates are in US Eastern time. Present them in a human-friendly
  format (e.g. "Friday, Feb 14 at 11:59 PM ET").
- get_all_upcoming_deadlines already removes quiz duplicates. If you use the
  per-course tools and an item appears as both a quiz and an assignment,
  treat it as a quiz.
- If a deadline has no due date, mention that it has no listed due date.
- Be concise but thorough — list every relevant item.
- If something goes wrong with the Canvas API, explain the error clearly.
- When the user asks to add deadlines to their calendar, use the
  add_deadlines_to_calendar tool. You can optionally filter by course name.
- Deadline tools take start_date / end_date. For time phrases, copy the
  matching range from the date table below; do not guess dates.
- Start your answer by stating the date range you used
  (e.g. "Due Mon Nov 23 to Sun Nov 29:").

{date_reference}
"""


def build_system_prompt(now: datetime | None = None) -> str:
    """Return the system prompt with today's date table (US Eastern).

    Built on every request so long-running servers never use a stale date.
    """
    now = now or datetime.now(_ET)
    return _SYSTEM_PROMPT_TEMPLATE.format(date_reference=date_reference(now))

DEFAULT_MODEL = "claude-sonnet-4-5-20250929"
DEFAULT_KEEP_TURNS = 6  # number of recent messages to keep fully intact
MAX_TOOL_ROUNDS = 15  # safety limit for agentic loop iterations
_TRIMMED = "[previous data omitted]"


class Agent:
    """Runs the agentic tool-use loop between an LLM and Canvas LMS.

    Callbacks (all optional):
        on_tool_start(label)   – called when a tool begins executing
        on_tool_detail(msg)    – called with sub-step detail messages
        on_text_chunk(text)    – called with each streamed text token
    """

    def __init__(
        self,
        *,
        model: str | None = None,
        canvas_token: str | None = None,
        google_creds=None,
        on_tool_start: Callable[[str], None] | None = None,
        on_tool_detail: Callable[[str], None] | None = None,
        on_text_chunk: Callable[[str], None] | None = None,
    ) -> None:
        resolved_model = model or os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL)
        self._provider = create_provider(resolved_model)
        self._canvas = CanvasClient(canvas_token=canvas_token)
        self._handler = ToolHandler(
            self._canvas,
            google_creds=google_creds,
            on_tool_start=on_tool_start,
            on_tool_detail=on_tool_detail,
        )
        self._messages: list[dict] = []
        self._on_text_chunk = on_text_chunk
        self.model = resolved_model
        self._keep_turns = int(os.environ.get("HISTORY_KEEP_TURNS", DEFAULT_KEEP_TURNS))
        log.info("Agent initialised — model=%s, provider=%s, keep_turns=%d",
                 self.model, type(self._provider).__name__, self._keep_turns)

    def _trim_history(self) -> None:
        """Trim conversation history to manage token usage.

        Strategy:
        1. Delete messages older than _keep_turns entirely.
        2. Clean up orphaned tool results/uses left at the boundary.
        3. Strip tool result content from all remaining messages.
           (Current turn's tool results are added *after* trim, so they
            stay fully intact.)
        """
        if len(self._messages) <= self._keep_turns:
            log.debug("_trim_history: %d messages ≤ keep_turns=%d, nothing to trim",
                       len(self._messages), self._keep_turns)
            return

        # --- Step 1: delete old messages ---
        cutoff = len(self._messages) - self._keep_turns
        log.info("_trim_history: deleting %d old messages (keeping %d)", cutoff, self._keep_turns)
        self._messages = self._messages[cutoff:]

        # --- Step 2: clean up orphaned messages at the start ---
        # Deletion may leave orphaned tool results (whose matching tool_use
        # was in a deleted assistant message) or assistant messages at the
        # front.  Both APIs require the conversation to start with a "user"
        # message containing actual text, so we drop messages until we reach
        # one.
        orphaned_count = 0
        while self._messages:
            msg = self._messages[0]
            role = msg.get("role")

            # Groq/OpenAI orphaned tool result
            if role == "tool":
                self._messages.pop(0)
                orphaned_count += 1
                continue

            # Assistant message can't start the conversation
            if role == "assistant":
                self._messages.pop(0)
                orphaned_count += 1
                continue

            # User message — but is it real text or just tool_result blocks?
            if role == "user":
                content = msg.get("content")
                if isinstance(content, str):
                    break  # real user text — valid start
                if isinstance(content, list):
                    has_text = any(
                        (isinstance(b, dict) and b.get("type") == "text")
                        or isinstance(b, str)
                        for b in content
                    )
                    if has_text:
                        break  # mixed content with real text — valid
                    # only tool_result blocks — orphaned, remove
                    self._messages.pop(0)
                    orphaned_count += 1
                    continue

            break  # unknown shape, keep it to be safe

        if orphaned_count:
            log.debug("_trim_history: removed %d orphaned messages", orphaned_count)

        # --- Step 3: strip tool results from ALL remaining messages ---
        # The current turn hasn't been added yet, so everything here is old.
        stripped_count = 0
        for msg in self._messages:
            # Groq/OpenAI: {"role": "tool", "content": "..."}
            if msg.get("role") == "tool":
                msg["content"] = _TRIMMED
                stripped_count += 1
                continue

            # Anthropic: {"role": "user", "content": [{"type": "tool_result", ...}]}
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        block["content"] = _TRIMMED
                        stripped_count += 1
        if stripped_count:
            log.debug("_trim_history: stripped %d tool results", stripped_count)
        log.info("_trim_history: %d messages remain after trim", len(self._messages))

    def chat(self, user_message: str) -> str:
        """Send a user message and return the assistant's final text reply.

        Streams text tokens via the on_text_chunk callback as they arrive.
        """
        self._trim_history()
        system_prompt = build_system_prompt()
        self._messages.append({"role": "user", "content": user_message})
        log.info("chat() called — user: %r  (%d messages in history)",
                 user_message[:80], len(self._messages))

        for round_num in range(1, MAX_TOOL_ROUNDS + 1):
            log.info("LLM round %d — sending %d messages to %s",
                     round_num, len(self._messages), self.model)
            response = self._provider.chat(
                system=system_prompt,
                messages=self._messages,
                on_text_chunk=self._on_text_chunk,
            )

            self._messages.append(
                self._provider.build_assistant_message(response)
            )

            # No tool calls → done
            if not response.tool_calls:
                log.info("LLM finished (no tool calls) — response: %d chars", len(response.text or ""))
                return response.text or "(no text response)"

            log.info("LLM requested %d tool call(s): %s", len(response.tool_calls),
                     [tc.name for tc in response.tool_calls])

            # Execute each tool call
            results: list[tuple[str, str]] = []
            for tc in response.tool_calls:
                log.debug("Executing tool: %s(id=%s, input=%s)", tc.name, tc.id, tc.input)
                data = self._handler.execute(tc.name, tc.input)
                log.debug("Tool %s result: %d chars", tc.name, len(data))
                results.append((tc.id, data))

            # Append tool results to the conversation
            tool_msg = self._provider.build_tool_results_message(results)
            if isinstance(tool_msg, list):
                # Groq/OpenAI: one message per tool result
                self._messages.extend(tool_msg)
            else:
                # Anthropic: single message with all results
                self._messages.append(tool_msg)

        log.warning("Reached MAX_TOOL_ROUNDS (%d)", MAX_TOOL_ROUNDS)
        return "I reached the maximum number of tool calls. Please try a simpler question."

    def close(self) -> None:
        self._canvas.close()
