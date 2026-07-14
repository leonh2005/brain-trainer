#!/usr/bin/env python3
import os
import logging
import asyncio
import anthropic
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from telegram.error import Conflict

logging.basicConfig(level=logging.WARNING)

TELEGRAM_TOKEN = open(os.path.expanduser("~/CCProject/.secrets/tgclaude_token.txt")).read().strip()
ALLOWED_CHAT_ID = 7556217543
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY") or open(os.path.expanduser("~/CCProject/.secrets/anthropic_key.txt")).read().strip()

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# 每個 chat 的對話歷史
histories: dict[int, list] = {}

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id != ALLOWED_CHAT_ID:
        return

    user_text = update.message.text
    if not user_text:
        return

    await update.message.reply_text("⏳ 處理中…")

    history = histories.setdefault(chat_id, [])
    history.append({"role": "user", "content": user_text})

    # 只保留最近 20 則，避免超出 context
    if len(history) > 20:
        history[:] = history[-20:]

    response = client.messages.create(
        model="claude-fable-5",
        max_tokens=4096,
        system="你是 Steven 的私人助理。回答簡潔、用繁體中文。",
        messages=history,
    )

    reply = response.content[0].text
    history.append({"role": "assistant", "content": reply})

    await update.message.reply_text(reply)

async def handle_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id != ALLOWED_CHAT_ID:
        return
    histories.pop(chat_id, None)
    await update.message.reply_text("✅ 對話記憶已清除")

async def error_handler(update, context):
    if isinstance(context.error, Conflict):
        logging.warning("Conflict 衝突，等待後重試…")
        await asyncio.sleep(5)
    else:
        logging.error(f"錯誤: {context.error}")

if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.COMMAND & filters.Regex(r'^/clear'), handle_clear))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)
    print("Bot 啟動中…")
    app.run_polling(drop_pending_updates=True)
