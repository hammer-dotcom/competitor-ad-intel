"""LinkedIn Ad Library via an Apify actor.

LinkedIn's Ad Library is public and, for B2B software, usually the richer source
of the two — most of your competitors run LinkedIn and not Meta.
"""
from __future__ import annotations

from apify_client import ApifyClient
from tenacity import retry, stop_after_attempt, wait_exponential

from src.config import SETTINGS

ACTOR = "dz_omar/linkedin-ads-scraper"  # alt: s-r/linkedin-ads-library


def _library_url(slug: str, country: str, days: int) -> str:
    window = "LAST_30_DAYS" if days <= 30 else "LAST_YEAR"
    return (
        "https://www.linkedin.com/ad-library/search"
        f"?companyIds=&accountOwner={slug}&countries={country}&dateOption={window}"
    )


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=5, max=60))
def fetch(slugs: list[str], countries: list[str], max_ads: int = 60,
          lookback_days: int = 30) -> list[dict]:
    if not slugs:
        return []

    client = ApifyClient(SETTINGS.apify_token)
    run = client.actor(ACTOR).call(
        run_input={
            "searches": [
                {"keyword": slug, "country": c}
                for slug in slugs for c in countries
            ],
            "urls": [{"url": _library_url(s, c, lookback_days)}
                     for s in slugs for c in countries],
            "maxResults": max_ads,
            "fetchAdDetail": True,
        },
        timeout_secs=600,
    )

    items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
    return [normalise(i) for i in items]


def normalise(raw: dict) -> dict:
    return {
        "platform": "linkedin",
        "ad_id": str(raw.get("adId") or raw.get("id") or raw.get("adUrl", "")),
        "advertiser": raw.get("advertiserName") or raw.get("companyName") or "",
        "headline": raw.get("headline") or raw.get("title") or "",
        "body": raw.get("adText") or raw.get("commentary") or raw.get("description") or "",
        "cta_text": raw.get("ctaText") or raw.get("callToAction") or "",
        "landing_url": raw.get("destinationUrl") or raw.get("externalLink") or "",
        "first_seen": raw.get("firstImpressionAt") or raw.get("startDate"),
        "last_seen": raw.get("lastImpressionAt") or raw.get("endDate"),
        "is_active": bool(raw.get("isActive", True)),
        "media": [{"type": raw.get("creativeType", "image"), "url": u}
                  for u in (raw.get("imageUrls") or []) if u],
        "permalink": raw.get("adUrl") or "",
        "raw": raw,
    }
