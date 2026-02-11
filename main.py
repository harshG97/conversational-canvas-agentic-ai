#!/usr/bin/env python3
"""Interactive CLI for the Canvas LMS conversational AI agent."""

from __future__ import annotations

import sys
import threading

from dotenv import load_dotenv


def main() -> None:
    load_dotenv()

    from canvas_agent import setup_logging
    setup_logging()

    import os

    for v in ("CANVAS_API_URL", "CANVAS_API_TOKEN"):
        if not os.environ.get(v):
            print(f"Error: missing environment variable: {v}")
            print("Copy .env.example to .env and fill in your values.")
            sys.exit(1)

    if not os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("GROQ_API_KEY"):
        print("Error: set ANTHROPIC_API_KEY or GROQ_API_KEY in .env")
        sys.exit(1)

    from rich.console import Console
    from rich.live import Live
    from rich.spinner import Spinner

    from canvas_agent.agent import Agent

    console = Console()

    # -- Shared state for the spinner / streaming --
    live: Live | None = None
    spinner_lock = threading.Lock()
    streaming_started = False

    def on_tool_start(label: str) -> None:
        nonlocal live
        with spinner_lock:
            if live is not None:
                live.update(Spinner("dots", text=f"  {label}..."))

    def on_tool_detail(msg: str) -> None:
        nonlocal live
        with spinner_lock:
            if live is not None:
                live.update(Spinner("dots", text=f"  {msg}"))

    def on_text_chunk(text: str) -> None:
        nonlocal live, streaming_started
        # Stop the spinner when the first text token arrives
        with spinner_lock:
            if live is not None:
                live.stop()
                live = None
        if not streaming_started:
            console.print("\n[bold]Assistant:[/bold] ", end="")
            streaming_started = True
        print(text, end="", flush=True)

    agent = Agent(
        on_tool_start=on_tool_start,
        on_tool_detail=on_tool_detail,
        on_text_chunk=on_text_chunk,
    )

    console.print("[bold]Canvas LMS AI Agent[/bold]")
    console.print("Ask me about your upcoming assignments, quizzes, and deadlines.")
    console.print('Type "!refresh" to clear cached data, "quit" or "exit" to leave.\n')

    try:
        while True:
            try:
                user_input = input("You: ").strip()
            except EOFError:
                break

            if not user_input:
                continue
            if user_input.lower() in ("quit", "exit"):
                break
            if user_input.lower() == "!refresh":
                agent._handler.clear_cache()
                console.print("[dim]Cache cleared. Next query will fetch fresh data.[/dim]\n")
                continue

            streaming_started = False
            live = Live(
                Spinner("dots", text="  Thinking..."),
                console=console,
                transient=True,
            )
            live.start()

            try:
                agent.chat(user_input)
            finally:
                with spinner_lock:
                    if live is not None:
                        live.stop()
                        live = None

            print("\n")
    except KeyboardInterrupt:
        print()
    finally:
        agent.close()
        console.print("Goodbye!")


if __name__ == "__main__":
    main()
