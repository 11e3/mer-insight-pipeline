"""
텔레그램 발송 모듈.
tier1: 모든 메시지 (단일 채널)
"""

import logging
import asyncio

from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import TelegramError

from config.settings import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_TIER1_CHAT_ID,
)
from src.delivery.formatters import format_message
from src.pipeline.event_types import EventType

log = logging.getLogger(__name__)

CHANNEL_MAP = {
    "tier1": TELEGRAM_TIER1_CHAT_ID,
}

MAX_MESSAGE_LEN = 4096  # 텔레그램 메시지 최대 길이


class TelegramBot:

    def __init__(self):
        if TELEGRAM_BOT_TOKEN:
            self.bot = Bot(token=TELEGRAM_BOT_TOKEN)
        else:
            self.bot = None
            log.warning("TELEGRAM_BOT_TOKEN 미설정 — 텔레그램 발송 비활성화")

    async def send_analysis(
        self,
        channel: str,
        event: dict,
        analysis: str,
        rules_count: int = 0,
    ) -> bool:
        if not self.bot:
            log.info(f"[텔레그램 비활성화] {event.get('title', '')[:40]}")
            return False

        chat_id = CHANNEL_MAP.get(channel)
        if not chat_id:
            log.warning(f"chat_id 미설정: {channel}")
            return False

        event_type = event.get("event_type", EventType.NEWS_ARTICLE)
        text = format_message(event_type, event, analysis, rules_count)

        return await self._send(chat_id, text)

    async def send_raw(self, channel: str, text: str) -> bool:
        """마크다운 텍스트를 직접 발송."""
        if not self.bot:
            return False
        chat_id = CHANNEL_MAP.get(channel)
        if not chat_id:
            return False
        return await self._send(chat_id, text)

    async def _send(self, chat_id: str, text: str) -> bool:
        """긴 메시지는 분할 발송."""
        chunks = _split_message(text)
        try:
            for chunk in chunks:
                await self.bot.send_message(
                    chat_id=chat_id,
                    text=chunk,
                    parse_mode=ParseMode.MARKDOWN,
                    disable_web_page_preview=True,
                )
                if len(chunks) > 1:
                    await asyncio.sleep(0.5)
            return True
        except TelegramError as e:
            log.error(f"텔레그램 발송 실패: {e}")
            # 파싱 오류 시 plain text 재시도
            try:
                await self.bot.send_message(chat_id=chat_id, text=text[:MAX_MESSAGE_LEN])
                return True
            except TelegramError:
                return False


def _split_message(text: str) -> list[str]:
    if len(text) <= MAX_MESSAGE_LEN:
        return [text]
    chunks = []
    while text:
        chunks.append(text[:MAX_MESSAGE_LEN])
        text = text[MAX_MESSAGE_LEN:]
    return chunks
