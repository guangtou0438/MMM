import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
DISPLAY_TIMEZONE = "Australia/Melbourne"


def llm_settings():
    from .llm.settings import load_llm_config
    return load_llm_config()


@dataclass(frozen=True)
class Settings:
    database_path: Path
    llm_base_url: str
    llm_model: str

    @classmethod
    def from_env(cls):
        path = Path(os.getenv("CALENDAR_DB_PATH", "data/calendar.db"))
        return cls(path if path.is_absolute() else ROOT / path,
                   os.getenv("LLM_BASE_URL", ""), os.getenv("LLM_MODEL", ""))
