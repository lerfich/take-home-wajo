"""Groq adapter. Credentials never enter prompts, reports, or exception text."""
from dataclasses import asdict
import json
import os
import math
import re
from email.utils import parsedate_to_datetime
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPRedirectHandler

from .core import Email, Proposal, validate, PATTERNS

DEFAULT_MODEL = "qwen/qwen3.8-27b"
PROMPT_VERSION = "triage-v6"
SYSTEM = (Path(__file__).parent / "prompts" / f"{PROMPT_VERSION}.txt").read_text()
FIELDS = {name: {"type": "string"} for name in ("action", "reason", "label", "text", "recipient")}
FIELDS.update({name: {"type": "boolean"} for name in ("notify", "suspicious", "needs_human")})
FIELDS.update({name: {"type": "boolean"} for name in ("requires_action", "has_deadline", "significant_change", "sensitive")})
FIELDS["pattern"] = {"type": "string", "enum": sorted(PATTERNS | {"unknown"})}
FIELDS["pattern_evidence"] = {"type": "string"}
SCHEMA = {"type": "object", "properties": FIELDS, "required": list(FIELDS), "additionalProperties": False}


class ProviderError(ValueError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_settings(path: Path) -> dict:
    """Small literal KEY=value reader; never source/evaluate a shell file."""
    values = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            name, sep, value = line.partition("=")
            if sep and name.strip() in {"GROQ_API_KEY", "GROQ_MODEL"}:
                values[name.strip()] = value.strip().strip("\"'")
    for name in ("GROQ_API_KEY", "GROQ_MODEL"):
        if name in os.environ:
            values[name] = os.environ[name]
    return values


class GroqProposer:
    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, max_retries: int = 2):
        if not api_key or any(c.isspace() for c in api_key):
            raise ProviderError("Set a valid GROQ_API_KEY in task/.env or the environment")
        self._key = api_key
        self.model = model
        self.calls = []
        if type(max_retries) is not int or not 0 <= max_retries <= 2:
            raise ValueError("max_retries must be 0, 1 or 2")
        self.max_retries = max_retries
        self.http_attempts = []

    def redact(self, text):
        text = str(text).replace(self._key, "[REDACTED_KEY]")
        return re.sub(r"(?i)\bBearer\s+[^\s\"']+|\bgsk_[A-Za-z0-9_-]+", "[REDACTED_KEY]", text)

    def response_headers(self, headers):
        return {k.lower(): self.redact(v) for k, v in headers.items()
                if k.lower().startswith("x-ratelimit-") or k.lower() in
                {"retry-after", "date", "content-type", "x-request-id"}}

    @staticmethod
    def retry_delay(headers, attempt):
        value = headers.get("retry-after")
        if value is not None:
            try:
                delay = float(value)
            except ValueError:
                try:
                    delay = parsedate_to_datetime(value).timestamp() - time.time()
                except (ValueError, TypeError, OverflowError):
                    delay = 5 * 2 ** attempt
            if not math.isfinite(delay):
                return None
            return max(0, delay)
        return 5 * 2 ** attempt

    @classmethod
    def from_env(cls, path: Path):
        values = read_settings(path)
        return cls(values.get("GROQ_API_KEY", ""), values.get("GROQ_MODEL") or DEFAULT_MODEL)

    def request(self, route: str, payload=None):
        if route not in {"models", "chat/completions"}:
            raise ProviderError("Unsupported API route")
        req = Request("https://api.groq.com/openai/v1/" + route,
                      data=None if payload is None else json.dumps(payload).encode(),
                      headers={"Authorization": "Bearer " + self._key, "Content-Type": "application/json",
                               "User-Agent": "wajo-email-agent/0.2"})
        waited = 0
        for attempt in range(self.max_retries + 1):
            start = time.monotonic()
            record = {"attempt": attempt + 1, "route": route}
            retryable = False
            headers = {}
            try:
                with build_opener(NoRedirect()).open(req, timeout=45) as response:
                    headers = self.response_headers(response.headers)
                    record.update(http_status=response.status, headers=headers)
                    raw = response.read(1_000_001)
                    if len(raw) > 1_000_000:
                        raise ProviderError("Provider response too large")
                    result = json.loads(raw)
                    record["status"] = "ok"
                    return result
            except HTTPError as exc:
                headers = self.response_headers(exc.headers)
                try:
                    raw = exc.read(65_537)
                    description = self.redact(raw[:65_536].decode("utf-8", errors="replace"))
                finally:
                    exc.close()
                record.update(status="error", http_status=exc.code, headers=headers,
                              body=description, body_truncated=len(raw) > 65_536)
                error = f"Groq HTTP {exc.code}: {description or '(empty response body)'}"
                retryable = exc.code in {408, 429, 500, 502, 503, 504}
            except (URLError, TimeoutError, OSError) as exc:
                error = f"Groq transport failure ({type(exc).__name__}); request did not produce a usable response"
                record.update(status="error", error=error)
                retryable = True
            except (ValueError, TypeError) as exc:
                error = str(exc) if isinstance(exc, ProviderError) else "Invalid Groq JSON response"
                record.update(status="error", error=error)
            finally:
                record["latency_seconds"] = round(time.monotonic() - start, 3)
                self.http_attempts.append(record)
            delay = self.retry_delay(headers, attempt)
            if (not retryable or attempt == self.max_retries or delay is None
                    or delay > 45 or waited + delay > 60):
                raise ProviderError(error) from None
            record["retry_delay_seconds"] = delay
            time.sleep(delay)
            waited += delay

    def models(self):
        return sorted(item["id"] for item in self.request("models")["data"])

    def propose(self, email: Email) -> Proposal:
        # The model does not choose the target email ID.
        content = {k: v for k, v in asdict(email).items() if k != "id"}
        if len(json.dumps(content)) > 24000:
            raise ProviderError("Email exceeds initial context limit; manual review required")
        payload = {"model": self.model, "temperature": 0, "max_completion_tokens": 1600,
                   "messages": [{"role": "system", "content": SYSTEM},
                                {"role": "user", "content": json.dumps({"untrusted_email": content}, ensure_ascii=False)}],
                   "response_format": {"type": "json_schema", "json_schema": {
                       "name": "email_proposal", "strict": True, "schema": SCHEMA}}}
        start = time.monotonic()
        record = {"model": self.model, "prompt_version": PROMPT_VERSION}
        attempts_start = len(self.http_attempts)
        try:
            result = self.request("chat/completions", payload)
            choice = result["choices"][0]
            if choice["finish_reason"] != "stop" or choice["message"].get("refusal"):
                raise ProviderError("Incomplete or refused model response")
            data = json.loads(choice["message"]["content"])
            if type(data) is not dict or set(data) != set(FIELDS):
                raise ProviderError("Model response schema mismatch")
            proposal = Proposal(**data)
            validate(proposal)
            record.update(status="ok", usage={k: v for k, v in result.get("usage", {}).items()
                                              if k in {"prompt_tokens", "completion_tokens", "total_tokens"}},
                          response_model=result.get("model"))
            return proposal
        except ProviderError as exc:
            record.update(status="error", error=str(exc))
            raise
        except (KeyError, IndexError, TypeError, ValueError):
            record.update(status="error", error="Invalid model response")
            raise ProviderError("Invalid model response") from None
        finally:
            record["latency_seconds"] = round(time.monotonic() - start, 3)
            record["http_attempts"] = self.http_attempts[attempts_start:]
            self.calls.append(record)
