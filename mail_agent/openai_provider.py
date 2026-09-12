"""OpenAI adapter implementing Wajo's existing proposer contract.

The adapter deliberately keeps credentials private and reuses the same prompts,
schemas and response validation as the bundled Groq path.  Selecting this paid
provider is handled by :mod:`model_settings`; constructing this class alone does
not enable it.
"""

import json
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from .groq_provider import GroqProposer, NoRedirect, ProviderError


DEFAULT_OPENAI_MODEL = "gpt-5.6-luna"


class OpenAIProposer(GroqProposer):
    """OpenAI Chat Completions implementation of ``propose``/``rewrite_draft``."""

    def __init__(self, api_key: str, model: str = DEFAULT_OPENAI_MODEL, max_retries: int = 2):
        if not api_key or any(character.isspace() for character in api_key):
            raise ProviderError("Enter a valid OpenAI API key")
        super().__init__(api_key, model, max_retries, serialize_calls=False)

    @staticmethod
    def _responses_payload(payload: dict) -> dict:
        schema = payload["response_format"]["json_schema"]
        return {
            "model": payload["model"],
            "input": payload["messages"],
            "temperature": payload["temperature"],
            "max_output_tokens": payload["max_completion_tokens"],
            "reasoning": {"effort": "none"},
            "text": {"format": {
                "type": "json_schema",
                "name": schema["name"],
                "strict": schema["strict"],
                "schema": schema["schema"],
            }},
            # Responses are otherwise retained as application state by default.
            "store": False,
        }

    @staticmethod
    def _chat_compatible_response(result: dict) -> dict:
        output = result.get("output", [])
        texts = [content.get("text", "") for item in output if item.get("type") == "message"
                 for content in item.get("content", []) if content.get("type") == "output_text"]
        refused = any(content.get("type") == "refusal" for item in output
                      for content in item.get("content", []))
        usage = result.get("usage", {})
        return {
            "choices": [{
                "finish_reason": "stop" if result.get("status") == "completed" else "incomplete",
                "message": {"content": "".join(texts), "refusal": refused},
            }],
            "usage": {
                "prompt_tokens": usage.get("input_tokens", 0),
                "completion_tokens": usage.get("output_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            },
            "model": result.get("model"),
        }

    def redact(self, text):
        text = str(text).replace(self._key, "[REDACTED_KEY]")
        return re.sub(
            r"(?i)\bBearer\s+[^\s\"']+|\b(?:sk|gsk)_[A-Za-z0-9_-]+|\bsk-[A-Za-z0-9_-]+",
            "[REDACTED_KEY]",
            text,
        )

    def request(self, route: str, payload=None):
        if route not in {"models", "chat/completions"}:
            raise ProviderError("Unsupported API route")
        is_response = route == "chat/completions"
        wire_route = "responses" if is_response else route
        wire_payload = self._responses_payload(payload) if is_response else payload
        request = Request(
            "https://api.openai.com/v1/" + wire_route,
            data=None if wire_payload is None else json.dumps(wire_payload).encode(),
            headers={
                "Authorization": "Bearer " + self._key,
                "Content-Type": "application/json",
                "User-Agent": "wajo-email-agent/0.2",
            },
        )
        waited = 0.0
        for attempt in range(self.max_retries + 1):
            start = time.monotonic()
            record = {"attempt": attempt + 1, "route": route}
            retryable = False
            headers = {}
            try:
                with build_opener(NoRedirect()).open(request, timeout=45) as response:
                    headers = self.response_headers(response.headers)
                    record.update(http_status=response.status, headers=headers)
                    raw = response.read(1_000_001)
                    if len(raw) > 1_000_000:
                        raise ProviderError("Provider response too large")
                    result = json.loads(raw)
                    record["status"] = "ok"
                    return self._chat_compatible_response(result) if is_response else result
            except HTTPError as exc:
                headers = self.response_headers(exc.headers)
                try:
                    raw = exc.read(65_537)
                    description = self.redact(raw[:65_536].decode("utf-8", errors="replace"))
                finally:
                    exc.close()
                record.update(
                    status="error",
                    http_status=exc.code,
                    headers=headers,
                    body=description,
                    body_truncated=len(raw) > 65_536,
                )
                error = f"OpenAI HTTP {exc.code}: {description or '(empty response body)'}"
                retryable = exc.code in {408, 429, 500, 502, 503, 504}
            except (URLError, TimeoutError, OSError) as exc:
                error = (
                    f"OpenAI transport failure ({type(exc).__name__}); "
                    "request did not produce a usable response"
                )
                record.update(status="error", error=error)
                retryable = True
            except (ValueError, TypeError) as exc:
                error = str(exc) if isinstance(exc, ProviderError) else "Invalid OpenAI JSON response"
                record.update(status="error", error=error)
            finally:
                record["latency_seconds"] = round(time.monotonic() - start, 3)
                self.http_attempts.append(record)
            delay = self.retry_delay(headers, attempt)
            if (
                not retryable
                or attempt == self.max_retries
                or delay is None
                or delay > 45
                or waited + delay > 60
            ):
                raise ProviderError(error) from None
            record["retry_delay_seconds"] = delay
            time.sleep(delay)
            waited += delay
