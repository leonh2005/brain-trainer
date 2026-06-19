from flask import Flask, render_template, jsonify, request
from dotenv import load_dotenv
load_dotenv()
import json
import requests as _req
import time
from datetime import date, timedelta
from pathlib import Path
from analysis import get_portfolio_data, PORTFOLIO_FILE
from ai_masters import get_all_analyses
from dcf import get_dcf_data

FINMIND_TOKEN = Path('/Users/steven/CCProject/.secrets/finmind_token.txt').read_text().strip()
_whale_cache: dict = {"data": {}, "time": 0}
_WHALE_TTL = 1800  # 30 分鐘

TDCC_CACHE_FILE = Path(__file__).parent / "tdcc_cache.json"
_tdcc_cache: dict = {"data": {}, "time": 0}
_TDCC_TTL = 3600 * 4  # 4 小時

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/data")
def api_data():
    try:
        return jsonify({"ok": True, "data": get_portfolio_data()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/refresh")
def api_refresh():
    try:
        return jsonify({"ok": True, "data": get_portfolio_data(force_refresh=True)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/ai-analysis")
def api_ai():
    try:
        force = request.args.get("refresh") == "1"
        portfolio_data = get_portfolio_data()
        results = get_all_analyses(portfolio_data, force_refresh=force)
        return jsonify({"ok": True, "data": results})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


def _normalize_ticker(ticker: str) -> str:
    """自動補台股後綴：純數字或數字開頭（如 2317、00687B）補 .TW，英文開頭視為美股"""
    import re
    ticker = ticker.strip().upper()
    if not ticker:
        return ticker
    if "." in ticker:
        return ticker  # 已有後綴，不動
    if re.match(r"^\d", ticker):
        return ticker + ".TW"  # 台股
    return ticker  # 美股（字母開頭）


@app.route("/api/positions", methods=["POST"])
def save_positions():
    try:
        positions = request.json
        if not isinstance(positions, list):
            return jsonify({"ok": False, "error": "格式錯誤"}), 400
        with open(PORTFOLIO_FILE) as f:
            portfolio = json.load(f)
        taiwan, us = [], []
        for p in positions:
            raw = p.get("ticker", "").strip()
            if not raw:
                continue
            ticker = _normalize_ticker(raw)
            entry = {"ticker": ticker, "name": p.get("name", ""), "shares": int(p.get("shares", 0))}
            if ticker.endswith(".TW") or ticker.endswith(".TWO"):
                taiwan.append(entry)
            else:
                us.append(entry)
        portfolio["taiwan"] = taiwan
        portfolio["us"] = us
        with open(PORTFOLIO_FILE, "w") as f:
            json.dump(portfolio, f, ensure_ascii=False, indent=2)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/targets", methods=["POST"])
def save_targets():
    try:
        targets = request.json
        if not isinstance(targets, list):
            return jsonify({"ok": False, "error": "格式錯誤"}), 400
        with open(PORTFOLIO_FILE) as f:
            portfolio = json.load(f)
        portfolio["target"] = targets
        with open(PORTFOLIO_FILE, "w") as f:
            json.dump(portfolio, f, ensure_ascii=False, indent=2)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/dcf")
def api_dcf():
    try:
        force = request.args.get("refresh") == "1"
        portfolio_data = get_portfolio_data()
        results = get_dcf_data(portfolio_data["positions"], force_refresh=force)
        return jsonify({"ok": True, "data": results})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/whale")
def api_whale():
    global _whale_cache
    force = request.args.get("refresh") == "1"
    now = time.time()
    if not force and _whale_cache["data"] and now - _whale_cache["time"] < _WHALE_TTL:
        return jsonify({"ok": True, "data": _whale_cache["data"]})

    portfolio = json.loads(Path(PORTFOLIO_FILE).read_text())
    tw_stocks = [p["ticker"].replace(".TW", "").replace(".TWO", "") for p in portfolio.get("taiwan", [])]

    result = {}
    start = str(date.today() - timedelta(days=7))
    for code in tw_stocks:
        try:
            r = _req.get("https://api.finmindtrade.com/api/v4/data", params={
                "dataset": "TaiwanStockInstitutionalInvestorsBuySell",
                "data_id": code, "start_date": start, "token": FINMIND_TOKEN
            }, timeout=8)
            rows = r.json().get("data", [])
            if not rows:
                continue
            # 取最新交易日
            latest_date = sorted({x["date"] for x in rows})[-1]
            day_rows = [x for x in rows if x["date"] == latest_date]
            net_foreign = sum(x["buy"] - x["sell"] for x in day_rows if x["name"] == "Foreign_Investor")
            net_trust = sum(x["buy"] - x["sell"] for x in day_rows if x["name"] == "Investment_Trust")
            net_dealer = sum(x["buy"] - x["sell"] for x in day_rows if x["name"] in ("Dealer_self", "Dealer_Hedging"))
            total = net_foreign + net_trust + net_dealer
            result[code] = {
                "date": latest_date,
                "foreign": round(net_foreign / 1000),   # 張
                "trust": round(net_trust / 1000),
                "dealer": round(net_dealer / 1000),
                "total": round(total / 1000),
            }
        except Exception:
            pass

    _whale_cache = {"data": result, "time": now}
    return jsonify({"ok": True, "data": result})


def _tdcc_query_prev_week(s, firm_date, prev_date, codes: list) -> dict:
    """用 TDCC 表單查前週各股千張以上大戶人數（每次重取 token）"""
    import re
    result = {}
    for code in codes:
        try:
            page = s.get("https://www.tdcc.com.tw/portal/zh/smWeb/qryStock", verify=False, timeout=10)
            token = re.search(r'SYNCHRONIZER_TOKEN.*?value=["\']([^"\']+)["\']', page.text).group(1)
            r = s.post(
                "https://www.tdcc.com.tw/portal/zh/smWeb/qryStock",
                data={
                    "SYNCHRONIZER_TOKEN": token,
                    "SYNCHRONIZER_URI": "/portal/zh/smWeb/qryStock",
                    "method": "submit",
                    "firDate": firm_date,
                    "scaDate": prev_date,
                    "sqlMethod": "StockNo",
                    "stockNo": code,
                    "stockName": "",
                    "REQ_TYPE": "",
                },
                headers={"Referer": "https://www.tdcc.com.tw/portal/zh/smWeb/qryStock"},
                verify=False, timeout=15,
            )
            rows = re.findall(r"<tr[^>]*>(.*?)</tr>", r.text, re.DOTALL)
            for row in rows:
                cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
                cells = [re.sub(r"<[^>]+>", "", c).strip().replace(",", "") for c in cells]
                if cells and cells[0] == "15":
                    result[code] = {"人數": int(cells[2])}
                    break
        except Exception:
            pass
    return result


@app.route("/api/tdcc")
def api_tdcc():
    import io, warnings, re, pandas as pd
    global _tdcc_cache
    force = request.args.get("refresh") == "1"
    now = time.time()
    if not force and _tdcc_cache["data"] and now - _tdcc_cache["time"] < _TDCC_TTL:
        return jsonify({"ok": True, "data": _tdcc_cache["data"]})

    try:
        warnings.filterwarnings("ignore")
        s = _req.Session()
        s.headers.update({"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"})
        page = s.get("https://www.tdcc.com.tw/portal/zh/smWeb/qryStock", verify=False, timeout=10)
        token = re.search(r'SYNCHRONIZER_TOKEN.*?value=["\']([^"\']+)["\']', page.text).group(1)

        # 取可用日期列表（最新=本週，次新=上週）
        available_dates = re.findall(r'<option[^>]*value="(20\d{6})"', page.text)
        available_dates = sorted(set(available_dates), reverse=True)
        firm_date = available_dates[0] if available_dates else str(date.today()).replace("-", "")
        prev_date = available_dates[1] if len(available_dates) > 1 else firm_date

        r = s.get("https://opendata.tdcc.com.tw/getOD.ashx", params={"id": "1-5"},
                  headers={"Referer": "https://www.tdcc.com.tw/"}, verify=False, timeout=60)
        df = pd.read_csv(io.StringIO(r.text.lstrip("﻿")))
        df["證券代號"] = df["證券代號"].astype(str).str.strip()

        # 只取千張以上（level 15）
        whale = df[df["持股分級"] == 15].set_index("證券代號")[["資料日期", "人數"]].to_dict("index")
        current_date = str(df["資料日期"].iloc[0])

        portfolio = json.loads(Path(PORTFOLIO_FILE).read_text())
        tw_codes = [p["ticker"].replace(".TW", "").replace(".TWO", "") for p in portfolio.get("taiwan", [])]

        # 讀舊快取判斷是否需要直接查前週
        prev: dict = {}
        if TDCC_CACHE_FILE.exists():
            cached = json.loads(TDCC_CACHE_FILE.read_text())
            if cached.get("date") != current_date:
                prev = cached.get("whale", {})

        # 若無前週快取，直接 POST 查前週
        if not prev and prev_date != firm_date:
            prev = _tdcc_query_prev_week(s, firm_date, prev_date, tw_codes)

        # 存本週快取（含前週供下次使用）
        TDCC_CACHE_FILE.write_text(json.dumps(
            {"date": current_date, "prev_date": prev_date,
             "whale": {k: {"人數": v["人數"]} for k, v in whale.items()},
             "prev_whale": prev},
            ensure_ascii=False
        ))

        result = {}
        for code in tw_codes:
            if code not in whale:
                continue
            cur_ppl = int(whale[code]["人數"])
            prev_ppl = int(prev.get(code, {}).get("人數", cur_ppl))
            result[code] = {
                "date": current_date,
                "prev_date": prev_date,
                "people": cur_ppl,
                "prev_people": prev_ppl,
                "diff": cur_ppl - prev_ppl,
            }

        _tdcc_cache = {"data": result, "time": now}
        return jsonify({"ok": True, "data": result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5800, debug=False)
