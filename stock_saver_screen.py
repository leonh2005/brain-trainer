"""
台股存股篩選器
篩選條件：股價≤50、穩定向上趨勢、低基期、穩定配息、EPS穩定成長
資料來源：FinMind API
"""

import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time
import warnings
warnings.filterwarnings('ignore')

# FinMind 設定
with open("/Users/steven/CCProject/.secrets/finmind_token.txt") as f:
    TOKEN = f.read().strip()
URL = "https://api.finmindtrade.com/api/v4/data"

TODAY = datetime.now().strftime("%Y-%m-%d")
ONE_YEAR_AGO = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
THREE_YEARS_AGO = (datetime.now() - timedelta(days=365*3)).strftime("%Y-%m-%d")


def fetch(dataset, stock_id=None, start=None, extra_params=None):
    params = {"dataset": dataset, "token": TOKEN}
    if stock_id:
        params["data_id"] = stock_id
    if start:
        params["start_date"] = start
    if extra_params:
        params.update(extra_params)
    resp = requests.get(URL, params=params, timeout=30)
    data = resp.json()
    if data.get("status") != 200 or not data.get("data"):
        return pd.DataFrame()
    return pd.DataFrame(data["data"])


# ===== Step 1: 取得全市場股票清單 =====
print("=" * 60)
print("Step 1: 取得台股清單...")
info = fetch("TaiwanStockInfo")
if info.empty:
    print("無法取得股票清單")
    exit(1)

# 只保留上市上櫃普通股（排除 ETF、權證、特別股等）
# stock_id 為純數字 4 碼，且 type 為 stock
info = info[info["stock_id"].str.match(r"^\d{4}$", na=False)].copy()
# 排除 KY 股、F 股
info = info[~info["stock_name"].str.contains("KY|-KY|F-", na=False)].copy()
print(f"  普通股數量: {len(info)}")

# ===== Step 2: 用種子清單 + 低價股篩選 =====
# 先抓一批種子股 + 隨機低價股的近期價格
# 為了效率，我們分批抓最近的收盤價

# 種子清單：知名低價傳產/金融/民生
seeds = [
    "1201", "1210", "1213", "1215", "1217", "1218", "1225", "1227",
    "1229", "1231", "1232", "1233", "1234", "1301", "1303", "1304",
    "1308", "1309", "1310", "1312", "1313", "1314", "1315", "1316",
    "1319", "1321", "1323", "1324", "1325", "1326", "1402", "1409",
    "1410", "1414", "1416", "1417", "1418", "1419", "1423", "1432",
    "1434", "1435", "1436", "1437", "1438", "1439", "1440", "1441",
    "1442", "1443", "1444", "1445", "1446", "1447", "1449", "1451",
    "1452", "1453", "1454", "1455", "1456", "1457", "1459", "1460",
    "1463", "1464", "1465", "1466", "1467", "1468", "1470", "1471",
    "1472", "1473", "1474", "1475", "1476", "1477", "2002", "2006",
    "2007", "2008", "2010", "2012", "2013", "2014", "2015", "2017",
    "2020", "2022", "2023", "2024", "2025", "2027", "2028", "2029",
    "2030", "2031", "2032", "2033", "2034", "2035", "2038", "2040",
    "2101", "2102", "2103", "2104", "2106", "2107", "2108", "2109",
    "2201", "2204", "2206", "2208", "2211", "2213", "2227", "2228",
    "2231", "2233", "2236", "2239", "2241", "2243", "2247",
    "2312", "2313", "2314", "2316", "2323", "2328", "2332", "2337",
    "2340", "2342", "2344", "2348", "2352", "2355", "2359", "2362",
    "2364", "2367", "2369", "2371", "2373", "2374", "2375", "2376",
    "2380", "2383", "2385", "2387", "2388", "2390", "2392", "2393",
    "2397", "2399", "2401", "2404", "2405", "2406", "2408", "2409",
    "2412", "2413", "2414", "2415", "2416", "2417", "2419", "2420",
    "2421", "2423", "2424", "2425", "2426", "2427", "2428", "2429",
    "2431", "2432", "2433", "2434", "2436", "2438", "2439", "2440",
    "2441", "2442", "2443", "2444", "2449", "2451", "2457",
    "2801", "2809", "2812", "2816", "2820", "2832", "2834", "2836",
    "2838", "2841", "2845", "2847", "2849", "2850", "2851", "2852",
    "2855", "2856", "2867", "2880", "2881", "2882", "2883", "2884",
    "2885", "2886", "2887", "2888", "2889", "2890", "2891", "2892",
    "2897", "2901", "2903", "2904", "2905", "2906", "2908", "2911",
    "2912", "2913", "2915", "2923", "2924", "2929",
    "3006", "3011", "3014", "3015", "3016", "3017", "3019", "3021",
    "3022", "3023", "3024", "3025", "3026", "3027", "3028", "3029",
    "3030", "3031", "3032", "3033", "3034", "3035", "3036", "3037",
    "3038", "3040", "3041", "3042", "3043", "3044", "3045", "3046",
    "3047", "3048", "3049", "3050", "3051", "3052", "3054", "3055",
    "3056", "3057", "3058", "3059", "3060", "3062",
    "4104", "4106", "4108", "4119", "4130", "4133", "4137", "4138",
    "4142", "4144", "4147", "4148", "4152", "4153", "4155", "4157",
    "4160", "4161", "4162", "4164", "4168", "4171", "4174", "4175",
    "4180", "4190", "4192", "4195", "4197",
    "4526", "4528", "4532", "4536", "4541", "4545", "4551", "4552",
    "4555", "4557", "4560", "4562", "4564", "4566", "4568", "4572",
    "5202", "5203", "5204", "5206", "5209", "5210", "5212", "5213",
    "5215", "5222", "5225", "5227", "5234", "5243", "5258", "5259",
    "5264", "5269", "5274", "5276", "5283", "5284", "5285", "5288",
    "5306", "5312", "5315", "5317", "5321", "5328", "5340", "5344",
    "5345", "5347", "5348", "5349", "5351", "5353", "5355", "5356",
    "5360", "5364", "5371", "5381", "5386", "5388", "5392", "5398",
    "5425", "5426", "5434", "5438", "5439", "5443",
    "5880", "6005", "6024", "6116", "6139", "6142", "6152", "6153",
    "6155", "6164", "6166", "6168", "6172", "6176", "6177", "6184",
    "6189", "6192", "6196", "6197", "6199", "6201", "6202", "6205",
    "6206", "6209", "6210", "6214", "6215", "6216", "6220", "6223",
    "6225", "6226", "6227", "6230", "6233", "6235", "6239", "6241",
    "6243", "6244", "6245", "6246", "6257", "6259", "6261", "6263",
    "6269", "6271", "6274", "6277", "6278", "6279", "6281", "6282",
    "6283", "6284", "6285", "6288", "6289", "6290", "6291",
    "8011", "8016", "8021", "8028", "8033", "8038", "8039", "8040",
    "8042", "8044", "8046", "8047", "8049", "8050", "8059", "8070",
    "8072", "8076", "8081", "8086", "8101", "8103", "8104", "8105",
    "8107", "8109", "8110", "8112", "8114", "8131", "8150", "8155",
    "8163", "8171", "8201", "8210", "8213", "8215", "8222", "8234",
    "8240", "8249", "8261", "8271", "8341", "8342", "8349", "8354",
    "8358", "8367", "8374", "8383", "8390", "8401", "8404", "8416",
    "8422", "8426", "8427", "8429", "8430", "8432", "8436", "8438",
    "8440", "8442", "8443", "8444", "8446", "8450", "8454", "8462",
    "8463", "8464", "8466", "8467", "8473", "8476", "8478",
    "9802", "9902", "9904", "9905", "9906", "9907", "9908", "9910",
    "9911", "9912", "9914", "9917", "9918", "9919", "9921", "9924",
    "9925", "9926", "9927", "9928", "9929", "9930", "9931", "9933",
    "9934", "9935", "9937", "9938", "9939", "9940", "9941", "9942",
    "9943", "9944", "9945", "9946", "9951", "9955", "9958",
]

# 過濾只保留存在於 info 中的 stock_id
valid_ids = set(info["stock_id"].tolist())
candidates = [s for s in seeds if s in valid_ids]
print(f"  種子候選股數量: {len(candidates)}")

# ===== Step 3: 抓近期價格，篩選 ≤ 50 TWD =====
print("\nStep 2: 抓取近期價格，篩選股價 ≤ 50...")
RECENT_START = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")

low_price_stocks = []
batch_size = 20
for i in range(0, len(candidates), batch_size):
    batch = candidates[i:i+batch_size]
    for sid in batch:
        try:
            df = fetch("TaiwanStockPrice", sid, RECENT_START)
            if df.empty:
                continue
            df["close"] = pd.to_numeric(df["close"], errors="coerce")
            last_close = df["close"].iloc[-1]
            if last_close <= 50 and last_close > 0:
                name_row = info[info["stock_id"] == sid]
                sname = name_row["stock_name"].iloc[0] if not name_row.empty else ""
                low_price_stocks.append({
                    "stock_id": sid,
                    "stock_name": sname,
                    "last_close": last_close
                })
        except Exception as e:
            continue
    print(f"  已處理 {min(i+batch_size, len(candidates))}/{len(candidates)} 支...")
    time.sleep(1)

print(f"  股價 ≤ 50 的候選股: {len(low_price_stocks)} 支")

if len(low_price_stocks) == 0:
    print("沒有找到符合條件的股票")
    exit(1)

# ===== Step 4: 對每支候選股做深度分析 =====
print(f"\nStep 3: 深度分析 {len(low_price_stocks)} 支候選股...")

results = []
for idx, stock in enumerate(low_price_stocks):
    sid = stock["stock_id"]
    sname = stock["stock_name"]
    current_price = stock["last_close"]

    try:
        # 4a. 近一年股價 - 線性回歸
        price_df = fetch("TaiwanStockPrice", sid, ONE_YEAR_AGO)
        if price_df.empty or len(price_df) < 100:
            continue

        price_df["close"] = pd.to_numeric(price_df["close"], errors="coerce")
        price_df = price_df.dropna(subset=["close"])
        closes = price_df["close"].values

        # 線性回歸
        x = np.arange(len(closes))
        slope, intercept = np.polyfit(x, closes, 1)

        # 年漲幅
        first_close = closes[0]
        year_return = (current_price - first_close) / first_close * 100

        # 穩定度：R² 值
        predicted = slope * x + intercept
        ss_res = np.sum((closes - predicted) ** 2)
        ss_tot = np.sum((closes - np.mean(closes)) ** 2)
        r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0

        # 排除暴漲股（年漲幅 > 80% 或 R² 太低表示不穩定）
        if year_return > 80:
            continue
        # 需要正斜率（向上趨勢）
        if slope <= 0:
            continue

        # 最高最低價判斷低基期
        high_1y = closes.max()
        low_1y = closes.min()
        pct_from_high = (high_1y - current_price) / high_1y * 100

        # 4b. 配息紀錄（近3年）
        div_df = fetch("TaiwanStockDividend", sid, THREE_YEARS_AGO)

        total_cash_div = 0
        div_years = set()
        latest_div = 0
        if not div_df.empty:
            if "CashEarningsDistribution" in div_df.columns:
                div_df["CashEarningsDistribution"] = pd.to_numeric(
                    div_df["CashEarningsDistribution"], errors="coerce"
                ).fillna(0)
            if "CashStaticDistribution" in div_df.columns:
                div_df["CashStaticDistribution"] = pd.to_numeric(
                    div_df["CashStaticDistribution"], errors="coerce"
                ).fillna(0)

            if "CashEarningsDistribution" in div_df.columns:
                div_df["total_cash"] = div_df.get("CashEarningsDistribution", 0) + div_df.get("CashStaticDistribution", 0)
            else:
                div_df["total_cash"] = 0

            if "date" in div_df.columns:
                div_df["year"] = pd.to_datetime(div_df["date"]).dt.year
                div_years = set(div_df[div_df["total_cash"] > 0]["year"].tolist())

            if len(div_df) > 0 and "total_cash" in div_df.columns:
                latest_div = div_df["total_cash"].iloc[-1]
                total_cash_div = div_df["total_cash"].sum()

        # 殖利率
        div_yield = (latest_div / current_price * 100) if current_price > 0 and latest_div > 0 else 0

        # 配息年數
        recent_years = {2023, 2024, 2025, 2026}
        div_coverage = len(div_years & recent_years)

        # 4c. EPS（近3年財報）
        fin_df = fetch("TaiwanStockFinancialStatements", sid, THREE_YEARS_AGO)

        eps_trend = "N/A"
        avg_eps = 0
        eps_values = []
        if not fin_df.empty and "type" in fin_df.columns:
            eps_df = fin_df[fin_df["type"] == "EPS"].copy()
            if not eps_df.empty:
                eps_df["value"] = pd.to_numeric(eps_df["value"], errors="coerce")
                eps_df = eps_df.dropna(subset=["value"])
                if len(eps_df) >= 2:
                    eps_values = eps_df["value"].tolist()
                    avg_eps = np.mean(eps_values)
                    # 判斷趨勢：用年度加總
                    eps_df["date"] = pd.to_datetime(eps_df["date"])
                    eps_df["year"] = eps_df["date"].dt.year
                    yearly_eps = eps_df.groupby("year")["value"].sum()
                    if len(yearly_eps) >= 2:
                        eps_slope = np.polyfit(range(len(yearly_eps)), yearly_eps.values, 1)[0]
                        if eps_slope > 0.1:
                            eps_trend = "成長"
                        elif eps_slope > -0.1:
                            eps_trend = "穩定"
                        else:
                            eps_trend = "衰退"

        # 排除 EPS 衰退或太低
        if eps_trend == "衰退":
            continue
        if avg_eps < 0.5:
            continue

        # ===== 綜合評分 =====
        score = 0

        # 趨勢穩定向上（R² 越高越好，斜率為正）
        score += r_squared * 25  # 0~25

        # 年漲幅合理（5%~30% 最佳）
        if 5 <= year_return <= 30:
            score += 20
        elif 0 < year_return < 5:
            score += 10
        elif 30 < year_return <= 50:
            score += 10

        # 殖利率
        if div_yield >= 5:
            score += 25
        elif div_yield >= 3:
            score += 20
        elif div_yield >= 1:
            score += 10

        # 配息穩定度（近3年都有配）
        score += div_coverage * 5  # 0~15

        # EPS 趨勢
        if eps_trend == "成長":
            score += 15
        elif eps_trend == "穩定":
            score += 10

        # 低基期加分（距高點越遠越好，但不要太遠表示在跌）
        if 5 <= pct_from_high <= 20:
            score += 10
        elif pct_from_high < 5:
            score += 5  # 接近高點，稍微扣分意義

        results.append({
            "stock_id": sid,
            "stock_name": sname,
            "price": current_price,
            "year_return_pct": round(year_return, 1),
            "slope": round(slope, 4),
            "r_squared": round(r_squared, 3),
            "div_yield_pct": round(div_yield, 2),
            "div_years": div_coverage,
            "eps_trend": eps_trend,
            "avg_eps": round(avg_eps, 2),
            "pct_from_high": round(pct_from_high, 1),
            "score": round(score, 1),
        })

    except Exception as e:
        continue

    if (idx + 1) % 10 == 0:
        print(f"  已分析 {idx+1}/{len(low_price_stocks)} 支...")
        time.sleep(0.5)

# ===== Step 5: 排名輸出 =====
print("\n" + "=" * 80)
print("  台股存股篩選結果 — TOP 10")
print("=" * 80)

if not results:
    print("沒有符合所有條件的股票")
    exit(0)

results_df = pd.DataFrame(results)
results_df = results_df.sort_values("score", ascending=False).head(15)

print(f"\n{'排名':>4} | {'代號':>6} | {'股名':>8} | {'現價':>6} | {'年漲%':>6} | {'R²':>5} | {'殖利率%':>7} | {'配息年':>4} | {'EPS趨勢':>6} | {'平均EPS':>7} | {'距高%':>5} | {'分數':>5}")
print("-" * 110)

for rank, (_, row) in enumerate(results_df.iterrows(), 1):
    if rank > 10:
        break
    print(f"{rank:>4} | {row['stock_id']:>6} | {row['stock_name']:>8} | "
          f"{row['price']:>6.1f} | {row['year_return_pct']:>6.1f} | "
          f"{row['r_squared']:>5.3f} | {row['div_yield_pct']:>7.2f} | "
          f"{row['div_years']:>4} | {row['eps_trend']:>6} | "
          f"{row['avg_eps']:>7.2f} | {row['pct_from_high']:>5.1f} | "
          f"{row['score']:>5.1f}")

print("\n" + "=" * 80)
print("\n各股推薦理由：")
print("-" * 60)

for rank, (_, row) in enumerate(results_df.iterrows(), 1):
    if rank > 10:
        break
    reasons = []
    if row["r_squared"] >= 0.6:
        reasons.append("趨勢穩定（R²高）")
    if row["year_return_pct"] >= 5:
        reasons.append(f"穩健上漲{row['year_return_pct']:.0f}%")
    if row["div_yield_pct"] >= 5:
        reasons.append(f"高殖利率{row['div_yield_pct']:.1f}%")
    elif row["div_yield_pct"] >= 3:
        reasons.append(f"殖利率{row['div_yield_pct']:.1f}%")
    if row["div_years"] >= 3:
        reasons.append("連續配息穩定")
    if row["eps_trend"] == "成長":
        reasons.append("EPS持續成長")
    elif row["eps_trend"] == "穩定":
        reasons.append("EPS穩定")
    if row["pct_from_high"] >= 10:
        reasons.append("低基期有空間")

    reason_str = "、".join(reasons) if reasons else "綜合評分較高"
    print(f"  {rank}. {row['stock_id']} {row['stock_name']}（{row['price']:.1f}元）：{reason_str}")

print("\n⚠ 以上為量化篩選結果，投資前請自行研究基本面與產業前景。")
