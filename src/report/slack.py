"""Slack delivery: a readable digest in-channel plus the PDF as a thread file."""
from __future__ import annotations

from slack_sdk import WebClient

from src.config import SETTINGS


def _blocks(digest: dict, synthesis: str, brand: str) -> list[dict]:
    head = (
        f"*Competitor ad intel · {digest['generated_at']:%d %b}*\n"
        f"{digest['new_count']} new creatives · {digest['active_count']} live and tracked"
    )
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": head}},
        {"type": "section", "text": {"type": "mrkdwn", "text": synthesis[:2900] or "_Quiet week._"}},
        {"type": "divider"},
    ]

    for a in digest["all_new"][:6]:
        c = a["classification"]
        line = (
            f"*{a['competitor']}* · `{c.get('angle','?')}` · `{c.get('offer','?')}`\n"
            f">{c.get('hook','')}\n"
            f"{c.get('notable','')}"
        )
        block = {"type": "section", "text": {"type": "mrkdwn", "text": line[:2900]}}
        if a.get("permalink"):
            block["accessory"] = {
                "type": "button",
                "text": {"type": "plain_text", "text": "View ad"},
                "url": a["permalink"],
            }
        blocks.append(block)

    return blocks


def post(digest: dict, synthesis: str, pdf_path: str | None, brand: str) -> None:
    client = WebClient(token=SETTINGS.slack_bot_token)
    res = client.chat_postMessage(
        channel=SETTINGS.slack_channel,
        text=f"Competitor ad intel — {digest['new_count']} new creatives",
        blocks=_blocks(digest, synthesis, brand),
    )
    if pdf_path:
        client.files_upload_v2(
            channel=SETTINGS.slack_channel,
            thread_ts=res["ts"],
            file=pdf_path,
            title="Full digest (PDF)",
            initial_comment="Full breakdown, including angle mix by advertiser.",
        )
