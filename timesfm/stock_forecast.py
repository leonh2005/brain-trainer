#!/usr/bin/env python3
"""
TimesFM 台股價格預測腳本
用法: python stock_forecast.py [股票代號] [預測天數]
範例: python stock_forecast.py 2330 10
"""

import sys
import os
import warnings
warnings.filterwarnings("ignore")

import numpy as np
import torch

# 設定路徑
MODEL_DIR = os.path.join(os.path.dirname(__file__), "model")


def fetch_stock_data(symbol: str, days: int = 120) -> tuple[list[float], list[str]]:
    """用 yfinance 抓台股歷史收盤價"""
    try:
        import yfinance as yf
    except ImportError:
        raise ImportError("請先安裝 yfinance: uv pip install yfinance")

    ticker = f"{symbol}.TW"
    df = yf.download(ticker, period=f"{days}d", progress=False, auto_adjust=True)
    if df.empty:
        # 嘗試 OTC
        ticker = f"{symbol}.TWO"
        df = yf.download(ticker, period=f"{days}d", progress=False, auto_adjust=True)
    if df.empty:
        raise ValueError(f"找不到股票 {symbol} 的資料")

    close_series = df["Close"].dropna().squeeze()
    closes = close_series.tolist()
    dates = [str(d.date()) for d in close_series.index]
    return closes, dates


def load_model():
    import timesfm
    torch.set_float32_matmul_precision("high")
    print("載入模型中...", end=" ", flush=True)
    model = timesfm.TimesFM_2p5_200M_torch.from_pretrained(MODEL_DIR)
    print("完成")
    return model


def forecast_stock(symbol: str, horizon: int = 10):
    print(f"\n=== TimesFM 台股預測：{symbol} ===\n")

    # 1. 抓資料
    print(f"抓取 {symbol} 近 120 日歷史資料...")
    closes, dates = fetch_stock_data(symbol, days=120)
    print(f"取得 {len(closes)} 筆收盤價，最新：{dates[-1]} = {closes[-1]:.2f}")

    # 2. 載入模型
    model = load_model()

    # 3. 預測
    print(f"\n預測未來 {horizon} 個交易日...")
    import timesfm
    model.compile(timesfm.ForecastConfig(
        max_context=min(512, len(closes)),
        max_horizon=horizon,
        normalize_inputs=True,
    ))
    input_data = [np.array(closes[-512:], dtype=np.float32)]
    point_forecast, quantile_forecast = model.forecast(
        horizon=horizon,
        inputs=input_data,
    )

    preds = point_forecast[0].tolist()
    # quantile_forecast shape: [batch, horizon, quantiles]
    q10 = quantile_forecast[0, :, 0].tolist()  # 10th percentile
    q90 = quantile_forecast[0, :, -1].tolist()  # 90th percentile

    # 4. 顯示結果
    last_close = closes[-1]
    print(f"\n{'交易日':<6} {'預測收盤':>10} {'變化%':>8} {'低估':>10} {'高估':>10}")
    print("-" * 50)
    for i, (p, lo, hi) in enumerate(zip(preds, q10, q90), 1):
        chg = (p - last_close) / last_close * 100
        chg_str = f"+{chg:.2f}%" if chg >= 0 else f"{chg:.2f}%"
        print(f"  +{i:2d}日  {p:>10.2f} {chg_str:>8}  {lo:>10.2f}  {hi:>10.2f}")

    # 5. 簡單趨勢判斷
    final_pred = preds[-1]
    total_chg = (final_pred - last_close) / last_close * 100
    trend = "📈 看漲" if total_chg > 1 else ("📉 看跌" if total_chg < -1 else "➡️ 盤整")
    print(f"\n{horizon} 日趨勢：{trend}（預測漲跌 {total_chg:+.2f}%）")
    print(f"預測區間：{min(q10):.2f} ~ {max(q90):.2f}")
    print()

    return preds


if __name__ == "__main__":
    symbol = sys.argv[1] if len(sys.argv) > 1 else "2330"
    horizon = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    forecast_stock(symbol, horizon)
