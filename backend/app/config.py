from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT / ".env"),
        extra="ignore",
    )

    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    whisper_model: str = "large-v3"
    data_dir: Path = ROOT / "data"
    host: str = "127.0.0.1"
    port: int = 8741
    watermark: str = ""
    default_facecam: str = "top_right"


settings = Settings()
data_dir = Path(settings.data_dir)
if not data_dir.is_absolute():
    data_dir = ROOT / data_dir
settings.data_dir = data_dir.resolve()
JOBS_DIR = settings.data_dir / "jobs"
JOBS_DIR.mkdir(parents=True, exist_ok=True)
