"""Local model selection, credentials and provider construction.

Non-secret selection state lives in SQLite. User API keys live in a separate,
git-ignored file with owner-only permissions and are never returned by public
settings serialization.
"""

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from .groq_provider import GroqProposer, ProviderError
from .openai_provider import OpenAIProposer


BUNDLED_GROQ = "bundled_groq"
USER_GROQ = "user_groq"
USER_OPENAI = "user_openai"
MODEL_MODES = (BUNDLED_GROQ, USER_GROQ, USER_OPENAI)
DEFAULT_CONCURRENCY = 3
MAX_USER_CONCURRENCY = 12


@dataclass(frozen=True)
class ModelSettings:
    mode: str = BUNDLED_GROQ
    concurrency: int = DEFAULT_CONCURRENCY

    def public_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class KeyValidation:
    ok: bool
    error: str = ""


def _validated(mode: str, concurrency: int) -> ModelSettings:
    if mode not in MODEL_MODES:
        raise ValueError("Invalid model mode")
    if type(concurrency) is not int:
        raise ValueError("Concurrency must be an integer")
    if mode == BUNDLED_GROQ:
        if concurrency != DEFAULT_CONCURRENCY:
            raise ValueError("Bundled Groq concurrency is fixed at 3")
    elif not DEFAULT_CONCURRENCY <= concurrency <= MAX_USER_CONCURRENCY:
        raise ValueError("User-provider concurrency must be between 3 and 12")
    return ModelSettings(mode, concurrency)


def validate_model_settings(mode: str, concurrency: int) -> ModelSettings:
    """Validate a prospective selection without persisting it."""
    return _validated(mode, concurrency)


def initialize_model_settings(db: sqlite3.Connection) -> None:
    db.execute(
        """CREATE TABLE IF NOT EXISTS model_settings (
               singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
               mode TEXT NOT NULL,
               concurrency INTEGER NOT NULL,
               updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
           )"""
    )
    db.execute(
        """INSERT OR IGNORE INTO model_settings(singleton, mode, concurrency)
           VALUES(1, ?, ?)""",
        (BUNDLED_GROQ, DEFAULT_CONCURRENCY),
    )
    db.commit()


def load_model_settings(db: sqlite3.Connection) -> ModelSettings:
    initialize_model_settings(db)
    row = db.execute("SELECT mode, concurrency FROM model_settings WHERE singleton=1").fetchone()
    try:
        return _validated(row[0], row[1])
    except (ValueError, TypeError):
        # A corrupt local setting must fail closed to the fixed bundled default.
        return ModelSettings()


def save_model_settings(db: sqlite3.Connection, mode: str, concurrency: int) -> ModelSettings:
    settings = _validated(mode, concurrency)
    initialize_model_settings(db)
    db.execute(
        """UPDATE model_settings SET mode=?, concurrency=?, updated_at=CURRENT_TIMESTAMP
           WHERE singleton=1""",
        (settings.mode, settings.concurrency),
    )
    db.commit()
    return settings


class CredentialStore:
    """Owner-readable local JSON credential file; values are never enumerable."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def _read(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError, TypeError):
            raise ProviderError("Local model credentials could not be read") from None
        if type(data) is not dict:
            raise ProviderError("Local model credentials are invalid")
        return {mode: value for mode, value in data.items()
                if mode in {USER_GROQ, USER_OPENAI} and type(value) is str}

    def has_key(self, mode: str) -> bool:
        return bool(self.get_key(mode))

    def get_key(self, mode: str) -> str:
        if mode not in {USER_GROQ, USER_OPENAI}:
            raise ValueError("Bundled credentials are managed by Mailward")
        return self._read().get(mode, "")

    def set_key(self, mode: str, api_key: str) -> None:
        if mode not in {USER_GROQ, USER_OPENAI}:
            raise ValueError("Bundled credentials are managed by Mailward")
        if not api_key or any(character.isspace() for character in api_key):
            raise ValueError("Enter a valid API key")
        values = self._read()
        values[mode] = api_key
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary_name = tempfile.mkstemp(prefix=".model-credentials-", dir=self.path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w") as handle:
                fd = -1
                json.dump(values, handle, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, self.path)
            os.chmod(self.path, 0o600)
        finally:
            if fd >= 0:
                os.close(fd)
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass

    def delete_key(self, mode: str) -> None:
        if mode not in {USER_GROQ, USER_OPENAI}:
            raise ValueError("Bundled credentials are managed by Mailward")
        values = self._read()
        if mode not in values:
            return
        del values[mode]
        if values:
            # Reuse the atomic/permission-preserving write path.
            remaining_mode, remaining_key = next(iter(values.items()))
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
            self.set_key(remaining_mode, remaining_key)
        else:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass


def create_provider(
    settings: ModelSettings,
    credentials: CredentialStore,
    bundled_env_path: Path,
):
    """Build the selected provider without changing persisted selection state."""
    settings = _validated(settings.mode, settings.concurrency)
    if settings.mode == BUNDLED_GROQ:
        return GroqProposer.from_env(Path(bundled_env_path))
    key = credentials.get_key(settings.mode)
    if not key:
        raise ProviderError("Enter and verify an API key before applying this model mode")
    if settings.mode == USER_GROQ:
        return GroqProposer(key, serialize_calls=False)
    return OpenAIProposer(key)


def validate_user_key(mode: str, api_key: str) -> KeyValidation:
    """Validate a user key and return only a sanitized, UI-safe result."""
    if mode not in {USER_GROQ, USER_OPENAI}:
        return KeyValidation(False, "Bundled Groq does not accept a user API key")
    try:
        provider = GroqProposer(api_key, max_retries=0, serialize_calls=False) if mode == USER_GROQ else OpenAIProposer(
            api_key, max_retries=0
        )
        available = provider.models()
        if provider.model not in available:
            return KeyValidation(False, "The required model is not available for this API key")
        return KeyValidation(True)
    except (ProviderError, ValueError) as exc:
        # Provider adapters redact the exact supplied key and credential-looking
        # bearer values before their errors cross this boundary.
        message = str(exc).replace(api_key, "[REDACTED_KEY]") if api_key else str(exc)
        return KeyValidation(False, message[:1000])
