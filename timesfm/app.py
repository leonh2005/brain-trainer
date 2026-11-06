#!/usr/bin/env python3
import os
import sys
import threading
import warnings

warnings.filterwarnings("ignore")

import numpy as np
from flask import Flask, jsonify, render_template, request

sys.path.insert(0, os.path.dirname(__file__))
MODEL_DIR = os.path.join(os.path.dirname(__file__), "model")

app = Flask(__name__)

_model = None
_model_ready = False
_model_error = None

def _load_model():
    global _model, _model_ready, _model_error
    try:
        import torch

        import timesfm
        torch.set_float32_matmul_precision("high")
        _model = timesfm.TimesFM_2p5_200M_torch.from_pretrained(MODEL_DIR)
        _model_ready = True
    except Exception as e:
        _model_error = str(e)

threading.Thread(target=_load_model, daemon=True).start()


def fetch_stock_data(symbol: str, days: int = 120):
    import yfinance as yf
    ticker = f"{symbol}.TW"
    df = yf.download(ticker, period=f"{days}d", progress=False, auto_adjust=True)
    if df.empty:
        ticker = f"{symbol}.TWO"
        df = yf.download(ticker, period=f"{days}d", progress=False, auto_adjust=True)
    if df.empty:
        raise ValueError(f"找不到股票 {symbol} 的資料")
    close_series = df["Close"].dropna().squeeze()
    closes = [round(float(x), 2) for x in close_series.tolist()]
    dates = [str(d.date()) for d in close_series.index]
    return closes, dates


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def status():
    if _model_error:
        return jsonify({"ready": False, "error": _model_error})
    return jsonify({"ready": _model_ready})


@app.route("/api/forecast", methods=["POST"])
def forecast():
    if not _model_ready:
        return jsonify({"error": "模型載入中，請稍候..."}), 503

    data = request.json
    symbol = (data.get("symbol") or "2330").strip().upper()
    horizon = min(max(int(data.get("horizon") or 10), 1), 30)

    try:
        closes, dates = fetch_stock_data(symbol, days=120)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    import timesfm
    _model.compile(timesfm.ForecastConfig(
        max_context=min(512, len(closes)),
        max_horizon=horizon,
        normalize_inputs=True,
    ))
    input_data = [np.array(closes[-512:], dtype=np.float32)]
    point_forecast, quantile_forecast = _model.forecast(
        horizon=horizon,
        inputs=input_data,
    )

    preds = [round(float(x), 2) for x in point_forecast[0].tolist()]
    q10 = [round(float(x), 2) for x in quantile_forecast[0, :, 0].tolist()]
    q90 = [round(float(x), 2) for x in quantile_forecast[0, :, -1].tolist()]

    last_close = closes[-1]
    final_pred = preds[-1]
    total_chg = (final_pred - last_close) / last_close * 100
    if total_chg > 1:
        trend = "📈 看漲"
    elif total_chg < -1:
        trend = "📉 看跌"
    else:
        trend = "➡️ 盤整"

    return jsonify({
        "symbol": symbol,
        "horizon": horizon,
        "history_dates": dates,
        "history_prices": closes,
        "pred_prices": preds,
        "q10": q10,
        "q90": q90,
        "last_close": last_close,
        "total_chg": round(total_chg, 2),
        "trend": trend,
    })


# yfinance 的 tz 與 cookie 快取都走 peewee，而 peewee 的 SQLite 連線是 thread-local；
# Flask 每個請求開一條新執行緒，連線在執行緒結束後不會自動關閉，fd 會一路累積到
# Too many open files 讓服務無法回應。請求結束時關掉當前執行緒的兩條快取連線；
# peewee 下次查詢會自動重連。
try:
    from yfinance.cache import _CookieDBManager, _TzDBManager
except Exception:  # yfinance 未安裝或改版拿掉私有 API → 停用清理
    _CookieDBManager = _TzDBManager = None


@app.teardown_request
def _close_yf_caches(exc=None):
    if _TzDBManager is None:
        app.logger.warning("yfinance 快取管理器不可用，fd 可能持續累積")
        return
    for manager in (_TzDBManager, _CookieDBManager):
        try:
            manager.close_db()  # 未初始化時是安全 no-op
        except Exception:
            app.logger.warning("關閉 yfinance 快取連線失敗", exc_info=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5550, debug=False)
