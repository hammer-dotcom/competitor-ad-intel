"""LLM classification. The bit that turns a pile of ad copy into a taxonomy.

Design notes that matter more than the code:
  * A fixed taxonomy beats free-text labels. Free text gives you 200 unique
    "angles" and nothing to trend. Enums give you a chart.
  * Classify in batches of ~8 ads per call: cheaper, and the model calibrates
    labels against neighbours instead of drifting ad to ad.
  * Force JSON with a tool schema, not a prompt instruction.
"""
from __future__ import annotations

import json

import anthropic
from tenacity import retry, stop_after_attempt, wait_exponential

from src.config import SETTINGS

from src.taxonomy import ANGLES, FUNNEL, OFFERS  # noqa: E402

SCHEMA = {
    "name": "classify_ads",
    "description": "Return one structured classification per ad, in input order.",
    "input_schema": {
        "type": "object",
        "properties": {
            "ads": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "ref": {"type": "string", "description": "The ref given in the input."},
                        "hook": {
                            "type": "string",
                            "description": "The opening promise in <=12 words, in the ad's own register.",
                        },
                        "angle": {"type": "string", "enum": ANGLES},
                        "offer": {"type": "string", "enum": OFFERS},
                        "funnel_stage": {"type": "string", "enum": FUNNEL},
                        "audience": {
                            "type": "string",
                            "description": "Who it speaks to, e.g. 'local authority grant managers'.",
                        },
                        "value_props": {
                            "type": "array", "items": {"type": "string"},
                            "description": "Up to 3 concrete claims made.",
                        },
                        "proof_used": {
                            "type": "array", "items": {"type": "string"},
                            "description": "Named customers, numbers, certifications, awards.",
                        },
                        "tone": {"type": "string"},
                        "notable": {
                            "type": "string",
                            "description": "One sentence on what a competing marketer should take from this. Say 'nothing notable' if so.",
                        },
                    },
                    "required": ["ref", "hook", "angle", "offer", "funnel_stage",
                                 "audience", "value_props", "proof_used", "tone", "notable"],
                },
            }
        },
        "required": ["ads"],
    },
}

SYSTEM = """You are a paid-media strategist doing competitive creative analysis for a B2B SaaS team.
You read raw ad copy and label it against a fixed taxonomy so it can be trended over time.

Rules:
- Label what the ad actually says, never what you assume the company does.
- Copy is often truncated or has boilerplate. Judge on the substance that is present.
- 'hook' must be grounded in the real wording. Do not invent a punchier version.
- Only list proof_used that literally appears (a named customer, a number, a certification).
- Be conservative with 'notable'. Most ads are unremarkable; say so rather than manufacture insight."""


def _batch_prompt(rows: list[dict]) -> str:
    blocks = []
    for r in rows:
        blocks.append(
            f"<ad ref=\"{r['key']}\">\n"
            f"advertiser: {r['advertiser']}\n"
            f"platform: {r['platform']}\n"
            f"headline: {r['headline'] or '(none)'}\n"
            f"body: {(r['body'] or '(none)')[:1200]}\n"
            f"cta: {r['cta_text'] or '(none)'}\n"
            f"landing_url: {r['landing_url'] or '(none)'}\n"
            "</ad>"
        )
    return "Classify every ad below.\n\n" + "\n\n".join(blocks)


@retry(stop=stop_after_attempt(4), wait=wait_exponential(min=4, max=45))
def _call(client: anthropic.Anthropic, rows: list[dict]) -> list[dict]:
    resp = client.messages.create(
        model=SETTINGS.classifier_model,
        max_tokens=4000,
        system=SYSTEM,
        tools=[SCHEMA],
        tool_choice={"type": "tool", "name": "classify_ads"},
        messages=[{"role": "user", "content": _batch_prompt(rows)}],
    )
    for block in resp.content:
        if block.type == "tool_use":
            return block.input["ads"]
    raise RuntimeError("Model returned no tool_use block")


def classify_rows(rows: list[dict], batch_size: int = 8) -> dict[str, dict]:
    """rows: dicts with key/advertiser/platform/headline/body/cta_text/landing_url."""
    client = anthropic.Anthropic(api_key=SETTINGS.anthropic_api_key)
    out: dict[str, dict] = {}

    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        try:
            for item in _call(client, batch):
                out[item["ref"]] = item
        except Exception as exc:  # one bad batch must not kill the run
            print(f"  ! batch {i // batch_size} failed: {exc}")
    return out


def synthesise(digest_facts: str) -> str:
    """Second, smaller LLM pass: the editorial 'so what' at the top of the digest."""
    client = anthropic.Anthropic(api_key=SETTINGS.anthropic_api_key)
    resp = client.messages.create(
        model=SETTINGS.classifier_model,
        max_tokens=900,
        system=(
            "You write the opening read for a weekly competitive ad digest sent to a "
            "B2B demand-gen team. Three to five short bullets. Each bullet must cite a "
            "specific competitor and a specific observation from the data given. No "
            "generic marketing advice, no filler, no restating the numbers back. If the "
            "week is quiet, say the week is quiet."
        ),
        messages=[{"role": "user", "content": digest_facts}],
    )
    return "\n".join(b.text for b in resp.content if b.type == "text").strip()
