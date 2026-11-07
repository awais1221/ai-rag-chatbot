"""Provider-agnostic LLM access via the OpenAI-compatible API.

Any provider exposing an OpenAI-compatible ``/chat/completions`` endpoint works
(Google Gemini, GitHub Models, Groq, Ollama, OpenAI, OpenRouter, ...). Switch
providers by editing BASE_URL / API_KEY / MODEL_NAME in ``.env``.

``call_llm`` and ``stream_llm`` are the only functions that touch the network,
which makes them the single place tests need to mock.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterator
from functools import lru_cache

from openai import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    OpenAI,
    RateLimitError,
)

from app.config import get_settings

logger = logging.getLogger(__name__)


class LLMNotConfiguredError(Exception):
    """BASE_URL / API_KEY / MODEL_NAME are missing from .env."""


class LLMError(Exception):
    """The provider returned an error (rate limit, auth, timeout, ...)."""


@lru_cache
def _get_client() -> OpenAI:
    settings = get_settings()
    return OpenAI(
        base_url=settings.base_url,
        api_key=settings.api_key,
        timeout=settings.llm_timeout,
        max_retries=2,  # SDK retries short network blips / 5xx with backoff
    )


def _retry_delay(exc: RateLimitError) -> float:
    """Seconds the provider asks us to wait after a 429 (default 20s).
    Uses the Retry-After header, or Gemini's "retry in 49.5s" / retryDelay text."""
    header = exc.response.headers.get("retry-after") if exc.response is not None else None
    if header:
        try:
            return float(header)
        except ValueError:
            pass
    match = re.search(r"retry in ([\d.]+)\s*s|retryDelay['\"]?\s*:\s*['\"]([\d.]+)s",
                      f"{exc.body} {exc}", re.IGNORECASE)
    if match:
        return float(match.group(1) or match.group(2))
    return 20.0


def _human_duration(seconds: float) -> str:
    minutes = round(seconds / 60)
    if minutes < 1:
        return f"{seconds:.0f} seconds"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m" if hours else f"{minutes} min"


def _create_with_rate_limit_retry(client: OpenAI, kwargs: dict, settings):
    """Free tiers allow only a few requests per minute. On a 429, wait as long
    as the provider asks (capped) and try again a limited number of times."""
    for attempt in range(settings.rate_limit_retries + 1):
        try:
            return client.chat.completions.create(**kwargs)
        except RateLimitError as exc:
            delay = _retry_delay(exc)
            if delay > settings.rate_limit_max_wait:
                # Not a per-minute limit: typically the free daily quota is used up.
                logger.warning("Provider quota exhausted; retry possible in %s",
                               _human_duration(delay))
                raise LLMError(
                    "The free AI quota for today is used up (it resets in about "
                    f"{_human_duration(delay)}). Please try again later."
                ) from exc
            if attempt >= settings.rate_limit_retries:
                raise
            wait = delay + 1  # small safety margin
            logger.info("Rate limited by provider; waiting %.0fs before retry %d/%d",
                        wait, attempt + 1, settings.rate_limit_retries)
            time.sleep(wait)


def _ensure_configured(settings) -> None:
    if not settings.llm_configured:
        raise LLMNotConfiguredError(
            "The AI model is not configured. Copy .env.example to .env and set "
            "BASE_URL, API_KEY and MODEL_NAME."
        )


def _translate(exc: Exception, settings) -> LLMError:
    """Turn any provider/network exception into an LLMError with a friendly message."""
    if isinstance(exc, LLMError):
        return exc
    if isinstance(exc, APITimeoutError):
        logger.warning("LLM request timed out after %ss", settings.llm_timeout)
        return LLMError("The AI provider took too long to respond. Please try again.")
    if isinstance(exc, APIConnectionError):
        logger.warning("Could not connect to the LLM provider (APIConnectionError)")
        return LLMError("Could not connect to the AI provider. Please try again shortly.")
    if isinstance(exc, APIError):
        # Only the status / type is logged; never the request or credentials.
        status = getattr(exc, "status_code", None)
        logger.warning("LLM request failed (status=%s, %s)", status, type(exc).__name__)
        return LLMError(_friendly_error(status))
    logger.warning("LLM request failed: %s", type(exc).__name__)
    return LLMError(f"Could not reach the AI provider ({type(exc).__name__}).")


def _base_kwargs(messages: list[dict], settings) -> dict:
    return {
        "model": settings.model_name,
        "messages": messages,
        "temperature": settings.llm_temperature,
    }


def call_llm(messages: list[dict]) -> str:
    """Send chat messages and return the assistant's full text reply."""
    settings = get_settings()
    _ensure_configured(settings)
    started = time.perf_counter()
    try:
        response = _create_with_rate_limit_retry(
            _get_client(), _base_kwargs(messages, settings), settings
        )
    except Exception as exc:
        raise _translate(exc, settings) from exc

    logger.info("LLM call to model=%s took %.1fs",
                settings.model_name, time.perf_counter() - started)
    content = response.choices[0].message.content if response.choices else None
    return content or ""


def stream_llm(messages: list[dict]) -> Iterator[str]:
    """Send chat messages and yield the reply in text pieces as they arrive."""
    settings = get_settings()
    _ensure_configured(settings)
    started = time.perf_counter()
    try:
        stream = _create_with_rate_limit_retry(
            _get_client(), {**_base_kwargs(messages, settings), "stream": True}, settings
        )
        for chunk in stream:
            if not chunk.choices:
                continue
            piece = chunk.choices[0].delta.content
            if piece:
                yield piece
    except Exception as exc:
        raise _translate(exc, settings) from exc
    logger.info("LLM stream from model=%s took %.1fs",
                settings.model_name, time.perf_counter() - started)


def _friendly_error(status: int | None) -> str:
    if status in (401, 403):
        return "The AI provider rejected the API key (check API_KEY in .env)."
    if status == 404:
        return "AI model or endpoint not found (check BASE_URL and MODEL_NAME in .env)."
    if status == 429:
        return "The AI is getting too many requests right now. Wait a minute and try again."
    if status in (500, 502, 503, 504):
        return "The AI provider is temporarily unavailable or overloaded. Please retry shortly."
    return f"AI provider error (status {status})."
