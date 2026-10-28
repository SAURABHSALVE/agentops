import os
from dataclasses import dataclass

APP_VERSION = "0.1.0"


@dataclass(frozen=True)
class Settings:
    cors_allow_origins: tuple[str, ...] = ("http://localhost:3000",)

    @classmethod
    def from_env(cls) -> "Settings":
        raw = os.environ.get("CORS_ALLOW_ORIGINS", "")
        origins = tuple(o.strip() for o in raw.split(",") if o.strip())
        return cls(cors_allow_origins=origins) if origins else cls()
