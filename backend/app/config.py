import logging
import platform
import sys
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]

log = logging.getLogger(__name__)

_SECRET_SUFFIXES = ("_api_key", "_token", "_secret_key", "_password")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(REPO_ROOT / ".env"), extra="ignore")

    environment: str = "dev"

    database_url: str = "postgresql+psycopg://vision24:vision24@localhost:5435/vision24"

    minio_endpoint: str = "localhost:9000"
    minio_public_endpoint: str = ""
    minio_public_secure: bool = False
    minio_region: str = "us-east-1"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "clips"

    go2rtc_base_url: str = "http://localhost:1984"

    ai_provider: str = "openai"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash"
    openai_api_key: str = ""
    openai_model: str = "gpt-5.1"
    openrouter_api_key: str = ""
    openrouter_model: str = "qwen/qwen3.7-flash"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_reasoning: bool = False
    live_ai_provider: str = ""
    live_ai_model: str = ""
    tts_enabled: bool = True
    tts_model: str = "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-4bit"
    tts_voice: str = ""
    tts_max_chars: int = 400
    tts_warmup: bool = True
    chat_max_tool_rounds: int = 6
    chat_max_history: int = 24
    chat_thinking_budget: int = 0
    chat_max_sessions: int = 200

    auth_secret_key: str = ""
    access_token_ttl_minutes: int = 30
    refresh_token_ttl_days: int = 30
    cookie_secure: bool = False
    cookie_samesite: str = "lax"
    cookie_domain: str = ""
    allow_public_registration: bool = False

    allow_reset: bool = True

    worker_fps: int = 12
    yolo_device: str = "mps"
    yolo_model: str = "yolo11s.pt"
    yolo_imgsz: int = 960
    yolo_conf: float = 0.3
    yolo_half: bool = True
    yolo_tracker: str = ""
    min_track_age_s: float = 0.8
    motion_gate_enabled: bool = True
    motion_min_ratio: float = 0.003
    motion_pixel_delta: int = 25
    render_workers: int = 6
    clip_enabled: bool = True
    clip_model: str = "ViT-B-32"
    clip_pretrained: str = "laion2b_s34b_b79k"
    delivery_enabled: bool = True
    delivery_model: str = "yolov8s-worldv2.pt"
    delivery_conf: float = 0.02
    delivery_prompts: str = (
        "cardboard box,plastic crate,bottle crate,tray of cans,sack,plastic bag,package"
    )
    delivery_keyframes: int = 5
    delivery_match_threshold: float = 0.50
    delivery_margin: float = 0.03
    delivery_vlm_verify: bool = True
    delivery_vlm_max_trips: int = 10
    pos_match_window_s: int = 30
    pos_min_presence_s: int = 20
    pos_vlm_verify: bool = True
    pos_vlm_clear_confidence: float = 0.7
    savings_avg_check: int = 120_000
    savings_value_per_flagged_txn: int = 150_000
    savings_after_hours_value: int = 500_000
    savings_package_value: int = 80_000
    subscription_price_month: int = 1_500_000

    video_source: str = ""
    replay_source: str = ""
    replay_rtsp_base: str = "rtsp://127.0.0.1:8554"
    replay_target: str = "rtsp://127.0.0.1:8554/cam1"

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    data_dir: str = ""

    log_level: str = "INFO"
    log_format: str = "text"
    log_dir: str = ""

    cors_origins: str = (
        "http://localhost:3000,http://127.0.0.1:3000,http://localhost:3001,http://127.0.0.1:3001"
    )

    def __repr_args__(self):
        for name, value in super().__repr_args__():
            if name and value and any(name.endswith(s) for s in _SECRET_SUFFIXES):
                yield name, "***redacted***"
            else:
                yield name, value

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir) if self.data_dir else REPO_ROOT

    @property
    def media_path(self) -> Path:
        return self.data_path / "media"

    @property
    def log_path(self) -> Path:
        return Path(self.log_dir) if self.log_dir else self.data_path / "logs"

    @property
    def ai_degraded(self) -> bool:
        return not {
            "openai": self.openai_api_key,
            "gemini": self.gemini_api_key,
            "openrouter": self.openrouter_api_key,
        }.get(self.ai_provider, "")

    @model_validator(mode="after")
    def _normalize(self):
        providers = {"openai", "gemini", "openrouter"}
        if self.ai_provider not in providers:
            raise ValueError(f"AI_PROVIDER={self.ai_provider!r} is not one of {sorted(providers)}")
        if self.live_ai_provider and self.live_ai_provider not in providers:
            raise ValueError(
                f"LIVE_AI_PROVIDER={self.live_ai_provider!r} is not one of {sorted(providers)}"
            )
        if not self.database_url.startswith("postgresql"):
            raise ValueError(
                "DATABASE_URL must be a PostgreSQL URL — the schema uses JSONB and pgvector."
            )

        apple_silicon = sys.platform == "darwin" and platform.machine() == "arm64"
        if self.tts_enabled and not apple_silicon:
            object.__setattr__(self, "tts_enabled", False)
        if self.yolo_device == "mps" and sys.platform != "darwin":
            object.__setattr__(self, "yolo_device", "cpu")
        return self

    def startup_report(self) -> str:
        db = self.database_url.rsplit("@", 1)[-1]
        lines = [
            f"environment : {self.environment}",
            f"database    : {db}",
            f"storage     : {self.minio_endpoint}/{self.minio_bucket}",
            f"ai provider : {self.ai_provider} ({self.ai_model_name})"
            + ("  [DEGRADED — no API key, keyword fallback in use]" if self.ai_degraded else ""),
            f"live ai     : {self.live_ai_provider or self.ai_provider}"
            f" ({self.live_ai_model or 'default'})",
            f"tts         : {'on' if self.tts_enabled else 'off (needs Apple Silicon)'}",
            f"detector    : {self.yolo_model} on {self.yolo_device} @ {self.worker_fps}fps",
            f"motion gate : {'on' if self.motion_gate_enabled else 'off'}",
            f"logs        : {self.log_path}",
            f"cors        : {', '.join(self.cors_origin_list)}",
        ]
        return "Vision 24 configuration\n  " + "\n  ".join(lines)

    @property
    def ai_model_name(self) -> str:
        return {
            "openai": self.openai_model,
            "gemini": self.gemini_model,
            "openrouter": self.openrouter_model,
        }.get(self.ai_provider, "?")


settings = Settings()
