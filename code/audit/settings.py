"""All settings in one place (DESIGN.md principle 11). Secrets come only from the environment.

LLM_PROVIDER, LLM_MODEL and LLM_API_KEY are read from environment variables. A local `.env` file at
the repo root (never committed) is loaded first if present; real environment variables win.
The API key is never printed: Settings hides it in its text form.
"""
import os
from dataclasses import dataclass, field

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATASET_DIR = os.path.join(REPO_ROOT, "dataset")
LLM_CACHE_DIR = os.path.join(REPO_ROOT, "code", "llm_cache")  # committed and shipped (D-003)

# Second lock next to the provider's own project settings (D-024): the code only calls these models.
ALLOWED_MODELS = {"openai": {"gpt-6-luna"}, "google": {"gemini-3.1-flash-lite"}}


def load_env_file(path, environ=None):
    """Load KEY=VALUE lines from path into environ without overriding values already set."""
    environ = os.environ if environ is None else environ
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key and key not in environ:
                environ[key] = value


@dataclass(frozen=True)
class Settings:
    provider: str
    model: str
    api_key: str = field(repr=False)
    reasoning_effort: str = "low"
    timeout_seconds: float = 120.0
    max_retries: int = 3
    requests_per_minute: int = 10
    max_output_tokens: int = 4000
    max_calls_per_run: int = 60
    max_tokens_per_run: int = 600_000

    def __str__(self):
        return f"Settings(provider={self.provider!r}, model={self.model!r}, api_key=<hidden>)"

    __repr__ = __str__


# The provider and model the saved answers in llm_cache were made with (D-025). Without settings the
# agent reads those saved answers only and makes no AI call.
DEFAULT_PROVIDER, DEFAULT_MODEL = "google", "gemini-3.1-flash-lite"


def load_settings(environ=None, env_file=os.path.join(REPO_ROOT, ".env")):
    environ = os.environ if environ is None else environ
    load_env_file(env_file, environ)
    return Settings(
        provider=environ.get("LLM_PROVIDER", "").strip().lower() or DEFAULT_PROVIDER,
        model=environ.get("LLM_MODEL", "").strip() or DEFAULT_MODEL,
        api_key=environ.get("LLM_API_KEY", "").strip(),
    )
