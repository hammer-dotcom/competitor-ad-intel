"""CLI entrypoint. Four verbs so you can debug each stage alone:

    python -m src.main fetch      # scrape + upsert, no LLM spend
    python -m src.main classify   # label anything unclassified
    python -m src.main report     # build PDF + Slack from stored state
    python -m src.main run        # all three, what the cron calls
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone

from src import analyze, classify, publish, store
from src.config import SETTINGS, load_competitors
from src.report import render, slack
from src.sources import linkedin, meta


def cmd_fetch(args) -> None:
    comps, defaults = load_competitors(args.config)
    conn = store.connect()
    new_total = 0

    for c in comps:
        print(f"→ {c.name}")
        ads: list[dict] = []
        try:
            ads += meta.fetch(c.meta_page_ids, defaults["countries"],
                              defaults["max_ads_per_competitor"])
        except Exception as exc:
            print(f"  ! meta failed: {exc}")
        try:
            ads += linkedin.fetch(c.linkedin_slugs, defaults["countries"],
                                  defaults["max_ads_per_competitor"],
                                  defaults["lookback_days"])
        except Exception as exc:
            print(f"  ! linkedin failed: {exc}")

        fresh = sum(store.upsert(conn, ad, c.name) for ad in ads)
        conn.commit()
        new_total += fresh
        print(f"  {len(ads)} scraped, {fresh} new")

    total = len(store.all_active(conn))
    store.log_run(conn, new_total, total)
    conn.commit()
    print(f"\n{new_total} new ads, {total} active in store")


def cmd_classify(args) -> None:
    conn = store.connect()
    rows = store.unclassified(conn)
    if not rows:
        print("nothing to classify")
        return
    print(f"classifying {len(rows)} ads")

    payload = [dict(r) for r in rows]
    results = classify.classify_rows(payload)
    for key, item in results.items():
        store.save_classification(conn, key, item)
    conn.commit()
    print(f"classified {len(results)}/{len(rows)}")


def cmd_report(args) -> None:
    comps, _ = load_competitors(args.config)
    conn = store.connect()

    cutoff = (datetime.now(timezone.utc) - timedelta(days=args.window)).isoformat()
    digest = analyze.build_digest(
        store.new_since(conn, cutoff), store.all_active(conn), args.window
    )

    synthesis = classify.synthesise(analyze.facts_for_llm(digest))
    print("\n--- the read ---\n" + synthesis + "\n")

    html = render.render_html(digest, synthesis, SETTINGS.brand_name, len(comps))
    with open("out/digest.html", "w") as fh:
        fh.write(html)

    pdf = None
    if not args.no_pdf:
        pdf = render.to_pdf(html)
        print(f"pdf → {pdf}")

    if args.slack:
        slack.post(digest, synthesis, pdf, SETTINGS.brand_name)
        print(f"posted to {SETTINGS.slack_channel}")

    if args.publish:
        _, defaults = load_competitors(args.config)
        issue = publish.publish(
            html, digest, SETTINGS.brand_name, pdf_path=pdf,
            site_dir=args.site, redact=(defaults.get("redact") or None),
        )
        print(f"published → {issue} (index at {args.site}/index.html)")


def cmd_run(args) -> None:
    cmd_fetch(args)
    cmd_classify(args)
    cmd_report(args)


def main() -> None:
    p = argparse.ArgumentParser(prog="ad-intel")
    p.add_argument("--config", default="config/competitors.yaml")
    p.add_argument("--window", type=int, default=7, help="digest lookback in days")
    p.add_argument("--slack", action="store_true")
    p.add_argument("--no-pdf", action="store_true")
    p.add_argument("--publish", action="store_true",
                   help="write the issue into site/ and rebuild the public index")
    p.add_argument("--site", default="site", help="static output directory")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in [("fetch", cmd_fetch), ("classify", cmd_classify),
                     ("report", cmd_report), ("run", cmd_run)]:
        sub.add_parser(name).set_defaults(func=fn)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
