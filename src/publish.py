"""Publish layer: turn a rendered digest into a static site anyone can open.

This is what makes the agent shareable. `out/` is a scratch directory; `site/` is a
deployable artifact — a stable index plus every past issue at a permanent URL. Push
`site/` to GitHub Pages, Vercel, Netlify, or an S3 bucket; it needs no runtime.

Redaction matters if the repo is public and the competitor set is a client's. Pass a
map and every occurrence of a real name is swapped before anything is written, so the
published HTML never contains it.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATES = Path(__file__).parent / "report" / "templates"
MANIFEST = "manifest.json"


def _apply_redaction(text: str, redact: dict[str, str] | None) -> str:
    if not redact:
        return text
    for real, alias in redact.items():
        text = text.replace(real, alias)
    return text


def _load_manifest(site_dir: Path) -> list[dict]:
    path = site_dir / MANIFEST
    if not path.exists():
        return []
    return json.loads(path.read_text())


def publish(
    html: str,
    digest: dict,
    brand: str,
    pdf_path: str | None = None,
    site_dir: str = "site",
    redact: dict[str, str] | None = None,
    demo: bool = False,
) -> str:
    """Write one issue into the site and rebuild the index. Returns the issue path."""
    site = Path(site_dir)
    (site / "issues").mkdir(parents=True, exist_ok=True)

    stamp = digest["generated_at"].strftime("%Y-%m-%d")
    slug = f"{stamp}{'-demo' if demo else ''}"

    html = _apply_redaction(html, redact)
    issue_path = site / "issues" / f"{slug}.html"
    issue_path.write_text(html)

    pdf_rel = None
    if pdf_path and Path(pdf_path).exists():
        pdf_rel = f"issues/{slug}.pdf"
        shutil.copy(pdf_path, site / pdf_rel)

    entry = {
        "slug": slug,
        "date": stamp,
        "html": f"issues/{slug}.html",
        "pdf": pdf_rel,
        "new_count": digest["new_count"],
        "active_count": digest["active_count"],
        "advertisers": sorted(
            _apply_redaction(c, redact) for c in digest["angle_mix"].keys()
        ),
        "top_mover": _apply_redaction(
            digest["velocity"][0][0] if digest["velocity"] else "—", redact
        ),
        "demo": demo,
        "published_at": datetime.now(timezone.utc).isoformat(),
    }

    manifest = [e for e in _load_manifest(site) if e["slug"] != slug]
    manifest.append(entry)
    manifest.sort(key=lambda e: e["date"], reverse=True)
    (site / MANIFEST).write_text(json.dumps(manifest, indent=2))

    _write_index(site, manifest, _apply_redaction(brand, redact), demo)
    return str(issue_path)


def _write_index(site: Path, manifest: list[dict], brand: str, demo: bool) -> None:
    env = Environment(
        loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"])
    )
    tmpl = env.get_template("index.html")
    latest = manifest[0] if manifest else None
    site.joinpath("index.html").write_text(
        tmpl.render(
            brand=brand,
            issues=manifest,
            latest=latest,
            demo=demo or all(e.get("demo") for e in manifest),
            advertiser_count=len(latest["advertisers"]) if latest else 0,
            updated=datetime.now(timezone.utc).strftime("%d %b %Y"),
        )
    )
    # Stops GitHub Pages from running the output through Jekyll.
    site.joinpath(".nojekyll").write_text("")
