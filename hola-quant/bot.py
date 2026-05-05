"""
Hola-Quant 通知模組：LINE Messaging API push
不需要 webhook / polling，不與 Oracle VM 的 tele-bot 衝突
"""
import logging
import os
from dataclasses import dataclass

import requests

logger = logging.getLogger(__name__)

LINE_API = "https://api.line.me/v2/bot/message/push"


@dataclass
class SignalMessage:
    symbol: str
    name: str
    direction: str
    confidence: int
    summary: str
    risks: str
    price_info: object = None


class HolaQuantBot:
    def __init__(self):
        self.token = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
        self.user_id = os.environ["LINE_USER_ID"]

    def _push(self, text: str):
        resp = requests.post(
            LINE_API,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
            json={"to": self.user_id, "messages": [{"type": "text", "text": text}]},
            timeout=10,
        )
        if resp.status_code != 200:
            logger.warning(f"[bot] LINE push 失敗: {resp.status_code} {resp.text}")

    def set_status_getter(self, getter):
        pass  # LINE 版不需要

    async def start(self):
        logger.info("[bot] LINE notifier 已啟動")

    async def stop(self):
        pass

    async def send_signal(self, sig: SignalMessage, _callback=None):
        direction_emoji = "📈" if sig.direction == "多" else "📉" if sig.direction == "空" else "😐"
        lines = [
            f"{direction_emoji} Hola-Quant 訊號",
            f"股票：{sig.symbol} {sig.name}",
            f"方向：{sig.direction}　信心：{sig.confidence}/10",
            f"摘要：{sig.summary}",
            f"風險：{sig.risks}",
        ]
        if sig.price_info:
            p = sig.price_info
            lines.append(
                f"現價：{p.current}　進場：{p.entry}\n"
                f"停損：{p.stop_loss}　目標：{p.target}"
            )
        self._push("\n".join(lines))
        logger.info(f"[bot] 訊號已推播 {sig.symbol} {sig.direction}")

    async def send_morning_report(self, text: str):
        self._push(text)

    async def push(self, text: str):
        self._push(text)
