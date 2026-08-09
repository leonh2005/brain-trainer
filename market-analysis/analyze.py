"""
台股大盤分析腳本
1. 大盤日內走勢十大類型統計
2. 大盤 x 費半 x 台積電ADR 相關性與隔日預測
3. 六大權值股與大盤相關性 / beta

資料來源: yfinance, period=10y, 日線, auto_adjust=False
"""
import json

import numpy as np
import pandas as pd
import yfinance as yf

START = "2000-01-01"  # 約26年,涵蓋各標的最長可得歷史
TICKERS_PART2 = ["^TWII", "^SOX", "TSM"]
TICKERS_PART3 = ["2330.TW", "2454.TW", "2308.TW", "2317.TW", "3711.TW", "2382.TW"]
NAMES_PART3 = {
    "2330.TW": "台積電",
    "2454.TW": "聯發科",
    "2308.TW": "台達電",
    "2317.TW": "鴻海",
    "3711.TW": "日月光",
    "2382.TW": "廣達",
}


def download(ticker):
    # 用 period="max" 取得該標的完整歷史(部分標的用 start=日期下載會遇到 yfinance
    # 的間歇性 API 錯誤,period="max" 較穩定),再裁切到 START 之後即可涵蓋~26年
    df = yf.download(ticker, period="max", auto_adjust=False, progress=False)
    if df is not None and not df.empty:
        df = df[df.index >= pd.Timestamp(START)]
    if df.empty:
        return None
    # yfinance 有時回傳多層欄位 (ticker 在第二層)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df[["Open", "High", "Low", "Close"]].dropna()
    df.index = pd.to_datetime(df.index).tz_localize(None)
    return df


# ---------------------------------------------------------------
# 分析一: 大盤日內走勢十大類型
# ---------------------------------------------------------------
def classify_day(row, range_p30):
    o, h, l, c = row["Open"], row["High"], row["Low"], row["Close"]
    rng = h - l
    if rng <= 0:
        return "窄幅震盪(其他)"
    body = c - o
    upper_shadow = h - max(o, c)
    lower_shadow = min(o, c) - l
    pos = (c - l) / rng  # 收盤位置 0=最低 1=最高
    b = abs(body) / rng  # 實體佔比
    up_ratio = upper_shadow / rng
    dn_ratio = lower_shadow / rng
    is_narrow = rng < range_p30
    is_doji = b < 0.10
    up_open = o >= (h + l) / 2  # 開盤位置偏高 or 偏低,用開盤相對前一日不易取得,改用開盤與當日中價比較近似判斷開高/開低走勢:改用 c vs o 決定紅黑,配合 pos 決定走勢型態

    is_red = c >= o

    # 互斥判斷順序: 十字 > 窄幅 > 長影 > 收極端(趨勢單邊) > 一般四型
    if is_doji:
        return "⑨十字變盤"
    if is_narrow:
        return "⑦窄幅紅盤震盪" if is_red else "⑧窄幅黑盤震盪"
    if up_ratio > 0.35 and up_ratio > dn_ratio:
        return "⑤衝高回落(長上影)"
    if dn_ratio > 0.35 and dn_ratio > up_ratio:
        return "⑥探底回升(長下影)"
    if pos >= 0.95 or pos <= 0.05:
        return "⑩趨勢單邊日(收極端)"
    if is_red and pos > 0.8:
        return "①開低走高強多" if o <= (h + l) / 2 else "①開高走高強多"
    if is_red and pos <= 0.8:
        return "②開低走高V轉" if o > c - body else "②開低走高V轉"
    if (not is_red) and pos < 0.2:
        return "④開低走低弱空" if o >= (h + l) / 2 else "④開低走低弱空"
    if not is_red:
        return "③開高走低翻黑"
    return "①開高走高強多"


def analysis_one(twii):
    range_ = twii["High"] - twii["Low"]
    range_p30 = range_.quantile(0.30)

    labels = twii.apply(lambda r: classify_day(r, range_p30), axis=1)
    counts = labels.value_counts()
    pct = (counts / len(labels) * 100).round(2)
    result = pd.DataFrame({"次數": counts, "百分比(%)": pct}).sort_values(
        "次數", ascending=False
    )
    return result, len(labels), range_p30


# ---------------------------------------------------------------
# 分析二: 大盤 x 費半 x 台積電ADR 相關性 -> 隔日預測
# ---------------------------------------------------------------
def analysis_two(twii, sox, tsm):
    close = pd.DataFrame(
        {
            "TWII": twii["Close"],
            "SOX": sox["Close"],
            "TSM": tsm["Close"],
        }
    ).dropna()

    ret = close.pct_change().dropna()
    ret_2y = ret.tail(504)  # 近似 2 年交易日

    corr_full = ret.corr()
    corr_2y = ret_2y.corr()

    # 隔夜領先: SOX/TSM 在 t 日的報酬 vs TWII 在 t+1 日的報酬
    lead = pd.DataFrame(
        {
            "SOX_t": ret["SOX"],
            "TSM_t": ret["TSM"],
            "TWII_t1": ret["TWII"].shift(-1),
        }
    ).dropna()
    lead_2y = lead.tail(504)

    lead_corr_full = {
        "SOX_lead_TWII": lead["SOX_t"].corr(lead["TWII_t1"]),
        "TSM_lead_TWII": lead["TSM_t"].corr(lead["TWII_t1"]),
    }
    lead_corr_2y = {
        "SOX_lead_TWII": lead_2y["SOX_t"].corr(lead_2y["TWII_t1"]),
        "TSM_lead_TWII": lead_2y["TSM_t"].corr(lead_2y["TWII_t1"]),
    }

    # 方向勝率 (全期)
    def win_rate(cond_col, target_col, df):
        up = df[df[cond_col] > 0]
        dn = df[df[cond_col] < 0]
        up_wr = (up[target_col] > 0).mean() * 100 if len(up) else np.nan
        dn_wr = (dn[target_col] < 0).mean() * 100 if len(dn) else np.nan
        return up_wr, dn_wr, len(up), len(dn)

    sox_up_wr, sox_dn_wr, n_sox_up, n_sox_dn = win_rate("SOX_t", "TWII_t1", lead)
    tsm_up_wr, tsm_dn_wr, n_tsm_up, n_tsm_dn = win_rate("TSM_t", "TWII_t1", lead)

    # 同向 (SOX 與 TSM 同向) 時隔日台股同向勝率
    same_up = lead[(lead["SOX_t"] > 0) & (lead["TSM_t"] > 0)]
    same_dn = lead[(lead["SOX_t"] < 0) & (lead["TSM_t"] < 0)]
    same_up_wr = (same_up["TWII_t1"] > 0).mean() * 100 if len(same_up) else np.nan
    same_dn_wr = (same_dn["TWII_t1"] < 0).mean() * 100 if len(same_dn) else np.nan

    win_rates = {
        "SOX上漲->TWII隔日上漲%": round(sox_up_wr, 2),
        "SOX下跌->TWII隔日下跌%": round(sox_dn_wr, 2),
        "TSM上漲->TWII隔日上漲%": round(tsm_up_wr, 2),
        "TSM下跌->TWII隔日下跌%": round(tsm_dn_wr, 2),
        "SOX與TSM同漲->TWII隔日上漲%": round(same_up_wr, 2),
        "SOX與TSM同跌->TWII隔日下跌%": round(same_dn_wr, 2),
        "樣本數(SOX上漲/下跌)": (n_sox_up, n_sox_dn),
        "樣本數(TSM上漲/下跌)": (n_tsm_up, n_tsm_dn),
        "樣本數(同漲/同跌)": (len(same_up), len(same_dn)),
    }

    return corr_full, corr_2y, lead_corr_full, lead_corr_2y, win_rates, len(ret), len(ret_2y)


# ---------------------------------------------------------------
# 分析三: 六大權值股與大盤相關性 / beta
# ---------------------------------------------------------------
def analysis_three(twii, stocks: dict):
    twii_ret = twii["Close"].pct_change().dropna()
    rows = []
    for tkr, df in stocks.items():
        if df is None:
            rows.append(
                {
                    "股票": f"{NAMES_PART3[tkr]}({tkr})",
                    "相關性(全期)": np.nan,
                    "beta(全期)": np.nan,
                    "相關性(近2年)": np.nan,
                    "beta(近2年)": np.nan,
                }
            )
            continue
        ret = df["Close"].pct_change().dropna()
        merged = pd.DataFrame({"stock": ret, "twii": twii_ret}).dropna()
        merged_2y = merged.tail(504)

        corr_full = merged["stock"].corr(merged["twii"])
        cov_full = merged["stock"].cov(merged["twii"])
        var_full = merged["twii"].var()
        beta_full = cov_full / var_full

        corr_2y = merged_2y["stock"].corr(merged_2y["twii"])
        cov_2y = merged_2y["stock"].cov(merged_2y["twii"])
        var_2y = merged_2y["twii"].var()
        beta_2y = cov_2y / var_2y

        rows.append(
            {
                "股票": f"{NAMES_PART3[tkr]}({tkr})",
                "相關性(全期)": round(corr_full, 4),
                "beta(全期)": round(beta_full, 3),
                "相關性(近2年)": round(corr_2y, 4),
                "beta(近2年)": round(beta_2y, 3),
            }
        )
    result = pd.DataFrame(rows).sort_values("相關性(全期)", ascending=False)
    return result


def df_to_md(df, index=True):
    return df.to_markdown(index=index)


def main():
    print("下載資料中...")
    twii = download("^TWII")
    sox = download("^SOX")
    tsm = download("TSM")

    stocks = {}
    for tkr in TICKERS_PART3:
        stocks[tkr] = download(tkr)

    missing = []
    if twii is None:
        missing.append("^TWII")
    if sox is None:
        missing.append("^SOX")
    if tsm is None:
        missing.append("TSM")
    for tkr, df in stocks.items():
        if df is None:
            missing.append(tkr)

    lines = []
    json_data = {}
    lines.append("# 台股大盤分析報告\n")
    lines.append(f"資料下載參數: start={START}, auto_adjust=False (取各標的最長可得歷史)\n")

    lines.append("\n## 各標的實際資料起訖日與樣本天數\n")
    all_series = {"^TWII": twii, "^SOX": sox, "TSM": tsm, **stocks}
    range_rows = []
    meta = {}
    for tkr, df in all_series.items():
        if df is None:
            range_rows.append({"標的": tkr, "起日": "N/A", "訖日": "N/A", "樣本天數": 0})
            meta[tkr] = {"start": None, "end": None, "days": 0}
        else:
            start_d = str(df.index.min().date())
            end_d = str(df.index.max().date())
            range_rows.append(
                {
                    "標的": tkr,
                    "起日": start_d,
                    "訖日": end_d,
                    "樣本天數": len(df),
                }
            )
            meta[tkr] = {"start": start_d, "end": end_d, "days": int(len(df))}
    lines.append(df_to_md(pd.DataFrame(range_rows), index=False))
    lines.append("\n")
    json_data["meta"] = meta

    if missing:
        lines.append(f"\n**注意: 以下標的抓不到資料: {', '.join(missing)}**\n")

    # ---- 分析一 ----
    lines.append("\n## 分析一: 大盤(^TWII) 日內走勢十大類型統計\n")
    if twii is not None:
        result1, n_days, range_p30 = analysis_one(twii)
        lines.append(f"樣本天數: {n_days}\n")
        lines.append(df_to_md(result1))
        lines.append("\n")
        json_data["intraday_types"] = [
            {"name": name, "count": int(row["次數"]), "pct": float(row["百分比(%)"])}
            for name, row in result1.iterrows()
        ]
        json_data["range_p30"] = float(range_p30)
    else:
        lines.append("無法取得 ^TWII 資料，略過。\n")
        json_data["intraday_types"] = []

    # ---- 分析二 ----
    lines.append("\n## 分析二: 大盤 x 費半(^SOX) x 台積電ADR(TSM) 相關性與隔日預測\n")
    if twii is not None and sox is not None and tsm is not None:
        corr_full, corr_2y, lead_full, lead_2y, win_rates, n_full, n_2y = analysis_two(
            twii, sox, tsm
        )
        lines.append(f"樣本天數: 全期 {n_full}, 近2年 {n_2y}\n")
        lines.append("### 同日相關矩陣 (全期日報酬)\n")
        lines.append(df_to_md(corr_full.round(4)))
        lines.append("\n### 同日相關矩陣 (近2年日報酬)\n")
        lines.append(df_to_md(corr_2y.round(4)))
        lines.append("\n### 隔夜領先相關 (t日美股 vs t+1日台股)\n")
        lines.append(
            df_to_md(
                pd.DataFrame(
                    {"全期": lead_full, "近2年": lead_2y}
                ).round(4)
            )
        )
        lines.append("\n### 方向勝率 (全期)\n")
        wr_rows = {
            k: v for k, v in win_rates.items() if not k.startswith("樣本數")
        }
        lines.append(
            df_to_md(pd.DataFrame(wr_rows.items(), columns=["條件", "勝率(%)"]), index=False)
        )
        lines.append("\n樣本數: " + str({k: v for k, v in win_rates.items() if k.startswith("樣本數")}) + "\n")

        json_data["correlations"] = {
            "full": {
                "twii_sox": float(corr_full.loc["TWII", "SOX"]),
                "twii_tsm": float(corr_full.loc["TWII", "TSM"]),
                "sox_tsm": float(corr_full.loc["SOX", "TSM"]),
            },
            "recent2y": {
                "twii_sox": float(corr_2y.loc["TWII", "SOX"]),
                "twii_tsm": float(corr_2y.loc["TWII", "TSM"]),
                "sox_tsm": float(corr_2y.loc["SOX", "TSM"]),
            },
        }
        json_data["overnight_lead"] = {
            "full": {
                "sox_to_twii_next": float(lead_full["SOX_lead_TWII"]),
                "tsm_to_twii_next": float(lead_full["TSM_lead_TWII"]),
            },
            "recent2y": {
                "sox_to_twii_next": float(lead_2y["SOX_lead_TWII"]),
                "tsm_to_twii_next": float(lead_2y["TSM_lead_TWII"]),
            },
        }
        json_data["hit_rates"] = {
            "sox_up_twii_up": float(win_rates["SOX上漲->TWII隔日上漲%"]),
            "sox_down_twii_down": float(win_rates["SOX下跌->TWII隔日下跌%"]),
            "tsm_up_twii_up": float(win_rates["TSM上漲->TWII隔日上漲%"]),
            "tsm_down_twii_down": float(win_rates["TSM下跌->TWII隔日下跌%"]),
            "sox_tsm_same_dir_twii_same": {
                "up": float(win_rates["SOX與TSM同漲->TWII隔日上漲%"]),
                "down": float(win_rates["SOX與TSM同跌->TWII隔日下跌%"]),
            },
        }
    else:
        lines.append("缺少必要資料，略過。\n")
        json_data["correlations"] = {}
        json_data["overnight_lead"] = {}
        json_data["hit_rates"] = {}

    # ---- 分析三 ----
    lines.append("\n## 分析三: 六大權值股與大盤(^TWII)相關性/beta\n")
    if twii is not None:
        result3 = analysis_three(twii, stocks)
        lines.append(df_to_md(result3, index=False))
        lines.append("\n")

        weight_stocks = []
        for _, row in result3.iterrows():
            # "股票" 欄格式為 "名稱(代號)"
            label = row["股票"]
            name, code = label[:-1].split("(")
            weight_stocks.append(
                {
                    "code": code,
                    "name": name,
                    "corr_full": None if pd.isna(row["相關性(全期)"]) else float(row["相關性(全期)"]),
                    "beta_full": None if pd.isna(row["beta(全期)"]) else float(row["beta(全期)"]),
                    "corr_2y": None if pd.isna(row["相關性(近2年)"]) else float(row["相關性(近2年)"]),
                    "beta_2y": None if pd.isna(row["beta(近2年)"]) else float(row["beta(近2年)"]),
                }
            )
        json_data["weight_stocks"] = weight_stocks
    else:
        lines.append("無法取得 ^TWII 資料，略過。\n")
        json_data["weight_stocks"] = []

    report = "\n".join(lines)
    with open("/Users/steven/CCProject/market-analysis/report.md", "w", encoding="utf-8") as f:
        f.write(report)
    print("報告已寫入 report.md")

    with open("/Users/steven/CCProject/market-analysis/analysis_data.json", "w", encoding="utf-8") as f:
        json.dump(json_data, f, ensure_ascii=False, indent=2)
    print("結構化資料已寫入 analysis_data.json")


if __name__ == "__main__":
    main()
