"""
Telegram inline keyboard bot：接收分析結果 → 推播 → 等待確認/拒絕
支援 +1/-1/+5/-5 快速調整，或點「✏️ 自訂」輸入任意股數
"""
import asyncio
import logging
import os
import re
from dataclasses import dataclass, replace
from typing import Callable, Awaitable

from telegram import ForceReply, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

logger = logging.getLogger(__name__)

CB_CONFIRM = "confirm:"
CB_REJECT  = "reject:"
CB_ADJ     = "adj:"     # adj:±N:symbol
CB_CUSTOM  = "custom:"  # custom:symbol


@dataclass
class SignalMessage:
    symbol: str
    name: str
    direction: str
    confidence: int
    summary: str
    risks: str
    price_info: object = None


# { "symbol:msg_id": {"sig": SignalMessage, "shares": int, "cb": callable, "msg_id": int} }
_pending: dict[str, dict] = {}

# chat_id → {"key": str|None, "symbol": str, "msg_id": int}
_awaiting_input: dict[int, dict] = {}


def _format_price_block(p, shares: int) -> str:
    amount = int(shares * p.entry)
    return (
        f"\n\n💰 *價位建議*\n"
        f"現價：{p.current}　進場：*{p.entry}*\n"
        f"停損：{p.stop_loss}（{round((p.stop_loss/p.current-1)*100,1)}%）　"
        f"目標：{p.target}（+{round((p.target/p.current-1)*100,1)}%）\n"
        f"📦 零股：*{shares} 股*（約 NT\\${amount:,}）"
    )


def _build_message(sig: SignalMessage, shares: int | None = None) -> str:
    icon = {"多": "📈", "空": "📉", "觀望": "👀"}.get(sig.direction, "❓")
    bar = "█" * sig.confidence + "░" * (10 - sig.confidence)
    msg = (
        f"{icon} *{sig.symbol} {sig.name}*\n"
        f"方向：*{sig.direction}*　信心：`{bar}` {sig.confidence}/10\n\n"
        f"{sig.summary}\n\n"
        f"⚠️ 風險：{sig.risks}"
    )
    if sig.price_info:
        p = sig.price_info
        effective_shares = shares if shares is not None else p.shares
        msg += _format_price_block(p, effective_shares)
    return msg


def _build_keyboard(symbol: str, shares: int | None = None) -> InlineKeyboardMarkup:
    rows = []
    if shares is not None:
        rows.append([
            InlineKeyboardButton("-5", callback_data=f"{CB_ADJ}-5:{symbol}"),
            InlineKeyboardButton("-1", callback_data=f"{CB_ADJ}-1:{symbol}"),
            InlineKeyboardButton(f"📦 {shares}股", callback_data="noop"),
            InlineKeyboardButton("+1", callback_data=f"{CB_ADJ}+1:{symbol}"),
            InlineKeyboardButton("+5", callback_data=f"{CB_ADJ}+5:{symbol}"),
        ])
        rows.append([
            InlineKeyboardButton("✏️ 自訂股數", callback_data=f"{CB_CUSTOM}{symbol}"),
        ])
    rows.append([
        InlineKeyboardButton("✅ 確認下單", callback_data=f"{CB_CONFIRM}{symbol}"),
        InlineKeyboardButton("❌ 拒絕",    callback_data=f"{CB_REJECT}{symbol}"),
    ])
    return InlineKeyboardMarkup(rows)


def _parse_shares_from_text(text: str) -> int | None:
    """從訊息文字解析目前股數（備用，當 _pending 無資料時）"""
    m = re.search(r"零股：\*(\d+) 股\*", text)
    return int(m.group(1)) if m else None


class HolaQuantBot:
    def __init__(self):
        token = os.environ["TG_BOT_TOKEN"]
        self.chat_id = int(os.environ["TG_CHAT_ID"])
        self._status_getter: Callable[[], dict] | None = None
        self.app = Application.builder().token(token).build()
        self.app.add_handler(CommandHandler("status", self._on_status))
        self.app.add_handler(CallbackQueryHandler(self._on_callback))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_text))

    def set_status_getter(self, getter: Callable[[], dict]):
        self._status_getter = getter

    async def start(self):
        await self.app.initialize()
        await self.app.bot.delete_webhook(drop_pending_updates=True)
        await self.app.start()
        await self.app.updater.start_polling(drop_pending_updates=True)
        logger.info("[bot] Telegram bot 已啟動")

    async def stop(self):
        await self.app.updater.stop()
        await self.app.stop()
        await self.app.shutdown()

    async def _on_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.id != self.chat_id:
            return
        s = self._status_getter() if self._status_getter else {}
        watchlist = s.get("watchlist", [])
        symbols = " ".join(w["symbol"] for w in watchlist) if watchlist else "未載入"
        last_scan = s.get("last_scan")
        last_scan_str = last_scan.strftime("%H:%M:%S") if last_scan else "尚未掃描"
        news = s.get("news_stats", {})
        bullish = news.get("bullish_pct", "-")
        bearish = news.get("bearish_pct", "-")
        total = news.get("total", "-")
        pending_count = len(_pending)
        msg = (
            f"✅ *Hola-Quant 系統正常*\n\n"
            f"🎯 監控 {len(watchlist)} 檔：`{symbols}`\n"
            f"🕐 上次掃描：{last_scan_str}\n"
            f"📰 情緒：多 {bullish}%　空 {bearish}%　共 {total} 篇\n"
            f"⏳ 待確認訊號：{pending_count} 個"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")

    async def send_signal(
        self,
        sig: SignalMessage,
        on_confirm: Callable[[bool], Awaitable[None]],
    ):
        shares = sig.price_info.shares if sig.price_info else None
        msg = await self.app.bot.send_message(
            chat_id=self.chat_id,
            text=_build_message(sig, shares),
            parse_mode="Markdown",
            reply_markup=_build_keyboard(sig.symbol, shares),
        )
        key = f"{sig.symbol}:{msg.message_id}"
        _pending[key] = {"sig": sig, "shares": shares, "cb": on_confirm, "msg_id": msg.message_id}

    async def _on_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.effective_chat.id
        if chat_id not in _awaiting_input:
            return

        ctx = _awaiting_input.pop(chat_id)
        text = (update.message.text or "").strip()

        try:
            new_shares = max(1, int(text))
            logger.info(f"[bot] 自訂股數：{new_shares} 股（{ctx['symbol']}）")
        except ValueError:
            await update.message.reply_text("⚠️ 請輸入正整數，例如：15")
            _awaiting_input[chat_id] = ctx  # 繼續等待
            return

        symbol  = ctx["symbol"]
        msg_id  = ctx["msg_id"]
        key     = ctx.get("key")
        state   = _pending.get(key) if key else None

        if state:
            state["shares"] = new_shares
            new_text = _build_message(state["sig"], new_shares)
        else:
            # 測試訊號路徑：只更新訊息中的股數/金額行
            original = update.effective_message.reply_to_message
            if original:
                orig_text = original.text or ""
                new_text = re.sub(
                    r"📦 零股：\*\d+ 股\*（約 NT\\\$[\d,]+）",
                    f"📦 零股：*{new_shares} 股*（更新中）",
                    orig_text,
                )
            else:
                new_text = f"📦 零股：*{new_shares} 股*"

        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=msg_id,
            text=new_text,
            parse_mode="Markdown",
            reply_markup=_build_keyboard(symbol, new_shares),
        )
        await update.message.delete()

    async def _on_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        data = query.data

        if data == "noop":
            return

        if data.startswith(CB_CUSTOM):
            symbol  = data[len(CB_CUSTOM):]
            chat_id = query.message.chat_id
            msg_id  = query.message.message_id

            # 找 _pending key（可能沒有，例如測試訊號）
            key = next(
                (k for k in reversed(list(_pending)) if k.startswith(f"{symbol}:")),
                None,
            )
            # 從現有訊息解析股數
            current_shares = (
                _pending[key]["shares"] if key
                else _parse_shares_from_text(query.message.text or "")
            ) or 1

            _awaiting_input[chat_id] = {"key": key, "symbol": symbol, "msg_id": msg_id}

            await query.message.reply_text(
                f"✏️ 目前：{current_shares} 股，請輸入新股數：",
                reply_markup=ForceReply(selective=True, input_field_placeholder="例：25"),
            )
            return

        if data.startswith(CB_ADJ):
            rest  = data[len(CB_ADJ):]
            colon = rest.index(":")
            delta  = int(rest[:colon])
            symbol = rest[colon+1:]
            key = next(
                (k for k in reversed(list(_pending)) if k.startswith(f"{symbol}:")),
                None,
            )
            if not key:
                return
            state = _pending[key]
            new_shares = max(1, (state["shares"] or 1) + delta)
            state["shares"] = new_shares
            await query.edit_message_text(
                _build_message(state["sig"], new_shares),
                parse_mode="Markdown",
                reply_markup=_build_keyboard(symbol, new_shares),
            )
            return

        if data.startswith(CB_CONFIRM):
            symbol    = data[len(CB_CONFIRM):]
            confirmed = True
        elif data.startswith(CB_REJECT):
            symbol    = data[len(CB_REJECT):]
            confirmed = False
        else:
            return

        key = next(
            (k for k in reversed(list(_pending)) if k.startswith(f"{symbol}:")),
            None,
        )
        if not key:
            return

        _awaiting_input.pop(query.message.chat_id, None)
        state = _pending.pop(key)
        label = "✅ 已確認下單" if confirmed else "❌ 已拒絕"

        if confirmed and state["shares"] and state["sig"].price_info:
            p = state["sig"].price_info
            from pricer import PriceInfo
            adjusted = PriceInfo(
                current=p.current,
                entry=p.entry,
                stop_loss=p.stop_loss,
                target=p.target,
                shares=state["shares"],
                amount=int(state["shares"] * p.entry),
            )
            state["sig"] = replace(state["sig"], price_info=adjusted)

        await query.edit_message_reply_markup(reply_markup=None)
        await query.edit_message_text(
            query.message.text + f"\n\n*{label}*",
            parse_mode="Markdown",
        )
        await state["cb"](confirmed)
