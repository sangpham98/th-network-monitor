import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


async def send_telegram(message: str) -> bool:
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        logger.debug("telegram send skipped because bot token or chat id is missing")
        return False

    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    payload = {
        "chat_id": settings.telegram_chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    used_fallback = False

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            response = await client.post(url, json=payload)
            if response.status_code == 400:
                used_fallback = True
                logger.warning("telegram rejected HTML payload; retrying without parse_mode")
                payload.pop("parse_mode", None)
                response = await client.post(url, json=payload)
            if not response.is_success:
                logger.warning(
                    "telegram send failed status=%s fallback=%s body=%r",
                    response.status_code,
                    used_fallback,
                    response.text[:500],
                )
            return response.is_success
        except httpx.HTTPError:
            logger.exception("telegram send failed due to httpx error")
            return False
