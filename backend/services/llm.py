"""
Multi-provider LLM client with automatic rate-limit fallback.

Priority order is set by PROVIDERS env var (comma-separated).
When a provider hits a rate limit it enters a cooldown period,
and the next available provider is tried automatically.

Supported providers: anthropic, groq, gemini
"""
import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional

from backend.config import (
    ANTHROPIC_API_KEY, CLAUDE_MODEL,
    GROQ_API_KEY, GROQ_MODEL,
    GEMINI_API_KEY, GEMINI_MODEL,
    PROVIDER_ORDER, PROVIDER_COOLDOWN_SECONDS,
)


# ── Provider state ─────────────────────────────────────────────────────────────

@dataclass
class ProviderState:
    name: str
    cooldown_until: float = 0.0  # monotonic timestamp

    def is_available(self) -> bool:
        return time.monotonic() >= self.cooldown_until

    def mark_rate_limited(self, seconds: float):
        self.cooldown_until = time.monotonic() + seconds
        print(f"[llm] Provider '{self.name}' rate-limited. Cooling down {seconds}s.")

    def mark_error(self):
        # Brief cooldown on generic errors so we don't hammer a broken provider
        self.cooldown_until = time.monotonic() + 15.0
        print(f"[llm] Provider '{self.name}' error. Brief cooldown 15s.")


# Singleton state per provider — persists for the process lifetime
_states: dict[str, ProviderState] = {
    name: ProviderState(name=name) for name in ["anthropic", "groq", "gemini"]
}


# ── Per-provider completion functions ─────────────────────────────────────────

async def _complete_anthropic(system: str, messages: list[dict], max_tokens: int) -> str:
    import anthropic as _anthropic
    client = _anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    # Run sync client in thread to avoid blocking event loop
    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(
        None,
        lambda: client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
        )
    )
    return response.content[0].text


async def _complete_groq(system: str, messages: list[dict], max_tokens: int) -> str:
    from openai import AsyncOpenAI, RateLimitError
    client = AsyncOpenAI(
        api_key=GROQ_API_KEY,
        base_url="https://api.groq.com/openai/v1",
    )
    full_messages = [{"role": "system", "content": system}] + messages
    response = await client.chat.completions.create(
        model=GROQ_MODEL,
        max_tokens=max_tokens,
        messages=full_messages,
    )
    return response.choices[0].message.content


async def _complete_gemini(system: str, messages: list[dict], max_tokens: int) -> str:
    import google.generativeai as genai
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(
        model_name=GEMINI_MODEL,
        system_instruction=system,
    )
    # Gemini uses a different message format; map role "user"/"assistant" → "user"/"model"
    history = []
    for msg in messages[:-1]:
        role = "model" if msg["role"] == "assistant" else "user"
        history.append({"role": role, "parts": [msg["content"]]})

    chat = model.start_chat(history=history)
    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(
        None,
        lambda: chat.send_message(
            messages[-1]["content"],
            generation_config={"max_output_tokens": max_tokens},
        )
    )
    return response.text


_COMPLETERS = {
    "anthropic": _complete_anthropic,
    "groq": _complete_groq,
    "gemini": _complete_gemini,
}

_CONFIGURED = {
    "anthropic": bool(ANTHROPIC_API_KEY),
    "groq": bool(GROQ_API_KEY),
    "gemini": bool(GEMINI_API_KEY),
}


# ── Public API ─────────────────────────────────────────────────────────────────

async def complete(
    system: str,
    messages: list[dict],
    max_tokens: int = 2048,
    preferred_provider: Optional[str] = None,
) -> tuple[str, str]:
    """
    Call the LLM with automatic provider fallback.

    Returns (response_text, provider_name_used).
    Raises RuntimeError if all providers fail or are cooling down.
    """
    # Build ordered list: preferred first, then env-configured order
    order = list(PROVIDER_ORDER)
    if preferred_provider and preferred_provider in order:
        order.remove(preferred_provider)
        order.insert(0, preferred_provider)

    last_error: Exception | None = None

    for name in order:
        if not _CONFIGURED.get(name):
            continue  # No API key configured for this provider

        state = _states[name]
        if not state.is_available():
            remaining = state.cooldown_until - time.monotonic()
            print(f"[llm] Skipping '{name}' — cooling down {remaining:.0f}s")
            continue

        completer = _COMPLETERS.get(name)
        if not completer:
            continue

        try:
            text = await completer(system, messages, max_tokens)
            print(f"[llm] Used provider: {name}")
            return text, name
        except Exception as e:
            err_str = str(e).lower()
            if "rate" in err_str or "429" in err_str or "quota" in err_str or "limit" in err_str:
                state.mark_rate_limited(PROVIDER_COOLDOWN_SECONDS)
            else:
                state.mark_error()
            last_error = e
            print(f"[llm] Provider '{name}' failed: {e}")
            continue

    raise RuntimeError(
        f"All LLM providers failed or are cooling down. Last error: {last_error}"
    )


def available_providers() -> list[str]:
    """Return names of configured + currently available providers."""
    return [
        name for name in PROVIDER_ORDER
        if _CONFIGURED.get(name) and _states[name].is_available()
    ]


def provider_status() -> dict:
    """Return status dict for all providers (for /health endpoint)."""
    now = time.monotonic()
    return {
        name: {
            "configured": _CONFIGURED.get(name, False),
            "available": _states[name].is_available(),
            "cooldown_remaining": max(0.0, round(_states[name].cooldown_until - now, 1)),
        }
        for name in ["anthropic", "groq", "gemini"]
    }
