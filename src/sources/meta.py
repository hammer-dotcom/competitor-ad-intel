"""Meta (Facebook/Instagram) Ad Library via an Apify actor.

Why an actor and not the official Graph API: Meta's Ad Library API only exposes
ads about social issues, elections and politics. Commercial SaaS ads are visible
in the web Ad Library but not in that API, so a scraper is the only route for
competitive creative intel. Keep the request volume modest and respect the
platform's terms — this reads a public transparency surface, nothing gated.
"""
from __future__ import annotations

from apify_client import ApifyClient
from tenacity import retry, stop_after_attempt, wait_exponential

from src.config import SETTINGS

# Cheap, high-volume, 99%+ success rate. Alternatives that take the same shape of
# input: constructive_calm/facebook-ad-library-pro, automly/facebook-ad-library-scraper
ACTOR = "curious_coder/facebook-ads-library-scraper"


def _library_url(page_id: str, country: str) -> str:
    return (
        "https://www.facebook.com/ads/library/"
        f"?active_status=all&ad_type=all&country={country}"
        f"&view_all_page_id={page_id}&sort_data[direction]=desc"
        "&sort_data[mode]=relevancy_monthly_grouped"
    )


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=5, max=60))
def fetch(page_ids: list[str], countries: list[str], max_ads: int = 60) -> list[dict]:
    if not page_ids:
        return []

    client = ApifyClient(SETTINGS.apify_token)
    urls = [{"url": _library_url(pid, c)} for pid in page_ids for c in countries]

    run = client.actor(ACTOR).call(
        run_input={
            "urls": urls,
            "count": max_ads,
            "limitPerSource": max_ads,
            "scrapeAdDetails": True,
            "scrapePageAds.activeStatus": "all",
        },
        timeout_secs=600,
    )

    items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
    return [normalise(i) for i in items]


def normalise(raw: dict) -> dict:
    """Flatten one actor record into the shared ad schema."""
    snap = raw.get("snapshot") or {}
    body = snap.get("body") or {}
    cards = snap.get("cards") or []

    creative_text = body.get("text") or raw.get("ad_creative_body") or ""
    if not creative_text and cards:
        creative_text = cards[0].get("body") or ""

    media = []
    for img in (snap.get("images") or []):
        if img.get("original_image_url"):
            media.append({"type": "image", "url": img["original_image_url"]})
    for vid in (snap.get("videos") or []):
        if vid.get("video_preview_image_url"):
            media.append({"type": "video", "url": vid["video_preview_image_url"]})

    return {
        "platform": "meta",
        "ad_id": str(raw.get("ad_archive_id") or raw.get("adArchiveID") or raw.get("id") or ""),
        "advertiser": raw.get("page_name") or snap.get("page_name") or "",
        "headline": snap.get("title") or (cards[0].get("title") if cards else "") or "",
        "body": creative_text,
        "cta_text": snap.get("cta_text") or (cards[0].get("cta_text") if cards else "") or "",
        "landing_url": snap.get("link_url") or (cards[0].get("link_url") if cards else "") or "",
        "first_seen": raw.get("start_date") or raw.get("startDate"),
        "last_seen": raw.get("end_date") or raw.get("endDate"),
        "is_active": bool(raw.get("is_active", True)),
        "media": media,
        "permalink": f"https://www.facebook.com/ads/library/?id={raw.get('ad_archive_id', '')}",
        "raw": raw,
    }
