"""Config + env loading. Single place that knows about the outside world."""
import os
from dataclasses import dataclass, field

import yaml
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Competitor:
    name: str
    category: str = ""
    meta_page_ids: list[str] = field(default_factory=list)
    linkedin_slugs: list[str] = field(default_factory=list)
    is_self: bool = False


@dataclass
class Settings:
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    classifier_model: str = os.getenv("CLASSIFIER_MODEL", "claude-sonnet-5")
    apify_token: str = os.getenv("APIFY_TOKEN", "")
    slack_bot_token: str = os.getenv("SLACK_BOT_TOKEN", "")
    slack_channel: str = os.getenv("SLACK_CHANNEL", "#competitive-intel")
    db_path: str = os.getenv("DB_PATH", "data/ads.db")
    brand_name: str = os.getenv("BRAND_NAME", "Your Brand")


def load_competitors(path: str = "config/competitors.yaml"):
    with open(path) as fh:
        raw = yaml.safe_load(fh)

    defaults = raw.get("defaults", {})
    comps = []

    brand = raw.get("brand") or {}
    if brand:
        comps.append(
            Competitor(
                name=brand.get("name", "Us"),
                category="self",
                meta_page_ids=brand.get("meta_page_ids", []),
                linkedin_slugs=brand.get("linkedin_slugs", []),
                is_self=True,
            )
        )

    for c in raw.get("competitors", []):
        comps.append(
            Competitor(
                name=c["name"],
                category=c.get("category", ""),
                meta_page_ids=c.get("meta_page_ids", []),
                linkedin_slugs=c.get("linkedin_slugs", []),
            )
        )
    return comps, defaults


SETTINGS = Settings()
