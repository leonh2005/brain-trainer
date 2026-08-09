"""每日收盤後跑一次：把當天大盤(^TWII)實際走勢分類進十大類型之一，
並累加對應類型的命中次數(hit_counts.json)，供前端在該類型後面顯示 +N。

從部署當天開始累計，不回溯補歷史資料。
"""
import json
import os

import pandas as pd
import yfinance as yf

from analyze import classify_day

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ANALYSIS_JSON = os.path.join(BASE_DIR, "analysis_data.json")
HITS_JSON = os.path.join(BASE_DIR, "hit_counts.json")


def load_hits():
    if os.path.exists(HITS_JSON):
        with open(HITS_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"last_date": None, "hits": {}, "history": []}


def save_hits(data):
    with open(HITS_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def main():
    with open(ANALYSIS_JSON, "r", encoding="utf-8") as f:
        analysis = json.load(f)
    range_p30 = analysis["range_p30"]

    df = yf.download("^TWII", period="5d", auto_adjust=False, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df[["Open", "High", "Low", "Close"]].dropna()
    if df.empty:
        print("無法取得 ^TWII 今日資料，略過")
        return

    last_row = df.iloc[-1]
    trade_date = df.index[-1].strftime("%Y-%m-%d")

    hits = load_hits()
    if hits["last_date"] == trade_date:
        print(f"{trade_date} 已經統計過，略過")
        return

    type_name = classify_day(last_row, range_p30)
    hits["hits"][type_name] = hits["hits"].get(type_name, 0) + 1
    hits["history"].append({"date": trade_date, "type": type_name})
    hits["last_date"] = trade_date
    save_hits(hits)
    print(f"{trade_date} 命中：{type_name}（累計 {hits['hits'][type_name]} 次）")


if __name__ == "__main__":
    main()
