"""Runtime settings, read once from the environment (and an optional .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("DATA_DIR", ROOT / "data")).resolve())
    llm_provider: str = field(default_factory=lambda: os.environ.get("LLM_PROVIDER", "gemini"))  # gemini | openrouter
    gemini_api_key: str = field(default_factory=lambda: os.environ.get("GEMINI_API_KEY", ""))
    gemini_model: str = field(default_factory=lambda: os.environ.get("GEMINI_MODEL", "gemini-3.5-flash"))
    # Gemini 3 thinking level: low keeps extraction fast; set "" to use the model default.
    gemini_thinking: str = field(default_factory=lambda: os.environ.get("GEMINI_THINKING", "low"))
    # Client-side cap on model calls per minute (0 = none). Free-tier keys allow about 5.
    gemini_rpm: float = field(default_factory=lambda: _float("GEMINI_RPM", 0))
    # OpenRouter (OpenAI-compatible); used when LLM_PROVIDER=openrouter.
    openrouter_api_key: str = field(default_factory=lambda: os.environ.get("OPENROUTER_API_KEY", ""))
    openrouter_model: str = field(default_factory=lambda: os.environ.get("OPENROUTER_MODEL", "deepseek/deepseek-v4-flash"))
    # Reasoning effort (low/medium/high); "" leaves the model default.
    openrouter_reasoning: str = field(default_factory=lambda: os.environ.get("OPENROUTER_REASONING", ""))
    openrouter_rpm: float = field(default_factory=lambda: _float("OPENROUTER_RPM", 0))
    llm_timeout_s: float = field(default_factory=lambda: _float("LLM_TIMEOUT_S", 300))
    # Extraction pipeline: "v1" baseline, "v2" improved (spreadsheet series read by code).
    extract_version: str = field(default_factory=lambda: os.environ.get("EXTRACT_VERSION", "v2"))

    worker_threads: int = field(default_factory=lambda: _int("WORKER_THREADS", 3))
    job_max_attempts: int = field(default_factory=lambda: _int("JOB_MAX_ATTEMPTS", 4))
    # Rate-limit deferrals do not use up attempts, but are capped so an exhausted quota still fails.
    job_max_deferrals: int = field(default_factory=lambda: _int("JOB_MAX_DEFERRALS", 20))
    job_lease_s: float = field(default_factory=lambda: _float("JOB_LEASE_S", 120))
    retry_base_s: float = field(default_factory=lambda: _float("RETRY_BASE_S", 5))
    retry_max_s: float = field(default_factory=lambda: _float("RETRY_MAX_S", 300))
    poll_interval_s: float = field(default_factory=lambda: _float("WORKER_POLL_S", 1.0))
    max_upload_mb: int = field(default_factory=lambda: _int("MAX_UPLOAD_MB", 25))

    @property
    def model_name(self) -> str:
        model = self.openrouter_model if self.llm_provider == "openrouter" else self.gemini_model
        return f"{self.llm_provider}:{model}"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "kuki.db"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"


def get_settings() -> Settings:
    return Settings()
