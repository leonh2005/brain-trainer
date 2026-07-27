# -*- coding: utf-8 -*-
"""台股市場週期圖 Flask 服務。

- /                    單頁互動圖
- /api/health          健康檢查
- /api/scripts         劇本版本清單(下拉選單)
- /api/chart-data      月線主圖資料(月線+波浪劇本+週期定義+當前位置)
- /api/taiex           K線資料(timeframe=monthly|daily，日線含均線)
"""

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


@app.route("/api/chart-data")
def chart_data():
    script_id = request.args.get("script", "v1")
    try:
        script = wave_scripts.get_script(script_id)
    except wave_scripts.WaveScriptError as e:
        return jsonify({"error": str(e)}), 400

    try:
        monthly = taiex_data.get_monthly(taiex_data.get_daily())
    except taiex_data.TaiexDataError as e:
        return jsonify({"error": f"加權指數資料載入失敗: {e}"}), 502

    if monthly.empty:
        return jsonify({"error": "月線資料為空"}), 502

    monthly = monthly.copy()
    monthly["ym"] = monthly["date"].dt.strftime("%Y-%m")
    close_by_ym = dict(zip(monthly["ym"], monthly["close"].round(2)))

    timeline = month_range(TIMELINE_START, TIMELINE_END)
    taiex_close = [close_by_ym.get(ym) for ym in timeline]  # 未來月份為 None

    # 當前 = 月線最後一筆
    last = monthly.iloc[-1]
    current_ym = last["ym"]
    current_price = float(round(last["close"], 2))

    return jsonify({
        "timeline": timeline,
        "taiex_close": taiex_close,
        "current_ym": current_ym,
        "current_price": current_price,
        "wave": {
            "id": script["id"],
            "label": script["label"],
            "source": script["source"],
            "source_date": script.get("source_date", ""),
            "description": script.get("description", ""),
            "anchors": script["anchors"],
            "targets": script["targets"],
            "retracements": script.get("retracements", []),
            "projected": script.get("projected", []),
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
