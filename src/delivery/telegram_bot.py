"""
텔레그램 발송 모듈 — 이벤트 드리븐.

발송 대상:
  1. 새 예측 추출 시: "새 예측 N건 추출됨" + 대시보드 링크
  2. verdict 변경 시: "예측 #ID [verdict]: 요약(30자)" + 대시보드 링크
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

log = logging.getLogger(__name__)

MAX_MESSAGE_LEN = 4096
DASHBOARD_URL = ""  # 대시보드 URL (배포 후 설정)


class TelegramBot:

    def __init__(self):
        if TELEGRAM_BOT_TOKEN:
            self.bot = Bot(token=TELEGRAM_BOT_TOKEN)
        else:
            self.bot = None
            log.warning("TELEGRAM_BOT_TOKEN 미설정 — 텔레그램 발송 비활성화")

    async def send_prediction_extracted(self, count: int) -> bool:
        """새 예측 추출 알림."""
        text = f"📊 *새 예측 {count}건 추출됨*"
        if DASHBOARD_URL:
            text += f"\n🔗 [대시보드]({DASHBOARD_URL})"
        return await self._send_to_tier1(text)

    async def send_verdict_changed(
        self, pred_id: int, verdict: str, summary: str
    ) -> bool:
        """예측 verdict 변경 알림."""
        emoji = "✅" if verdict == "CORRECT" else "❌"
        text = f"{emoji} *예측 #{pred_id}* [{verdict}]: {summary}"
        if DASHBOARD_URL:
            text += f"\n🔗 [대시보드]({DASHBOARD_URL})"
        return await self._send_to_tier1(text)

    async def send_raw(self, channel: str, text: str) -> bool:
        """마크다운 텍스트를 직접 발송."""
        if not self.bot:
            return False
        chat_id = TELEGRAM_TIER1_CHAT_ID if channel == "tier1" else None
        if not chat_id:
            return False
        return await self._send(chat_id, text)

    async def send_alert(self, message: str) -> bool:
        """크리티컬 에러 알림을 tier1 채널로 발송."""
        if not self.bot:
            log.warning(f"[텔레그램 비활성화] alert: {message[:80]}")
            return False
        chat_id = TELEGRAM_TIER1_CHAT_ID
        if not chat_id:
            return False
        text = f"[ALERT] pipeline error\n\n{message}"
        return await self._send(chat_id, text)

    async def _send_to_tier1(self, text: str) -> bool:
        if not self.bot:
            log.info(f"[텔레그램 비활성화] {text[:60]}")
            return False
        if not TELEGRAM_TIER1_CHAT_ID:
            log.warning("TELEGRAM_TIER1_CHAT_ID 미설정")
            return False
        return await self._send(TELEGRAM_TIER1_CHAT_ID, text)

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
