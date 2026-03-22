"""Channel Monitor tool — follow a public Telegram channel via web preview.

Fetches https://t.me/s/{channel} (Telegram's public HTML preview), parses
messages, and surfaces only posts newer than the last-seen message ID.

State is persisted on Drive at:
    memory/channel_monitor/{channel}.json
    → {"last_id": <int>, "channel": <str>, "updated_at": <iso8601>}

No Telegram credentials required — the /s/ preview is fully public.
BeautifulSoup4 + requests are used for parsing (both pre-installed in Colab).
"""

from __future__ import annotations

import json
import logging
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STATE_DIR = "memory/channel_monitor"
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
_DEFAULT_CHANNEL = "abulaphia"


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _strip_html(html: str) -> str:
    """Remove HTML tags and decode common entities."""
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    # Common HTML entities
    entities = {
        "&amp;": "&", "&lt;": "<", "&gt;": ">",
        "&quot;": '"', "&#039;": "'", "&nbsp;": " ",
        "&#36;": "$", "&#036;": "$",
    }
    for ent, ch in entities.items():
        text = text.replace(ent, ch)
    text = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _fetch_html(channel: str) -> str:
    """Fetch the public Telegram channel preview page."""
    url = f"https://t.me/s/{channel}"
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _parse_messages(html: str, channel: str) -> List[Dict[str, Any]]:
    """Parse all visible messages from the channel preview HTML.

    Returns list of dicts (newest last, matching page order):
        id       : int — Telegram message number
        text     : str — plain text content (empty for media-only posts)
        date     : str — ISO-8601 datetime string from <time datetime="...">
        url      : str — direct link to the message
        has_media: bool — True if post contains an image/video/document
    """
    messages: List[Dict[str, Any]] = []

    # Each post has data-post="channel/N"
    pattern = re.compile(
        rf'data-post="{re.escape(channel)}/(\d+)".*?'
        r'(?:class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>)?'
        r'.*?'
        r'(?:<time[^>]*datetime="([^"]*)"[^>]*>)?',
        re.DOTALL,
    )

    # More reliable: split on per-message wrapper
    blocks = re.split(r'class="tgme_widget_message_wrap', html)

    for block in blocks[1:]:  # skip preamble
        # ID
        id_match = re.search(rf'data-post="{re.escape(channel)}/(\d+)"', block)
        if not id_match:
            continue
        msg_id = int(id_match.group(1))

        # Text content
        text_match = re.search(
            r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>',
            block,
            re.DOTALL,
        )
        raw_text = text_match.group(1) if text_match else ""
        text = _strip_html(raw_text) if raw_text else ""

        # Date
        date_match = re.search(r'<time[^>]+datetime="([^"]+)"', block)
        date_str = date_match.group(1) if date_match else ""

        # Media indicator
        has_media = bool(
            re.search(r'tgme_widget_message_photo|tgme_widget_message_video|tgme_widget_message_document', block)
        )

        messages.append({
            "id": msg_id,
            "text": text,
            "date": date_str,
            "url": f"https://t.me/{channel}/{msg_id}",
            "has_media": has_media,
        })

    # Sort by ID ascending
    messages.sort(key=lambda m: m["id"])
    return messages


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def _state_path(drive_root: Path, channel: str) -> Path:
    state_dir = drive_root / STATE_DIR
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir / f"{channel}.json"


def _load_state(drive_root: Path, channel: str) -> Dict[str, Any]:
    path = _state_path(drive_root, channel)
    if path.exists():
        try:
            return json.loads(path.read_text("utf-8"))
        except Exception:
            pass
    return {"last_id": 0, "channel": channel}


def _save_state(drive_root: Path, channel: str, last_id: int) -> None:
    path = _state_path(drive_root, channel)
    state = {
        "last_id": last_id,
        "channel": channel,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), "utf-8")


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def check_channel(
    drive_root: Path,
    channel: str = _DEFAULT_CHANNEL,
    mark_read: bool = True,
    limit: int = 5,
) -> str:
    """Fetch new posts from a public Telegram channel and return a summary.

    Args:
        drive_root: Path to Drive root (ctx.drive_path('') parent).
        channel: Telegram channel username (without @).
        mark_read: If True, update last_id so the same posts won't appear again.
        limit: Maximum number of new posts to return in this call.

    Returns:
        Human-readable string with new post summaries, or "No new posts."
    """
    state = _load_state(drive_root, channel)
    last_id: int = state.get("last_id", 0)

    try:
        html = _fetch_html(channel)
    except Exception as e:
        return f"⚠️ Failed to fetch channel '{channel}': {e}"

    messages = _parse_messages(html, channel)
    if not messages:
        return f"⚠️ No messages parsed from channel '{channel}'. Channel may be private or username incorrect."

    # Filter new messages
    new_messages = [m for m in messages if m["id"] > last_id]

    if not new_messages:
        latest = messages[-1]["id"]
        return f"No new posts in @{channel} (last seen: #{last_id}, latest on page: #{latest})."

    # Apply limit from most recent
    displayed = new_messages[-limit:]
    skipped = len(new_messages) - len(displayed)

    lines: List[str] = [
        f"📬 **{len(new_messages)} new post{'s' if len(new_messages) != 1 else ''} in @{channel}**"
        + (f" (showing last {limit})" if skipped else "")
    ]

    for msg in displayed:
        header = f"\n**#{msg['id']}** · {msg['date']}"
        link = f"  🔗 {msg['url']}"
        if msg["text"]:
            # Truncate long posts
            body = msg["text"]
            if len(body) > 600:
                body = body[:597] + "…"
            lines.append(f"{header}\n{body}\n{link}")
        else:
            media_note = "📷 [media post]" if msg["has_media"] else "[no text]"
            lines.append(f"{header}\n{media_note}\n{link}")

    if mark_read:
        new_last_id = new_messages[-1]["id"]
        _save_state(drive_root, channel, new_last_id)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool integration
# ---------------------------------------------------------------------------

def _tool_check_channel(ctx, channel: str = _DEFAULT_CHANNEL, mark_read: bool = True, limit: int = 5) -> str:
    """Tool wrapper — resolves drive_root from ctx."""
    drive_root = ctx.drive_path("").parent if hasattr(ctx, "drive_path") else Path("/content/drive/MyDrive/Ouroboros")
    # ctx.drive_path("memory") → …/Ouroboros/memory, so parent → …/Ouroboros
    try:
        drive_root = ctx.drive_path("memory").parent
    except Exception:
        drive_root = Path("/content/drive/MyDrive/Ouroboros")
    return check_channel(drive_root=drive_root, channel=channel, mark_read=mark_read, limit=limit)


def get_tools():
    from ouroboros.tools.registry import ToolEntry
    return [
        ToolEntry(
            "check_channel",
            {
                "name": "check_channel",
                "description": (
                    "Fetch new posts from a public Telegram channel's web preview. "
                    "Tracks last-seen message ID on Drive so each post is shown only once. "
                    "Default channel: abulaphia (owner's channel)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "channel": {
                            "type": "string",
                            "description": "Telegram channel username without @. Default: 'abulaphia'.",
                        },
                        "mark_read": {
                            "type": "boolean",
                            "description": "Update last-seen ID so posts aren't repeated. Default: true.",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max number of new posts to return. Default: 5.",
                        },
                    },
                    "required": [],
                },
            },
            _tool_check_channel,
        )
    ]
