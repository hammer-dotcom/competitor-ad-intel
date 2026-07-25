"""Aggregation: turn classified rows into the numbers the digest reports."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone


def _parse(dt: str | None):
    if not dt:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
        try:
            return datetime.strptime(dt, fmt)
        except (ValueError, TypeError):
            continue
    return None


def days_live(row) -> int | None:
    start = _parse(row["first_seen"]) or _parse(row["first_ingested"])
    if not start:
        return None
    end = _parse(row["last_seen"]) if not row["is_active"] else datetime.now(timezone.utc)
    if not end:
        end = datetime.now(timezone.utc)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    return max((end - start).days, 0)


def enrich(rows) -> list[dict]:
    out = []
    for r in rows:
        d = dict(r)
        d["classification"] = json.loads(r["classification"]) if r["classification"] else {}
        d["media"] = json.loads(r["media_json"] or "[]")
        d["days_live"] = days_live(r)
        out.append(d)
    return out


def build_digest(new_rows, active_rows, lookback_days: int = 7) -> dict:
    new_ads = enrich(new_rows)
    active = enrich(active_rows)

    by_comp = defaultdict(list)
    for a in new_ads:
        by_comp[a["competitor"]].append(a)

    angle_mix = defaultdict(Counter)
    offer_mix = defaultdict(Counter)
    for a in active:
        c = a["classification"]
        if c:
            angle_mix[a["competitor"]][c.get("angle", "other")] += 1
            offer_mix[a["competitor"]][c.get("offer", "other")] += 1

    # An ad that has been live a long time is usually an ad that works.
    proven = sorted(
        [a for a in active if (a["days_live"] or 0) >= 21 and a["classification"]],
        key=lambda a: a["days_live"], reverse=True,
    )[:10]

    # Sharp increase in volume from one advertiser = a push worth knowing about.
    velocity = Counter(a["competitor"] for a in new_ads)

    return {
        "generated_at": datetime.now(timezone.utc),
        "window_days": lookback_days,
        "new_count": len(new_ads),
        "active_count": len(active),
        "by_competitor": dict(by_comp),
        "angle_mix": {k: dict(v) for k, v in angle_mix.items()},
        "offer_mix": {k: dict(v) for k, v in offer_mix.items()},
        "proven": proven,
        "velocity": velocity.most_common(),
        "all_new": new_ads,
    }


def facts_for_llm(digest: dict) -> str:
    """Compact, factual dump the synthesis pass reasons over."""
    lines = [
        f"Window: last {digest['window_days']} days.",
        f"New ads found: {digest['new_count']}. Total active tracked: {digest['active_count']}.",
        "",
        "New ads per competitor: "
        + ", ".join(f"{k}={v}" for k, v in digest["velocity"]) or "none",
        "",
        "Angle mix across all active ads:",
    ]
    for comp, mix in digest["angle_mix"].items():
        lines.append(f"  {comp}: " + ", ".join(f"{k} x{v}" for k, v in
                     sorted(mix.items(), key=lambda kv: -kv[1])))
    lines += ["", "Longest-running ads (proxy for what is working):"]
    for a in digest["proven"][:8]:
        c = a["classification"]
        lines.append(
            f"  {a['competitor']} [{a['platform']}] {a['days_live']}d live — "
            f"angle={c.get('angle')} offer={c.get('offer')} hook=\"{c.get('hook')}\""
        )
    lines += ["", "New this window:"]
    for a in digest["all_new"][:25]:
        c = a["classification"]
        lines.append(
            f"  {a['competitor']} [{a['platform']}] angle={c.get('angle')} "
            f"offer={c.get('offer')} stage={c.get('funnel_stage')} "
            f"hook=\"{c.get('hook')}\" notable=\"{c.get('notable')}\""
        )
    return "\n".join(lines)
