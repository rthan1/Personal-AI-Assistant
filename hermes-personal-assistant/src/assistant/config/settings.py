import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_ROOT / "data"

DEFAULT_TIMEZONE = "America/New_York"


@dataclass(frozen=True)
class Settings:
    timezone: str
    google_maps_api_key: str | None
    google_web_client_path: Path
    db_path: Path
    secret_key: str
    bridge_token: str
    public_base_url: str
    bot_phone: str
    web_port: int
    bridge_port: int
    photon_project_id: str | None
    photon_project_secret: str | None
    hermes_cmd: Path | None = None
    contact_email: str | None = None
    google_site_verification: str | None = None


def _hermes_home() -> Path:
    return Path(os.getenv("HERMES_HOME") or Path(os.getenv("LOCALAPPDATA", "")) / "hermes")


def load_photon_credentials(hermes_home: Path) -> tuple[str | None, str | None]:
    """Photon project id/secret, looked up the same way Hermes does: its .env first, then auth.json."""
    env = dotenv_values(hermes_home / ".env") if (hermes_home / ".env").exists() else {}
    project_id, secret = env.get("PHOTON_PROJECT_ID"), env.get("PHOTON_PROJECT_SECRET")
    if project_id and secret:
        return project_id, secret
    try:
        auth = json.loads((hermes_home / "auth.json").read_text(encoding="utf-8-sig"))
        entry = (auth.get("credential_pool", {}).get("photon_project") or [{}])[0]
    except (OSError, ValueError, AttributeError, IndexError):
        return None, None
    return entry.get("spectrum_project_id") or None, entry.get("project_secret") or None


def load_settings() -> Settings:
    load_dotenv(PROJECT_ROOT / ".env")
    photon_project_id, photon_project_secret = load_photon_credentials(_hermes_home())
    return Settings(
        timezone=os.getenv("ASSISTANT_TIMEZONE", DEFAULT_TIMEZONE),
        google_maps_api_key=os.getenv("GOOGLE_MAPS_API_KEY") or None,
        google_web_client_path=DATA_DIR / "web_client.json",
        db_path=Path(os.getenv("ASSISTANT_DB_PATH") or DATA_DIR / "assistant.db"),
        secret_key=os.environ["ASSISTANT_SECRET_KEY"],
        bridge_token=os.environ["BRIDGE_TOKEN"],
        public_base_url=os.getenv("PUBLIC_BASE_URL", "http://localhost:8787").rstrip("/"),
        bot_phone=os.environ["BOT_PHONE"],
        web_port=int(os.getenv("WEB_PORT", "8787")),
        bridge_port=int(os.getenv("BRIDGE_PORT", "8788")),
        photon_project_id=photon_project_id,
        photon_project_secret=photon_project_secret,
        hermes_cmd=Path(os.getenv("HERMES_CMD") or _hermes_home() / "bin" / "hermes.cmd"),
        contact_email=os.getenv("CONTACT_EMAIL") or None,
        google_site_verification=os.getenv("GOOGLE_SITE_VERIFICATION") or None,
    )
