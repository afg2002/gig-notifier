"""
Telegram Bot API helpers — async HTTP wrappers.
"""
import json
import logging
from typing import Optional
from urllib.request import Request, urlopen

from core.config import TELEGRAM_BOT_TOKEN, TG_API

logger = logging.getLogger(__name__)


async def tg_request(token: str, method: str, payload: dict) -> dict:
    """Make a request to the Telegram Bot API."""
    import httpx

    url = f"{TG_API}{token}/{method}"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, json=payload)
        data = resp.json()
        if not data.get("ok"):
            logger.error(f"Telegram API error [{method}]: {data}")
        return data


async def send_message(
    token: str,
    chat_id: str,
    text: str,
    reply_markup: Optional[dict] = None,
    parse_mode: str = "HTML",
) -> dict:
    """Send a Telegram message."""
    payload: dict = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return await tg_request(token, "sendMessage", payload)


async def broadcast(
    token: str,
    chat_ids: list[str],
    text: str,
    reply_markup: Optional[dict] = None,
    parse_mode: str = "HTML",
) -> dict:
    """Send a message to multiple chat IDs. Returns last result."""
    result = {}
    for cid in chat_ids:
        result = await send_message(token, cid, text, reply_markup, parse_mode)
    return result


async def edit_message(
    token: str,
    chat_id: str,
    message_id: int,
    text: str,
    reply_markup: Optional[dict] = None,
    parse_mode: str = "HTML",
) -> dict:
    """Edit an existing Telegram message."""
    payload: dict = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return await tg_request(token, "editMessageText", payload)


async def answer_callback(token: str, callback_query_id: str, text: str = "") -> dict:
    """Answer a callback query (removes loading spinner)."""
    payload: dict = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    return await tg_request(token, "answerCallbackQuery", payload)
