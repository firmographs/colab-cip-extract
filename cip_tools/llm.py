"""
Thin httpx wrapper around the Anthropic Messages API.
Bypasses the anthropic SDK (pydantic 3.14 incompatibility).

Usage:
    from cip_tools.llm import ask, ask_json

    text = ask("claude-sonnet-4-6", system="...", user="...")
    obj  = ask_json("claude-sonnet-4-6", system="...", user="...")  # parses first JSON block
"""

import json
import os
import re
import time

import httpx

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

# Model aliases
HAIKU  = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-4-6"
OPUS   = "claude-opus-4-7"

DEFAULT_MODEL = SONNET


def _api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_API")
    if not key:
        raise RuntimeError(
            "No Anthropic API key found. Set ANTHROPIC_API_KEY or CLAUDE_API "
            "environment variable, or run via: doppler run -- python -m cip_tools ..."
        )
    return key


def ask(
    user: str,
    *,
    system: str = "",
    model: str = DEFAULT_MODEL,
    max_tokens: int = 16000,
    temperature: float = 0.2,
    retries: int = 6,
) -> str:
    """Send a message to Claude and return the text response."""
    import random

    headers = {
        "x-api-key": _api_key(),
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }
    payload: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [{"role": "user", "content": user}],
    }
    if system:
        payload["system"] = system

    for attempt in range(retries):
        try:
            resp = httpx.post(
                ANTHROPIC_API_URL,
                headers=headers,
                json=payload,
                timeout=120.0,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["content"][0]["text"]
        except httpx.HTTPStatusError as e:
            retryable = e.response.status_code in (429, 500, 502, 503, 529)
            if retryable and attempt < retries - 1:
                # Exponential backoff with jitter: 30s, 60s, 120s, 240s, 480s
                delay = min(30 * (2 ** attempt), 480) + random.uniform(0, 10)
                print(f"  API {e.response.status_code} — retrying in {delay:.0f}s (attempt {attempt+1}/{retries})")
                time.sleep(delay)
                continue
            raise
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as e:
            if attempt < retries - 1:
                delay = 15 * (attempt + 1) + random.uniform(0, 5)
                print(f"  Network error — retrying in {delay:.0f}s (attempt {attempt+1}/{retries})")
                time.sleep(delay)
                continue
            raise
    raise RuntimeError("Exhausted retries")


def ask_json(
    user: str,
    *,
    system: str = "",
    model: str = DEFAULT_MODEL,
    max_tokens: int = 16000,
) -> dict | list:
    """
    Like ask() but extracts and parses the first JSON block from the response.
    Raises ValueError if no valid JSON is found.
    """
    raw = ask(user, system=system, model=model, max_tokens=max_tokens)

    # Try direct parse first
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Look for ```json ... ``` block
    m = re.search(r"```json\s*\n([\s\S]*?)\n```", raw)
    if m:
        return json.loads(m.group(1))

    # Look for any ``` ... ``` block
    m = re.search(r"```\s*\n([\s\S]*?)\n```", raw)
    if m:
        return json.loads(m.group(1))

    # Look for bare { ... } or [ ... ] block
    m = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", raw)
    if m:
        return json.loads(m.group(1))

    raise ValueError(f"No JSON found in LLM response:\n{raw[:500]}")
