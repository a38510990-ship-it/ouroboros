"""Channel Monitor — follow public Telegram channels via the /s/ web preview.

No Playwright or Telegram client needed. Uses stdlib urllib + BeautifulSoup4.
Last-seen message IDs are persisted on Drive so reruns skip already-seen posts.
"""

from __future__ import annotations

import json
import os
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

# ─── constants ────────────────────────────────────────────────────────────────

DEFAULT_CHANNEL = "abulaphia"
DRIVE_ROOT = Path(os.environ.get("DRIVE_ROOT", "/content/drive/MyDrive/Ouroboros"))
STATE_DIR = DRIVE_ROOT / "memory" / "channel_monitor"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


# ─── helpers ──────────────────────────────────────────────────────────────────


def _state_path(channel: str) -> Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    return STATE_DIR / f"{channel}.json"


def _load_state(channel: str) -> dict:
    p = _state_path(channel)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {"last_id": 0}


def _save_state(channel: str, state: dict) -> None:
    _state_path(channel).write_text(json.dumps(state, indent=2))


def _fetch_html(channel: str) -> str:
    url = f"https://t.me/s/{channel}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _parse_messages(html: str) -> list[dict]:
    """Return list of message dicts sorted oldest-first."""
    soup = BeautifulSoup(html, "html.parser")
    results: list[dict] = []

    for wrap in soup.find_all("div", class_="tgme_widget_message_wrap"):
        post_div = wrap.find("div", attrs={"data-post": True})
        if not post_div:
            continue

        data_post = post_div.get("data-post", "")
        try:
            msg_id = int(data_post.split("/")[-1])
        except ValueError:
            continue

        # Text (may be absent for media-only posts)
        text_div = wrap.find("div", class_="tgme_widget_message_text")
        text = text_div.get_text(separator="\n", strip=True) if text_div else ""

        # Datetime
        time_el = wrap.find("time")
        dt_str = time_el.get("datetime", "") if time_el else ""

        # Permalink
        link_el = wrap.find("a", class_="tgme_widget_message_date")
        link = link_el.get("href", f"https://t.me/{data_post}") if link_el else f"https://t.me/{data_post}"

        # Media flag
        has_photo = bool(wrap.find("a", class_="tgme_widget_message_photo_wrap"))
        has_video = bool(wrap.find("video"))

        results.append(
            {
                "id": msg_id,
                "text": text,
                "datetime": dt_str,
                "link": link,
                "has_photo": has_photo,
                "has_video": has_video,
            }
        )

    # Sort by ID ascending (oldest first)
    results.sort(key=lambda m: m["id"])
    return results


def _format_message(msg: dict) -> str:
    lines = [f"📨 #{msg['id']} — {msg['datetime']}", f"🔗 {msg['link']}"]
    media_tags = []
    if msg["has_photo"]:
        media_tags.append("📷 photo")
    if msg["has_video"]:
        media_tags.append("🎥 video")
    if media_tags:
        lines.append(" ".join(media_tags))
    if msg["text"]:
        # Truncate very long posts
        body = msg["text"]
        if len(body) > 600:
            body = body[:600] + "…"
        lines.append(body)
    return "\n".join(lines)


# ─── main tool function ───────────────────────────────────────────────────────


def check_channel(channel: str = DEFAULT_CHANNEL, mark_read: bool = True) -> dict[str, Any]:
    """Fetch the latest messages from a public Telegram channel web preview.

    Reads https://t.me/s/{channel}, returns any messages newer than the
    last-seen ID.  Persists the high-water mark on Drive.

    Args:
        channel: Telegram channel username (without @), e.g. "abulaphia".
        mark_read: If True (default), advance the last-seen pointer so the
                   next call only returns newer posts.

    Returns:
        {
            "channel": str,
            "new_count": int,
            "last_id": int,
            "messages": [{"id", "text", "datetime", "link", "has_photo", "has_video"}, ...],
            "summary": str,   # human-readable digest
            "error": str | None,
        }
    """
    try:
        html = _fetch_html(channel)
    except urllib.error.URLError as exc:
        return {
            "channel": channel,
            "new_count": 0,
            "last_id": 0,
            "messages": [],
            "summary": "",
            "error": f"Network error: {exc}",
        }

    all_messages = _parse_messages(html)
    if not all_messages:
        return {
            "channel": channel,
            "new_count": 0,
            "last_id": 0,
            "messages": [],
            "summary": "No messages found on channel preview page.",
            "error": None,
        }

    state = _load_state(channel)
    last_id = state.get("last_id", 0)

    new_messages = [m for m in all_messages if m["id"] > last_id]
    new_max_id = max(m["id"] for m in all_messages)

    if mark_read and new_messages:
        _save_state(channel, {"last_id": new_max_id, "updated_at": datetime.utcnow().isoformat()})

    if new_messages:
        formatted = "\n\n---\n\n".join(_format_message(m) for m in new_messages)
        summary = f"{len(new_messages)} new post(s) in @{channel}:\n\n{formatted}"
    else:
        summary = f"No new posts in @{channel} since #{last_id}."

    return {
        "channel": channel,
        "new_count": len(new_messages),
        "last_id": new_max_id,
        "messages": new_messages,
        "summary": summary,
        "error": None,
    }


# ─── tool registration ────────────────────────────────────────────────────────


def get_tools() -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": "check_channel",
                "description": (
                    "Fetch new messages from a public Telegram channel web preview "
                    "(https://t.me/s/{channel}). Returns posts newer than the last-seen ID. "
                    "Persists state on Drive so repeated calls only return new content. "
                    "Default channel is 'abulaphia' (the owner's channel)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "channel": {
                            "type": "string",
                            "description": "Telegram channel username (without @). Default: 'abulaphia'.",
                        },
                        "mark_read": {
                            "type": "boolean",
                            "description": "Advance last-seen pointer after reading. Default: true.",
                        },
                    },
                    "required": [],
                },
            },
        }
    ]
