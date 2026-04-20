"""Minimal A2A client for poking at the running checklist-agent server.

Usage:

    # one-shot
    python scripts/a2a_client.py --prompt "Validate TTO for project 1101672345"

    # pick a different target
    python scripts/a2a_client.py --url http://my-host:9000 --prompt "..."

    # interactive REPL
    python scripts/a2a_client.py

    # streaming (see incremental updates)
    python scripts/a2a_client.py --stream --prompt "..."

    # dump the agent card and exit
    python scripts/a2a_client.py --card

This exists so you can test the A2A server without the parent agent. It speaks
the same protocol any Strands-based A2A parent will speak, so if this works the
real parent will too.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from uuid import uuid4

import httpx
from a2a.client import A2ACardResolver, A2AClient
from a2a.types import MessageSendParams, SendMessageRequest, SendStreamingMessageRequest


def _build_send_params(text: str) -> MessageSendParams:
    return MessageSendParams(
        message={
            "role": "user",
            "parts": [{"type": "text", "text": text}],
            "messageId": str(uuid4()),
        }
    )


def _extract_text(response_payload: dict) -> str:
    """Pull human-readable text out of an A2A response, best-effort across shapes."""
    chunks: list[str] = []

    def walk(obj):
        if isinstance(obj, dict):
            if obj.get("type") == "text" and isinstance(obj.get("text"), str):
                chunks.append(obj["text"])
                return
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)

    walk(response_payload)
    return "\n".join(c.strip() for c in chunks if c.strip())


async def _print_card(url: str) -> None:
    async with httpx.AsyncClient(timeout=30) as http:
        resolver = A2ACardResolver(http, url)
        card = await resolver.get_agent_card()
        print(json.dumps(card.model_dump(mode="json"), indent=2))


async def _send_once(url: str, text: str, stream: bool, show_raw: bool) -> None:
    async with httpx.AsyncClient(timeout=600) as http:
        client = await A2AClient.get_client_from_agent_card_url(http, url)

        if stream:
            req = SendStreamingMessageRequest(id=str(uuid4()), params=_build_send_params(text))
            print(f"--> {text}\n")
            async for event in client.send_message_streaming(req):
                payload = event.model_dump(mode="json")
                if show_raw:
                    print(json.dumps(payload, indent=2))
                    continue
                t = _extract_text(payload)
                if t:
                    print(t, flush=True)
            return

        req = SendMessageRequest(id=str(uuid4()), params=_build_send_params(text))
        print(f"--> {text}\n")
        resp = await client.send_message(req)
        payload = resp.model_dump(mode="json")
        if show_raw:
            print(json.dumps(payload, indent=2))
            return
        print(_extract_text(payload) or json.dumps(payload, indent=2))


async def _repl(url: str, stream: bool, show_raw: bool) -> None:
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
            await _send_once(url, text, stream=stream, show_raw=show_raw)
        except Exception as exc:  # noqa: BLE001
            print(f"[error] {exc}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="A2A client for the TTO checklist agent.")
    parser.add_argument("--url", default="http://127.0.0.1:9000", help="A2A server base URL.")
    parser.add_argument("--prompt", help="One-shot prompt. Omit for interactive REPL.")
    parser.add_argument("--stream", action="store_true", help="Use streaming transport.")
    parser.add_argument("--raw", action="store_true", help="Print the full JSON response.")
    parser.add_argument("--card", action="store_true", help="Print the agent card and exit.")
    args = parser.parse_args()

    if args.card:
        asyncio.run(_print_card(args.url))
        return

    if args.prompt:
        asyncio.run(_send_once(args.url, args.prompt, stream=args.stream, show_raw=args.raw))
        return

    asyncio.run(_repl(args.url, stream=args.stream, show_raw=args.raw))


if __name__ == "__main__":
    main()
