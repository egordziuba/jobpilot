"""Загрузка config.toml, profile/facts.toml и .env (без сторонних библиотек)."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        env[key.strip()] = value
    return env


@dataclass
class Settings:
    root: Path
    cfg: dict
    facts: dict
    env: dict = field(default_factory=dict)

    # --- удобные ссылки ---
    @property
    def run(self) -> dict:
        return self.cfg.get("run", {})

    @property
    def applicant(self) -> dict:
        return self.cfg.get("applicant", {})

    @property
    def geo(self) -> dict:
        return self.cfg.get("geo", {})

    @property
    def search(self) -> dict:
        return self.cfg.get("search", {})

    @property
    def live(self) -> bool:
        return str(self.run.get("send_mode", "dry_run")).lower() == "live"

    def secret(self, name: str, default: str = "") -> str:
        return os.environ.get(name) or self.env.get(name) or default

    @property
    def smtp_ready(self) -> bool:
        return bool(self.secret("SMTP_USER") and self.secret("SMTP_PASSWORD"))

    @property
    def data_dir(self) -> Path:
        d = self.root / "data"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def db_path(self) -> Path:
        return self.data_dir / "jobpilot.sqlite"

    @property
    def cv_path(self) -> Path:
        return self.root / self.applicant.get("cv_file", "")


def load_settings(root: Path | str) -> Settings:
    root = Path(root).resolve()
    for name, example in (("config.toml", "config.example.toml"), ("profile/facts.toml", "profile/facts.example.toml")):
        if not (root / name).exists():
            raise FileNotFoundError(f"Нет {name}. Скопируй {example} в {name} и впиши свои данные (или запусти setup.bat).")
    with open(root / "config.toml", "rb") as fh:
        cfg = tomllib.load(fh)
    with open(root / "profile" / "facts.toml", "rb") as fh:
        facts = tomllib.load(fh)
    env = load_env(root / ".env")
    return Settings(root=root, cfg=cfg, facts=facts, env=env)
