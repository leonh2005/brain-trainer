"""
AI 買賣點分析模組
使用 DeepSeek V3（deepseek-chat），給出買入價 / 止損價 / 目標價 + 一句結論
"""

import json
import os
from openai import OpenAI, APIStatusError

DEEPSEEK_API_KEY = "sk-49f9f0a651514aff96412fa7ad11ae85"

_client = None

def _get_client():
    global _client
    if _client is None:
        _client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
    return _client


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
今日量：{vol_k:,}張（5日均量：{avg5:,}張）
收盤位置：{close_pos:.0f}%（100%=日內最高點）
外資買賣超：{fi_net:+,}張
量價訊號：{signal}
策略：{strategy}

計算邏輯參考：
- 買入價：收盤價附近的合理進場點（考慮收盤位置與波動）
- 止損價：{"進場價 -1.5%，跌破立刻出" if strategy == "當沖" else "開盤跳空綠或跌破進場價 -2% 出"}
- 目標價：{"進場價 +2~3%" if strategy == "當沖" else "進場價 +3~5%，隔日漲2~4%區間出場"}

請只輸出 JSON，不要其他文字：
{{"buy": 數字, "stop": 數字, "target": 數字, "note": "20字內的一句操作結論"}}"""

    try:
        client = _get_client()
        resp = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=120,
        )
        text = resp.choices[0].message.content.strip()
        # 取出 JSON（防止前後有多餘文字）
        start = text.find('{')
        end = text.rfind('}') + 1
        if start == -1 or end == 0:
            return None
        data = json.loads(text[start:end])
        return {
            'buy':    round(float(data['buy']), 1),
            'stop':   round(float(data['stop']), 1),
            'target': round(float(data['target']), 1),
            'note':   str(data.get('note', '')),
        }
    except APIStatusError as e:
        print(f'[AI] {code} API 錯誤 {e.status_code}: {e.message}')
        if e.status_code == 402:
            _send_credit_alert()
        return None
    except Exception as e:
        print(f'[AI] {code} 分析失敗: {e}')
        return None


def _send_credit_alert():
    """DeepSeek 餘額不足時推播 Telegram"""
    import requests
    BOT_TOKEN = "8666778924:AAFMAFKfsfx3opS2CfCBrDYMIx6vcJKACTk"
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
