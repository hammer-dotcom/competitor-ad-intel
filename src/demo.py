"""Offline demo run: fixtures -> classified rows -> PDF -> published site.

No API keys, no scraping, no LLM spend. This is what a public deployment runs, and
what you should use while iterating on the document design.

    python -m src.demo            # build PDF + site/ from fixtures
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src import analyze, publish, store
from src.report import render

FIXTURES = Path("demo/fixtures.json")


def seed() -> dict:
    data = json.loads(FIXTURES.read_text())
    conn = store.connect()
    now = datetime.now(timezone.utc)

    for i, f in enumerate(data["ads"]):
        key = f"{f['platform']}:demo{i}"
        store.upsert(
            conn,
            {
                "platform": f["platform"],
                "ad_id": f"demo{i}",
                "advertiser": f["competitor"],
                "headline": f["hook"],
                "body": f["hook"] + ". " + " ".join(f["value_props"])
                        + " Request a walkthrough with our team.",
                "cta_text": "Learn more",
                "landing_url": "https://example.com",
                "first_seen": (now - timedelta(days=f["days"])).strftime("%Y-%m-%d"),
                "last_seen": None,
                "is_active": True,
                "media": [],
                "permalink": "https://www.linkedin.com/ad-library/",
            },
            f["competitor"],
        )
        store.save_classification(conn, key, {
            "ref": key, "hook": f["hook"], "angle": f["angle"], "offer": f["offer"],
            "funnel_stage": f["funnel_stage"], "audience": f["audience"],
            "value_props": f["value_props"], "proof_used": f["proof_used"],
            "tone": f["tone"], "notable": f["notable"],
        })
    conn.commit()
    return data


def main() -> None:
    data = seed()
    conn = store.connect()
    digest = analyze.build_digest(
        store.new_since(conn, "1970-01-01"), store.all_active(conn), 7
    )
    synthesis = "\n".join(f"- {b}" for b in data["synthesis"])
    brand = data["brand"]

    html = render.render_html(digest, synthesis, brand, len(digest["angle_mix"]))
    Path("out").mkdir(exist_ok=True)
    Path("out/digest.html").write_text(html)
    pdf = render.to_pdf(html)
    print(f"pdf  → {pdf}")

    issue = publish.publish(html, digest, brand, pdf_path=pdf, demo=True)
    print(f"site → site/index.html\nissue → {issue}")


if __name__ == "__main__":
    main()
