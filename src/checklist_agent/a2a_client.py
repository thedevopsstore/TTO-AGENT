"""Minimal A2A client for poking at the running checklist-agent server.

Uses `strands.agent.a2a_agent.A2AAgent` (strands-agents >= 1.25), which wraps
card resolution, client factory, message building, and response parsing so
this file stays boringly short.

Usage:

    # one-shot
    python scripts/a2a_client.py --prompt "Validate TTO for project 1101672345"

    # streaming (see incremental updates)
    python scripts/a2a_client.py --stream --prompt "..."

    # pick a different target
    python scripts/a2a_client.py --url http://my-host:9000 --prompt "..."

    # interactive REPL
    python scripts/a2a_client.py

    # dump the agent card and exit
    python scripts/a2a_client.py --card
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from strands.agent.a2a_agent import A2AAgent


def _message_text(message) -> str:
    """Extract text from an AgentResult.message ({'role', 'content': [{'text': ...}]})."""
    if not message:
        return ""
    content = message.get("content") if isinstance(message, dict) else None
    if not content:
        return ""
    return "\n".join(block.get("text", "") for block in content if isinstance(block, dict))


def _print_result(result, show_raw: bool) -> None:
    if show_raw:
        payload = result.model_dump(mode="json") if hasattr(result, "model_dump") else result
        print(json.dumps(payload, default=str, indent=2))
        return
    text = _message_text(getattr(result, "message", None))
    print(text or repr(result))


async def _send_once(url: str, text: str, timeout: int, stream: bool, show_raw: bool) -> None:
    agent = A2AAgent(endpoint=url, timeout=timeout)
    print(f"--> {text}\n")

    if stream:
        async for event in agent.stream_async(text):
            if show_raw:
                print(json.dumps(event, default=str, indent=2))
                continue
            data = event.get("data") if isinstance(event, dict) else None
            if data:
                print(data, end="", flush=True)
        print()
        return

    result = await agent.invoke_async(text)
    _print_result(result, show_raw)


async def _print_card(url: str, timeout: int) -> None:
    agent = A2AAgent(endpoint=url, timeout=timeout)
    card = await agent.get_agent_card()
    payload = card.model_dump(mode="json") if hasattr(card, "model_dump") else card
    print(json.dumps(payload, default=str, indent=2))


async def _repl(url: str, timeout: int, stream: bool, show_raw: bool) -> None:
    print(f"connected to {url} (Ctrl-D or 'exit' to quit)")
    while True:
        try:
            text = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not text:
            continue
        if text.lower() in {"exit", "quit"}:
            return
        try:
            await _send_once(url, text, timeout=timeout, stream=stream, show_raw=show_raw)
        except Exception as exc:  # noqa: BLE001
            print(f"[error] {exc}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="A2A client for the TTO checklist agent.")
    parser.add_argument("--url", default="http://127.0.0.1:9000", help="A2A server base URL.")
    parser.add_argument("--prompt", help="One-shot prompt. Omit for interactive REPL.")
    parser.add_argument("--stream", action="store_true", help="Use streaming transport.")
    parser.add_argument("--raw", action="store_true", help="Print the full JSON response.")
    parser.add_argument("--card", action="store_true", help="Print the agent card and exit.")
    parser.add_argument("--timeout", type=int, default=600, help="HTTP timeout in seconds.")
    args = parser.parse_args()

    if args.card:
        asyncio.run(_print_card(args.url, timeout=args.timeout))
        return

    if args.prompt:
        asyncio.run(
            _send_once(args.url, args.prompt, timeout=args.timeout, stream=args.stream, show_raw=args.raw)
        )
        return

    asyncio.run(_repl(args.url, timeout=args.timeout, stream=args.stream, show_raw=args.raw))


if __name__ == "__main__":
    main()
