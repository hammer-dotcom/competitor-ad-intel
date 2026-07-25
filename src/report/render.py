"""HTML -> branded PDF. WeasyPrint keeps it dependency-light and headless-friendly."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATES = Path(__file__).parent / "templates"


def _bullets_to_html(text: str) -> str:
    items = [l.lstrip("-•* ").strip() for l in text.splitlines() if l.strip()]
    if not items:
        return "<p>No material change this window.</p>"
    return "<ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>"


def render_html(digest: dict, synthesis: str, brand: str, competitor_count: int) -> str:
    env = Environment(
        loader=FileSystemLoader(TEMPLATES),
        autoescape=select_autoescape(["html"]),
    )
    env.filters["min"] = min
    tmpl = env.get_template("digest.html")
    return tmpl.render(
        digest=digest,
        synthesis_html=_bullets_to_html(synthesis),
        brand=brand,
        competitor_count=competitor_count,
    )


def to_pdf(html: str, out_dir: str = "out") -> str:
    from weasyprint import HTML

    Path(out_dir).mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d")
    path = f"{out_dir}/ad-intel-{stamp}.pdf"
    HTML(string=html, base_url=str(TEMPLATES)).write_pdf(path)
    return path
