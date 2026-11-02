"""
AI 買賣點分析模組
Gemini 為主（免費額度、OpenAI 相容端點），DeepSeek 當備援（有餘額時才用得到）
"""

import json
import os

from openai import APIStatusError, OpenAI

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/openai/"
GEMINI_MODELS = ("gemini-2.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3-flash-preview")
_exhausted: set[str] = set()          # 本程序已回 429 的模型，後面的股票不必再試
_credit_alerted = False


def _read_key(filename: str) -> str | None:
    """延遲載入金鑰；檔案不存在回 None（該來源就跳過）。"""
    try:
        with open(os.path.expanduser(f"~/CCProject/.secrets/{filename}"), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return None


def _candidates():
    """依序嘗試的 (client, model, extra)：Gemini 三個模型 → DeepSeek（已限流的跳過）。"""
    gemini = _read_key("gemini_key.txt")
    if gemini:
        for model in GEMINI_MODELS:
            yield (OpenAI(api_key=gemini, base_url=GEMINI_BASE, timeout=30, max_retries=0),
                   model, {"reasoning_effort": "none"})
    deepseek = _read_key("deepseek_key.txt")
    if deepseek:
        yield (OpenAI(api_key=deepseek, base_url="https://api.deepseek.com", timeout=30,
                      max_retries=0), "deepseek-chat", {})


def analyze_stock(code: str, name: str, close: float, chg_pct: float,
                  amp_pct: float, vol_k: int, avg5: int, close_pos: float,
                  fi_net: int, signal: str, strategy: str = "隔日沖") -> dict | None:
    """
    呼叫 Groq 分析單檔股票，回傳 dict：
    {buy, stop, target, note} 或 None（失敗時）
    """
    prompt = f"""你是台股{strategy}交易分析師。根據以下技術數據，給出精確的買賣點位與一句操作結論。

標的：{code} {name}
收盤價：{close}元
今日漲幅：{chg_pct:+.1f}%
振幅：{amp_pct:.1f}%
今日累計量：{vol_k:,}張（⚠️ 09:30 開盤初期快照，非全日量，請勿用於評估量能）
5日日均量：{avg5:,}張（以此評估量能是否充足）
收盤位置：{close_pos:.0f}%（100%=日內最高點）
外資買賣超：{fi_net:+,}張
量價訊號：{signal}
策略：{strategy}

重要：本資料為開盤 5 分鐘內的即時快照，今日累計量極低屬正常，禁止以今日量不足作為結論。
量能判斷請依5日日均量（{avg5:,}張），若5日均量 > 3000張即代表流動性充足。

計算邏輯參考：
- 買入價：收盤價附近的合理進場點（考慮收盤位置與波動）
- 止損價：{"進場價 -1.5%，跌破立刻出" if strategy == "當沖" else "開盤跳空綠或跌破進場價 -2% 出"}
- 目標價：{"進場價 +2~3%" if strategy == "當沖" else "進場價 +3~5%，隔日漲2~4%區間出場"}

請只輸出 JSON，不要其他文字：
{{"buy": 數字, "stop": 數字, "target": 數字, "note": "20字內的一句操作結論"}}"""

    last_error = None
    tried = 0
    for client, model, extra in _candidates():
        if model in _exhausted:
            continue
        tried += 1
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=200,      # Gemini 關掉思考後 120 也夠，但留點餘裕給長一點的結論
                **extra,
            )
            text = (resp.choices[0].message.content or "").strip()
            if not text:
                raise ValueError("模型回應為空")   # 200 但沒內容＝失敗，不能當成功
            # 取出 JSON（防止前後有多餘文字或 ``` 圍籬）
            start = text.find('{')
            end = text.rfind('}') + 1
            if start == -1 or end == 0:
                raise ValueError("回應不是 JSON")
            data = json.loads(text[start:end])
            return {
                'buy':    round(float(data['buy']), 1),
                'stop':   round(float(data['stop']), 1),
                'target': round(float(data['target']), 1),
                'note':   str(data.get('note', '')),
            }
        except APIStatusError as e:
            last_error = e
            if e.status_code == 429:
                _exhausted.add(model)
            print(f'[AI] {code} {model} 失敗 {e.status_code}: {str(e.message)[:80]}')
            continue
        except Exception as e:
            last_error = e
            print(f'[AI] {code} {model} 分析失敗: {str(e)[:80]}')
            continue

    if tried == 0:
        print('[AI] 沒有可用的 API 金鑰（Gemini／DeepSeek 都讀不到）')
    elif isinstance(last_error, APIStatusError) and last_error.status_code == 402:
        _send_credit_alert()
    return None


def _send_credit_alert():
    """備援（DeepSeek）也沒餘額時推播 Telegram；每個程序只發一次。"""
    global _credit_alerted
    if _credit_alerted:
        return
    _credit_alerted = True
    import requests
    BOT_TOKEN = open(os.path.expanduser("~/CCProject/.secrets/telegram_token.txt")).read().strip()
    CHAT_ID   = "7556217543"
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID,
              "text": "⚠️ DeepSeek API 餘額不足，AI 買賣點分析已停用。\n請至 https://platform.deepseek.com 儲值。"},
        timeout=10
    )


def format_ai_block(result: dict | None) -> str:
    """把分析結果格式化成 Telegram 訊息區塊"""
    if not result:
        return "   🤖 AI分析：無法取得"
    return (
        f"   🤖 買:{result['buy']}  止損:{result['stop']}  目標:{result['target']}\n"
        f"   💬 {result['note']}"
    )
