#!/usr/bin/env python3
"""
TradingAgents 單次分析執行腳本
用法: python run_analysis.py <ticker> <date>
輸出: 進度到 stderr，最終 JSON 到 stdout
"""

import sys
import json
import os

def log(msg):
    print(msg, file=sys.stderr, flush=True)

if len(sys.argv) < 3:
    print(json.dumps({"error": "用法: run_analysis.py <ticker> <date>"}))
    sys.exit(1)

ticker = sys.argv[1]
date   = sys.argv[2]

# 正規化台股代碼
if ticker.isdigit() and len(ticker) == 4:
    ticker = ticker + ".TW"

log(f"[init] 分析 {ticker} @ {date}")

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

config = DEFAULT_CONFIG.copy()
config["llm_provider"]    = "deepseek"
config["deep_think_llm"]  = "deepseek-chat"
config["quick_think_llm"] = "deepseek-chat"
config["max_debate_rounds"] = 1
config["max_risk_discuss_rounds"] = 1

# 台股（.TW）自動路由到 FinMind 取得更準確的財報資料
if ticker.endswith(".TW"):
    log("[init] 偵測到台股，資料來源切換為 FinMind")
    config["data_vendors"] = {
        "core_stock_apis":     "finmind",
        "technical_indicators": "finmind",
        "fundamental_data":    "finmind",
        "news_data":           "yfinance",
    }
    config["tool_vendors"] = {
        "get_insider_transactions": "finmind",
    }
else:
    log("[init] 偵測到美股，使用預設 yfinance / alpha_vantage")

log("[init] 初始化 TradingAgentsGraph...")
ta = TradingAgentsGraph(
    selected_analysts=["market", "news", "fundamentals"],
    debug=False,
    config=config,
)

log("[run] 開始多Agent分析，預計 3~8 分鐘...")
try:
    final_state, decision = ta.propagate(ticker, date)
    log("[done] 分析完成")

    result = {
        "ticker":             ticker,
        "date":               date,
        "decision":           decision,
        "market_report":      final_state.get("market_report", ""),
        "news_report":        final_state.get("news_report", ""),
        "fundamentals_report": final_state.get("fundamentals_report", ""),
        "trader_plan":        final_state.get("trader_investment_plan", ""),
        "final_decision":     final_state.get("final_trade_decision", ""),
    }
    print(json.dumps(result, ensure_ascii=False))

except Exception as e:
    import traceback
    log(f"[error] {e}")
    print(json.dumps({"error": str(e), "detail": traceback.format_exc()}))
    sys.exit(1)
