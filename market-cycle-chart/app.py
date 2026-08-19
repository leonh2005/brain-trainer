# -*- coding: utf-8 -*-
"""台股市場週期圖 Flask 服務。

- /                    單頁互動圖
- /api/health          健康檢查
- /api/scripts         劇本版本清單(下拉選單)
- /api/chart-data      主圖資料(月線或週線+波浪劇本+週期定義+當前位置，timeframe=monthly|weekly)
- /api/taiex           K線資料(timeframe=monthly|daily，日線含均線)
"""

import pandas as pd

import cycles
import current_position
import econ_cycles
import taiex_data
import wave_scripts
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

TIMELINE_START = "2020-01"
TIMELINE_END = "2028-12"  # 向未來延伸，讓波浪目標與週期未來段可見


def month_range(start_ym: str, end_ym: str) -> list[str]:
    sy, sm = map(int, start_ym.split("-"))
    ey, em = map(int, end_ym.split("-"))
    out = []
    y, m = sy, sm
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/api/scripts")
def scripts():
    return jsonify(wave_scripts.list_script_ids())


def week_range(start_ym: str, end_ym: str) -> list[str]:
    """start_ym~end_ym(YYYY-MM) 涵蓋範圍內每個週五的日期字串(YYYY-MM-DD)。"""
    start = pd.Timestamp(f"{start_ym}-01")
    end = pd.Timestamp(f"{end_ym}-01") + pd.offsets.MonthEnd(1)
    return pd.date_range(start=start, end=end, freq="W-FRI").strftime("%Y-%m-%d").tolist()


def _last_week_of_month(ym: str) -> str:
    """月份(YYYY-MM)當月最後一個週五(不論有沒有實際交易資料，跟 week_range() 用同一套算法，
    確保回傳值一定是 timeline 類別軸裡存在的值，不會讓週線圖上的點跑到軸外)。"""
    month_start = pd.Timestamp(f"{ym}-01")
    month_end = month_start + pd.offsets.MonthEnd(1)
    fridays = pd.date_range(start=month_start, end=month_end, freq="W-FRI")
    return fridays[-1].strftime("%Y-%m-%d")


def _week_of_ym_map(weekly_df) -> dict:
    """{YYYY-MM: 該月最後一筆實際週線資料的日期字串}，供錨點對齊與 current_x 計算共用。"""
    weekly = weekly_df.copy()
    weekly["ym"] = weekly["date"].dt.strftime("%Y-%m")
    weekly["date_str"] = weekly["date"].dt.strftime("%Y-%m-%d")
    return weekly.groupby("ym")["date_str"].last().to_dict()


def _resolve_anchor_dates_to_week(anchors: list, last_week_of_ym: dict) -> list:
    """波浪劇本 anchors/projected 的月份(YYYY-MM)對應到該月最後一個週五，讓錨點能對齊週線
    x 軸的實際資料點；沒有實際週線資料的月份(如尚未發生的未來月份)用同套週五算法推算，
    確保一定落在 timeline 類別清單內，不會被 Plotly 排到圖表最右側。"""
    out = []
    for a in anchors:
        resolved = dict(a)
        resolved["date"] = last_week_of_ym.get(a["date"]) or _last_week_of_month(a["date"])
        out.append(resolved)
    return out


@app.route("/api/chart-data")
def chart_data():
    script_id = request.args.get("script", "v1")
    timeframe = request.args.get("timeframe", "monthly")  # monthly|weekly
    try:
        script = wave_scripts.get_script(script_id)
    except wave_scripts.WaveScriptError as e:
        return jsonify({"error": str(e)}), 400

    try:
        daily = taiex_data.get_daily()
    except taiex_data.TaiexDataError as e:
        return jsonify({"error": f"加權指數資料載入失敗: {e}"}), 502

    # 位置/週期段位一律以月為單位判斷，跟週線/月線顯示模式無關；
    # current_x 也一律從 current_ym 對應回去，避免「現在」線位置跟標示的月份對不上
    # (週線resample以週五為標籤，月初交易日可能被歸進下週五、跨到下個月)
    monthly = taiex_data.get_monthly(daily)
    if monthly.empty:
        return jsonify({"error": "月線資料為空"}), 502
    last_month = monthly.iloc[-1]
    current_ym = last_month["date"].strftime("%Y-%m")
    current_price = float(round(last_month["close"], 2))

    if timeframe == "weekly":
        bars = taiex_data.get_weekly(daily)
        if bars.empty:
            return jsonify({"error": "週線資料為空"}), 502
        bars = bars.copy()
        bars["date_str"] = bars["date"].dt.strftime("%Y-%m-%d")
        close_by_key = dict(zip(bars["date_str"], bars["close"].round(2)))
        timeline = week_range(TIMELINE_START, TIMELINE_END)
        last_week_of_ym = _week_of_ym_map(bars)
        anchors = _resolve_anchor_dates_to_week(script["anchors"], last_week_of_ym)
        projected = _resolve_anchor_dates_to_week(script.get("projected", []), last_week_of_ym)
        current_x = last_week_of_ym.get(current_ym, bars.iloc[-1]["date_str"])
    else:
        bars = monthly.copy()
        bars["ym"] = bars["date"].dt.strftime("%Y-%m")
        close_by_key = dict(zip(bars["ym"], bars["close"].round(2)))
        timeline = month_range(TIMELINE_START, TIMELINE_END)
        anchors = script["anchors"]
        projected = script.get("projected", [])
        current_x = current_ym

    taiex_close = [close_by_key.get(key) for key in timeline]  # 未來為 None

    return jsonify({
        "timeline": timeline,
        "taiex_close": taiex_close,
        "current_x": current_x,
        "current_ym": current_ym,
        "current_price": current_price,
        "wave": {
            "id": script["id"],
            "label": script["label"],
            "source": script["source"],
            "source_date": script.get("source_date", ""),
            "description": script.get("description", ""),
            "anchors": anchors,
            "targets": script["targets"],
            "retracements": script.get("retracements", []),
            "projected": projected,
        },
        "market_cycles": cycles.DEFAULT_CYCLES,
        "econ_cycles": econ_cycles.DEFAULT_ECON_CYCLES,
        "position": {
            "wave": current_position.wave_status(script, current_ym, current_price),
            "stages": current_position.cycle_stages(current_ym),
        },
    })


@app.route("/api/taiex")
def taiex():
    timeframe = request.args.get("timeframe", "daily")
    try:
        daily = taiex_data.get_daily()
    except taiex_data.TaiexDataError as e:
        return jsonify({"error": f"加權指數資料載入失敗: {e}"}), 502

    if timeframe == "monthly":
        df = taiex_data.get_monthly(daily)
        df = taiex_data.add_moving_averages(df, windows=(3, 12))
    else:
        df = daily.tail(500).copy()  # 日線視圖近約2年
        df = taiex_data.add_moving_averages(df, windows=(5, 20, 60))

    df = df.copy()
    df["date"] = df["date"].dt.strftime("%Y-%m-%d")
    return jsonify({
        "timeframe": timeframe,
        "rows": df.where(df.notna(), None).to_dict(orient="records"),
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5905, debug=False)
