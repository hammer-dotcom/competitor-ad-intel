"""MCP server for the Competitor Ad Intel agent.

Design decision worth understanding before reading the code:

    The connected Claude IS the classifier.

In the cron version, `src/classify.py` calls the Anthropic API to label ads — the pipeline
needs its own key and pays per token. Over MCP that's wasteful and worse: the model is
already sitting on the other end of the connection. So this server does not classify. It
hands Claude the raw ads plus the taxonomy (`get_ads_to_classify`), Claude labels them in
the conversation where you can see and correct every label, and writes them back
(`save_classifications`). The server needs no ANTHROPIC_API_KEY at all — only an Apify
token for scraping, and optionally a Slack token for delivery.

That also means the human is in the loop for the one step that most needs it, and the
whole thing is auditable in chat rather than buried in a log.

Run locally (stdio, what Claude Desktop uses):
    python -m src.mcp_server

Run as a remote connector (streamable HTTP, what claude.ai uses):
    python -m src.mcp_server --http --port 8000
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field

from src import analyze, publish, store
from src.taxonomy import ANGLES, FUNNEL, OFFERS
from src.config import SETTINGS, load_competitors

CONFIG_PATH = os.getenv("ADINTEL_CONFIG", "config/competitors.yaml")

mcp = FastMCP(
    "competitor-ad-intel",
    instructions=(
        "Competitive ad intelligence over the public Meta and LinkedIn ad libraries.\n\n"
        "Normal loop: scan_ads → get_ads_to_classify → (you label them) → "
        "save_classifications → get_digest → build_report.\n\n"
        "You are the classifier here. When get_ads_to_classify returns ads, label each one "
        "against the taxonomy it gives you and call save_classifications with the results. "
        "Label only what the ad copy actually says; never infer from what you know about "
        "the company. Most ads are unremarkable — say so rather than manufacture insight."
    ),
)


# --------------------------------------------------------------------------- models


class Classification(BaseModel):
    """One labelled ad. Field meanings match the taxonomy resource."""

    key: str = Field(description="The ad key exactly as returned by get_ads_to_classify.")
    hook: str = Field(description="The opening promise in 12 words or fewer, in the ad's own register.")
    angle: Literal[tuple(ANGLES)] = Field(description="The persuasive angle.")  # type: ignore[valid-type]
    offer: Literal[tuple(OFFERS)] = Field(description="What the ad asks for.")  # type: ignore[valid-type]
    funnel_stage: Literal[tuple(FUNNEL)] = Field(description="Buying-stage the ad targets.")  # type: ignore[valid-type]
    audience: str = Field(description="Who it speaks to, e.g. 'local authority grant managers'.")
    value_props: list[str] = Field(default_factory=list, description="Up to 3 concrete claims made.")
    proof_used: list[str] = Field(
        default_factory=list,
        description="Only what literally appears: named customers, numbers, certifications.",
    )
    tone: str = Field(default="", description="One or two words.")
    notable: str = Field(
        description="One sentence a competing marketer should take from this, or "
        "'nothing notable' — which is the honest answer for most ads."
    )


# --------------------------------------------------------------------------- helpers


def _load_yaml() -> dict:
    return yaml.safe_load(Path(CONFIG_PATH).read_text())


def _compact(row) -> dict:
    """Trim an ad row down to what a model needs to label it. Saves a lot of context."""
    return {
        "key": row["key"],
        "advertiser": row["competitor"],
        "platform": row["platform"],
        "headline": row["headline"] or "",
        "body": (row["body"] or "")[:900],
        "cta": row["cta_text"] or "",
        "landing_url": row["landing_url"] or "",
        "first_seen": row["first_seen"],
    }


# --------------------------------------------------------------------------- tools


@mcp.tool()
def list_tracked_advertisers() -> dict[str, Any]:
    """List the advertisers currently tracked, and which ad libraries each is watched on.

    Call this first when the user asks what is being monitored, or before scanning, so you
    can tell them which competitors have no configured source yet.
    """
    comps, defaults = load_competitors(CONFIG_PATH)
    return {
        "countries": defaults.get("countries", []),
        "lookback_days": defaults.get("lookback_days", 30),
        "advertisers": [
            {
                "name": c.name,
                "category": c.category,
                "is_you": c.is_self,
                "linkedin": c.linkedin_slugs,
                "meta": c.meta_page_ids,
                "configured": bool(c.linkedin_slugs or c.meta_page_ids),
            }
            for c in comps
        ],
    }


@mcp.tool()
def add_advertiser(
    name: Annotated[str, Field(description="Display name, e.g. 'Notion'.")],
    linkedin_slug: Annotated[str, Field(description="Tail of linkedin.com/company/<slug>. Empty if they don't advertise there.")] = "",
    meta_page_id: Annotated[str, Field(description="view_all_page_id from the Meta Ad Library URL. Empty if none.")] = "",
    category: Annotated[str, Field(description="Free-text grouping, e.g. 'project management'.")] = "",
) -> str:
    """Add an advertiser to the tracked set, writing it into the config file.

    Use this when the user names a competitor that list_tracked_advertisers doesn't show.
    If you don't know their LinkedIn slug or Meta page ID, ask rather than guess — a wrong
    slug returns an empty scan that looks like "they aren't advertising", which is a
    genuinely misleading answer.
    """
    raw = _load_yaml()
    existing = {c["name"].lower() for c in raw.get("competitors", [])}
    if name.lower() in existing:
        return f"{name} is already tracked."
    if not (linkedin_slug or meta_page_id):
        return "Refused: need at least a LinkedIn slug or a Meta page ID, otherwise scans return nothing."

    raw.setdefault("competitors", []).append(
        {
            "name": name,
            "linkedin_slugs": [linkedin_slug] if linkedin_slug else [],
            "meta_page_ids": [meta_page_id] if meta_page_id else [],
            "category": category,
        }
    )
    Path(CONFIG_PATH).write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True))
    return f"Added {name}. Run scan_ads to collect their creative."


@mcp.tool()
async def scan_ads(
    ctx: Context,
    advertisers: Annotated[list[str], Field(description="Names to scan. Empty list means every tracked advertiser.")] = [],
    platforms: Annotated[list[Literal["meta", "linkedin"]], Field(description="Which libraries to read.")] = ["meta", "linkedin"],
    countries: Annotated[list[str], Field(description="ISO country codes. Empty uses the config default.")] = [],
    max_ads_per_advertiser: Annotated[int, Field(ge=1, le=200, description="Cap per advertiser per platform. Each ad costs a fraction of a cent.")] = 40,
) -> dict[str, Any]:
    """Read the public ad libraries for the tracked advertisers and store what's found.

    This is the only tool that costs money (Apify credit, roughly $0.001 per ad) and the
    only slow one — expect 30 to 120 seconds per advertiser. Deduplication happens on
    write, so scanning twice in a day is safe and cheap in results, not in credit.

    Returns per-advertiser counts and how many ads are newly seen. Follow with
    get_ads_to_classify.
    """
    if not SETTINGS.apify_token:
        return {"error": "APIFY_TOKEN is not set. Add it to the server's environment and restart."}

    from src.sources import linkedin as li_src
    from src.sources import meta as meta_src

    comps, defaults = load_competitors(CONFIG_PATH)
    wanted = {a.lower() for a in advertisers}
    if wanted:
        comps = [c for c in comps if c.name.lower() in wanted]
    if not comps:
        return {"error": "No matching advertisers. Call list_tracked_advertisers first."}

    ctries = countries or defaults.get("countries", ["US"])
    conn = store.connect()
    results, new_total, errors = [], 0, []

    for i, c in enumerate(comps):
        await ctx.report_progress(i, len(comps), f"Reading {c.name}")
        ads: list[dict] = []
        if "meta" in platforms and c.meta_page_ids:
            try:
                ads += meta_src.fetch(c.meta_page_ids, ctries, max_ads_per_advertiser)
            except Exception as exc:
                errors.append(f"{c.name}/meta: {exc}")
        if "linkedin" in platforms and c.linkedin_slugs:
            try:
                ads += li_src.fetch(c.linkedin_slugs, ctries, max_ads_per_advertiser,
                                    defaults.get("lookback_days", 30))
            except Exception as exc:
                errors.append(f"{c.name}/linkedin: {exc}")

        fresh = sum(store.upsert(conn, ad, c.name) for ad in ads)
        conn.commit()
        new_total += fresh
        results.append({"advertiser": c.name, "scraped": len(ads), "new": fresh})

    store.log_run(conn, new_total, len(store.all_active(conn)), "mcp scan")
    conn.commit()

    return {
        "new_ads": new_total,
        "unclassified_waiting": len(store.unclassified(conn)),
        "per_advertiser": results,
        "errors": errors,
        "next": "Call get_ads_to_classify to label the new creative." if new_total
                else "Nothing new since the last scan.",
    }


@mcp.tool()
def get_ads_to_classify(
    limit: Annotated[int, Field(ge=1, le=40, description="How many ads to return. Batches of 10-15 keep labelling consistent.")] = 12,
) -> dict[str, Any]:
    """Fetch unlabelled ads together with the taxonomy to label them against.

    You are the classifier. For every ad returned, produce one Classification and pass the
    whole list to save_classifications.

    Rules that keep the dataset trendable:
      - Use only the enum values given. Do not invent angles or offers.
      - Judge the copy that is present; library text is often truncated.
      - 'hook' must be grounded in the real wording, not a punchier rewrite.
      - 'proof_used' only if it literally appears in the ad.
      - Be conservative with 'notable'. Most ads warrant "nothing notable".
    """
    conn = store.connect()
    rows = store.unclassified(conn)
    return {
        "remaining": len(rows),
        "taxonomy": {"angle": ANGLES, "offer": OFFERS, "funnel_stage": FUNNEL},
        "ads": [_compact(r) for r in rows[:limit]],
    }


@mcp.tool()
def save_classifications(
    classifications: Annotated[list[Classification], Field(description="One entry per ad from get_ads_to_classify.")],
) -> dict[str, Any]:
    """Store your labels. Call get_ads_to_classify again if any remain."""
    conn = store.connect()
    saved = 0
    for c in classifications:
        payload = c.model_dump()
        payload["ref"] = payload.pop("key")
        store.save_classification(conn, c.key, payload)
        saved += 1
    conn.commit()
    remaining = len(store.unclassified(conn))
    return {
        "saved": saved,
        "remaining_unclassified": remaining,
        "next": "Call get_ads_to_classify again." if remaining else "All labelled. Call get_digest.",
    }


@mcp.tool()
def get_digest(
    window_days: Annotated[int, Field(ge=1, le=365, description="How far back 'new' means.")] = 7,
) -> dict[str, Any]:
    """Aggregate the stored, labelled ads into the findings for a digest.

    Returns the numbers, not the narrative: new-ad counts per advertiser, angle mix per
    advertiser across all live creative, and the longest-running ads.

    Read 'proven' carefully — an ad live 60 days is the closest thing to a public
    performance signal, because nobody keeps paying for a creative that doesn't convert.
    Write the narrative yourself from these facts, then pass your bullets to build_report.
    """
    conn = store.connect()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()
    digest = analyze.build_digest(store.new_since(conn, cutoff),
                                 store.all_active(conn), window_days)

    def slim(a: dict) -> dict:
        c = a.get("classification") or {}
        return {
            "advertiser": a["competitor"], "platform": a["platform"],
            "days_live": a["days_live"], "hook": c.get("hook"),
            "angle": c.get("angle"), "offer": c.get("offer"),
            "funnel_stage": c.get("funnel_stage"), "audience": c.get("audience"),
            "proof_used": c.get("proof_used"), "notable": c.get("notable"),
            "permalink": a.get("permalink"),
        }

    return {
        "window_days": window_days,
        "new_ads": digest["new_count"],
        "live_and_tracked": digest["active_count"],
        "new_per_advertiser": dict(digest["velocity"]),
        "angle_mix_by_advertiser": digest["angle_mix"],
        "offer_mix_by_advertiser": digest["offer_mix"],
        "proven_long_running": [slim(a) for a in digest["proven"]],
        "new_this_window": [slim(a) for a in digest["all_new"][:30]],
        "next": "Write 3-5 specific bullets, then call build_report with them.",
    }


@mcp.tool()
def build_report(
    synthesis_bullets: Annotated[list[str], Field(description="Your 3-5 findings. Each must name a specific advertiser and a specific observation. No generic marketing advice.")],
    window_days: Annotated[int, Field(ge=1, le=365)] = 7,
    formats: Annotated[list[Literal["pdf", "html", "site"]], Field(description="'site' also updates the shareable archive index.")] = ["pdf"],
) -> dict[str, Any]:
    """Render the branded digest. Returns file paths on the machine running this server.

    Tell the user where the files landed; you cannot attach them yourself.
    """
    from src.report import render

    conn = store.connect()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()
    digest = analyze.build_digest(store.new_since(conn, cutoff),
                                 store.all_active(conn), window_days)
    synthesis = "\n".join(f"- {b.lstrip('-• ')}" for b in synthesis_bullets)

    html = render.render_html(digest, synthesis, SETTINGS.brand_name, len(digest["angle_mix"]))
    out: dict[str, Any] = {"advertisers": len(digest["angle_mix"]), "new_ads": digest["new_count"]}

    Path("out").mkdir(exist_ok=True)
    if "html" in formats or "site" in formats:
        Path("out/digest.html").write_text(html)
        out["html"] = str(Path("out/digest.html").resolve())

    pdf = None
    if "pdf" in formats:
        try:
            pdf = render.to_pdf(html)
            out["pdf"] = str(Path(pdf).resolve())
        except OSError as exc:
            out["pdf_error"] = (
                f"WeasyPrint could not render ({exc}). Its system libraries are missing — "
                "on Debian: apt install libpango-1.0-0 libpangoft2-1.0-0 libcairo2 "
                "libgdk-pixbuf-2.0-0. The HTML is still available."
            )

    if "site" in formats:
        _, defaults = load_competitors(CONFIG_PATH)
        issue = publish.publish(html, digest, SETTINGS.brand_name, pdf_path=pdf,
                                redact=defaults.get("redact") or None)
        out["site_issue"] = str(Path(issue).resolve())
        out["site_index"] = str(Path("site/index.html").resolve())

    return out


@mcp.tool()
def post_digest_to_slack(
    synthesis_bullets: Annotated[list[str], Field(description="The same bullets you'd put in the report.")],
    window_days: Annotated[int, Field(ge=1, le=365)] = 7,
    attach_pdf: Annotated[bool, Field(description="Upload the PDF into the message thread.")] = True,
) -> str:
    """Post the digest to the configured Slack channel.

    This is visible to everyone in that channel, so confirm with the user before calling it.
    """
    if not SETTINGS.slack_bot_token:
        return "SLACK_BOT_TOKEN is not set on the server, so nothing was posted."

    from src.report import render, slack

    conn = store.connect()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()
    digest = analyze.build_digest(store.new_since(conn, cutoff),
                                 store.all_active(conn), window_days)
    synthesis = "\n".join(f"• {b.lstrip('-• ')}" for b in synthesis_bullets)

    pdf = None
    if attach_pdf:
        try:
            pdf = render.to_pdf(render.render_html(
                digest, synthesis, SETTINGS.brand_name, len(digest["angle_mix"])))
        except OSError:
            pdf = None

    slack.post(digest, synthesis, pdf, SETTINGS.brand_name)
    return f"Posted to {SETTINGS.slack_channel}{' with the PDF attached' if pdf else ''}."


# ----------------------------------------------------------------------- resources


@mcp.resource("adintel://taxonomy", mime_type="application/json")
def taxonomy() -> str:
    """The fixed label set. Enum-constrained so labels can be trended, not just read."""
    return json.dumps({"angle": ANGLES, "offer": OFFERS, "funnel_stage": FUNNEL}, indent=2)


@mcp.resource("adintel://config", mime_type="text/yaml")
def config_file() -> str:
    """The current tracked-advertiser configuration."""
    return Path(CONFIG_PATH).read_text()


@mcp.resource("adintel://digest/latest", mime_type="text/html")
def latest_digest() -> str:
    """The most recently rendered digest document."""
    p = Path("out/digest.html")
    return p.read_text() if p.exists() else "<p>No digest rendered yet. Run build_report.</p>"


# ------------------------------------------------------------------------- prompts


@mcp.prompt(title="Run the weekly ad intel digest")
def weekly_digest(window_days: str = "7") -> str:
    """The whole loop in one command: scan, label, analyse, render."""
    return (
        f"Run this week's competitor ad intel digest over a {window_days}-day window.\n\n"
        "1. list_tracked_advertisers, and tell me if any lack a configured source.\n"
        "2. scan_ads across every tracked advertiser.\n"
        "3. get_ads_to_classify and label each ad yourself, in batches, until none remain. "
        "Show me the labels as you go so I can correct you.\n"
        "4. get_digest, then tell me the 3-5 things that actually matter. Name advertisers "
        "and cite specifics. If it was a quiet week, say so — don't pad it.\n"
        "5. Once I've approved the bullets, build_report with them.\n\n"
        "Don't post to Slack unless I ask."
    )


@mcp.prompt(title="Find the gap in the market's messaging")
def messaging_gap() -> str:
    """Positioning analysis from the stored angle mix."""
    return (
        "Call get_digest with a 90-day window and look only at angle_mix_by_advertiser.\n\n"
        "Tell me: which angles is the whole category crowding into, which are unclaimed, "
        "and where my own row sits relative to everyone else. Then say which unclaimed "
        "angle is actually worth taking versus which is empty because it doesn't work. "
        "Distinguish the two — an empty column is not automatically an opportunity."
    )


# ---------------------------------------------------------------------------- main


def main() -> None:
    ap = argparse.ArgumentParser(description="Competitor Ad Intel MCP server")
    ap.add_argument("--http", action="store_true",
                    help="serve over streamable HTTP instead of stdio (for remote connectors)")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=int(os.getenv("PORT", 8000)))
    args = ap.parse_args()

    if args.http:
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
