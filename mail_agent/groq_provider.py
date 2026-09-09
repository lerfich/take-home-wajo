"""Groq adapter. Credentials never enter prompts, reports, or exception text."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPRedirectHandler

from .core import Email, Proposal, validate, PATTERNS

DEFAULT_MODEL = "qwen/qwen3.8-27b"
PROMPT_VERSION = "triage-v4"
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
    def __init__(self, api_key: str, model: str = DEFAULT_MODEL):
        if not api_key or any(c.isspace() for c in api_key):
            raise ProviderError("Set a valid GROQ_API_KEY in task/.env or the environment")
        self._key = api_key
        self.model = model
        self.calls = []

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
        try:
            with build_opener(NoRedirect()).open(req, timeout=45) as response:
                raw = response.read(1_000_001)
                if len(raw) > 1_000_000:
                    raise ProviderError("Provider response too large")
                return json.loads(raw)
        except HTTPError as exc:
            # Never include response bodies or request headers in an exception.
            raise ProviderError(f"Groq HTTP {exc.code}; check access, model or quota; no automatic fallback") from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise ProviderError("Groq connection or response failure") from None

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
            self.calls.append(record)
