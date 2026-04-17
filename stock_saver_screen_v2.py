"""
台股存股篩選器 v2
聚焦精選低價候選股，確保資料完整
"""

import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time
import warnings
warnings.filterwarnings('ignore')

with open("/Users/steven/CCProject/.secrets/finmind_token.txt") as f:
    TOKEN = f.read().strip()
URL = "https://api.finmindtrade.com/api/v4/data"

TODAY = datetime.now().strftime("%Y-%m-%d")
ONE_YEAR_AGO = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
THREE_YEARS_AGO = (datetime.now() - timedelta(days=365*3)).strftime("%Y-%m-%d")


def fetch(dataset, stock_id=None, start=None):
    params = {"dataset": dataset, "token": TOKEN}
    if stock_id:
        params["data_id"] = stock_id
    if start:
        params["start_date"] = start
    for attempt in range(3):
        try:
            resp = requests.get(URL, params=params, timeout=30)
            data = resp.json()
            if data.get("status") != 200 or not data.get("data"):
                if attempt < 2:
                    time.sleep(3)
                    continue
                return pd.DataFrame()
            return pd.DataFrame(data["data"])
        except Exception:
            if attempt < 2:
                time.sleep(3)
            continue
    return pd.DataFrame()


# 精選候選清單：知名低價存股標的（傳產、金融、民生、紡織等）
# 挑選過去常被討論的低價績優股
CANDIDATES = [
    # 金融股
    ("2880", "華南金"), ("2881", "富邦金"), ("2882", "國泰金"),
    ("2883", "開發金"), ("2884", "玉山金"), ("2885", "元大金"),
    ("2886", "兆豐金"), ("2887", "台新金"), ("2888", "新光金"),
    ("2889", "國票金"), ("2890", "永豐金"), ("2891", "中信金"),
    ("2892", "第一金"), ("2897", "王道銀"), ("5880", "合庫金"),
    ("2809", "京城銀"), ("2812", "台中銀"), ("2816", "旺旺保"),
    ("2820", "華票"), ("2832", "台產"), ("2836", "高雄銀"),
    ("2838", "聯邦銀"), ("2845", "遠東銀"), ("2849", "安泰銀"),
    ("2850", "新產"), ("2851", "中再保"), ("2852", "第一保"),
    ("2855", "統一證"), ("2856", "元富證"),
    # 傳產
    ("1201", "味全"), ("1210", "大成"), ("1215", "卜蜂"),
    ("1217", "愛之味"), ("1218", "泰山"), ("1225", "福懋油"),
    ("1227", "佳格"), ("1229", "聯華"), ("1231", "聯華食"),
    ("1232", "大統益"), ("1233", "天仁"), ("1234", "黑松"),
    ("1301", "台塑"), ("1303", "南亞"), ("1304", "台聚"),
    ("1308", "亞聚"), ("1309", "台達化"), ("1310", "台苯"),
    ("1312", "國喬"), ("1313", "聯成"), ("1314", "中石化"),
    ("1316", "上曜"), ("1319", "東陽"), ("1321", "大洋"),
    ("1326", "台化"), ("1402", "遠東新"), ("1409", "新纖"),
    ("1410", "南染"), ("1414", "東和"), ("1416", "廣豐"),
    ("1418", "東華"), ("1423", "利華"), ("1434", "福懋"),
    ("1440", "南紡"), ("1444", "力麗"), ("1445", "大宇"),
    ("1446", "宏和"), ("1447", "力鵬"), ("1449", "佳和"),
    ("1451", "年興"), ("1452", "宏益"), ("1453", "大將"),
    ("1454", "台富"), ("1455", "集盛"), ("1456", "怡華"),
    ("1457", "宜進"), ("1459", "聯發"),
    # 鋼鐵
    ("2002", "中鋼"), ("2006", "東和鋼"), ("2007", "燁興"),
    ("2008", "高興昌"), ("2010", "春源"), ("2012", "春雨"),
    ("2013", "中鋼構"), ("2014", "中鴻"), ("2015", "豐興"),
    ("2017", "官田鋼"), ("2020", "美亞"), ("2022", "聚亨"),
    ("2023", "燁輝"), ("2024", "志聯"), ("2025", "千興"),
    ("2027", "大成鋼"), ("2028", "威致"), ("2029", "盛餘"),
    ("2030", "彰源"), ("2031", "新光鋼"), ("2032", "新鋼"),
    ("2033", "佳大"), ("2034", "允強"),
    # 營建 / 資產 / 航運
    ("2501", "國建"), ("2504", "國產"), ("2505", "國揚"),
    ("2511", "太子"), ("2514", "龍邦"), ("2515", "中工"),
    ("2520", "冠德"), ("2524", "京城"), ("2527", "宏璟"),
    ("2528", "皇普"), ("2530", "華建"), ("2534", "宏盛"),
    ("2535", "達欣工"), ("2536", "宏普"), ("2537", "聯上"),
    ("2538", "基泰"), ("2539", "櫻花建"), ("2540", "愛山林"),
    ("2545", "皇翔"), ("2546", "根基"), ("2547", "日勝生"),
    ("2597", "潤弘"), ("2601", "益航"), ("2603", "長榮"),
    ("2605", "新興"), ("2606", "裕民"), ("2607", "榮運"),
    ("2608", "嘉里大榮"), ("2609", "陽明"), ("2610", "華航"),
    ("2611", "志信"), ("2612", "中航"), ("2613", "中櫃"),
    ("2614", "東森"), ("2615", "萬海"), ("2616", "山隆"),
    ("2617", "台航"),
    # 電子 - 低價
    ("2301", "光寶科"), ("2312", "金寶"), ("2313", "華通"),
    ("2314", "台半"), ("2316", "楠梓電"), ("2323", "中環"),
    ("2328", "廣宇"), ("2332", "友訊"), ("2340", "光磊"),
    ("2342", "茂矽"), ("2344", "華邦電"), ("2348", "海昌"),
    ("2352", "佳世達"), ("2355", "敬鵬"), ("2359", "所羅門"),
    ("2362", "藍天"), ("2364", "倫飛"), ("2367", "燿華"),
    ("2371", "大同"), ("2373", "震旦行"), ("2374", "佳能"),
    ("2375", "智寶"), ("2376", "技嘉"), ("2380", "虹光"),
    ("2383", "台光電"), ("2385", "群光"), ("2387", "精元"),
    ("2388", "威盛"), ("2390", "云辰"), ("2393", "億光"),
    ("2399", "映泰"), ("2401", "凌陽"), ("2404", "漢唐"),
    ("2406", "國碩"), ("2408", "南亞科"), ("2413", "環科"),
    ("2414", "精技"), ("2415", "錩新"), ("2416", "釩創"),
    ("2417", "圓剛"), ("2419", "仲琦"), ("2420", "新巨"),
    ("2421", "建準"), ("2425", "承啟"), ("2426", "鼎元"),
    ("2427", "三商電"), ("2428", "興勤"), ("2429", "銘旺科"),
    ("2431", "聯昌"), ("2433", "互盛電"), ("2434", "統懋"),
    ("2436", "偉詮電"), ("2438", "翔耀"), ("2439", "美律"),
    ("2440", "太空梭"), ("2441", "超象"), ("2442", "新美齊"),
    # 其他 - 民生 / 觀光 / 百貨
    ("2901", "欣欣"), ("2903", "遠百"), ("2904", "匯僑"),
    ("2905", "三商"), ("2906", "高林"), ("2908", "特力"),
    ("2911", "麗嬰房"), ("2912", "統一超"), ("2913", "農林"),
    ("2915", "潤泰全"),
    # 生技低價
    ("4106", "雃博"), ("4108", "懷特"), ("4133", "亞諾法"),
    ("4137", "麗豐-KY"), ("4144", "康聯-KY"), ("4147", "中裕"),
    ("4155", "杏昌"), ("4164", "承業醫"),
    # 其他低價
    ("6005", "群益證"), ("6024", "群益期"),
    ("9902", "台火"), ("9904", "寶成"), ("9905", "大華"),
    ("9906", "欣巴"), ("9907", "統一實"), ("9908", "大台北"),
    ("9910", "豐泰"), ("9911", "櫻花"), ("9912", "偉聯"),
    ("9914", "美利達"), ("9917", "中保科"), ("9918", "欣天然"),
    ("9919", "康那香"), ("9921", "巨大"), ("9924", "福興"),
    ("9925", "新保"), ("9926", "新海"), ("9927", "泰銘"),
    ("9928", "中視"), ("9929", "秋雨"), ("9930", "中聯資源"),
    ("9931", "欣高"), ("9933", "愛地雅"), ("9934", "成霖"),
    ("9935", "慶豐富"), ("9937", "全國"), ("9938", "百和"),
    ("9939", "宏全"), ("9940", "信義"), ("9941", "裕融"),
    ("9942", "茂順"), ("9943", "好樂迪"), ("9944", "新麗"),
    ("9945", "潤泰新"), ("9946", "三發"), ("9955", "佳龍"),
]

print("=" * 60)
print(f"候選股數量: {len(CANDIDATES)}")

# Step 1: 先快速篩選 ≤ 50 元的
print("\nStep 1: 篩選股價 ≤ 50 元...")
RECENT_START = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")

low_price = []
for i, (sid, sname) in enumerate(CANDIDATES):
    try:
        df = fetch("TaiwanStockPrice", sid, RECENT_START)
        if df.empty:
            continue
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        last_close = df["close"].iloc[-1]
        if 1 < last_close <= 50:
            low_price.append((sid, sname, last_close))
    except:
        continue
    if (i+1) % 50 == 0:
        print(f"  已處理 {i+1}/{len(CANDIDATES)}...")
        time.sleep(1)

print(f"  股價 ≤ 50 的: {len(low_price)} 支")
for sid, sname, p in sorted(low_price, key=lambda x: x[2]):
    print(f"    {sid} {sname}: {p:.1f}")

# Step 2: 深度分析
print(f"\nStep 2: 深度分析 {len(low_price)} 支...")
results = []

for idx, (sid, sname, current_price) in enumerate(low_price):
    time.sleep(0.8)  # rate limit
    try:
        # 近一年股價
        price_df = fetch("TaiwanStockPrice", sid, ONE_YEAR_AGO)
        if price_df.empty or len(price_df) < 80:
            print(f"  {sid} {sname}: 股價資料不足，跳過")
            continue

        price_df["close"] = pd.to_numeric(price_df["close"], errors="coerce").dropna()
        closes = price_df["close"].values
        if len(closes) < 80:
            continue

        # 線性回歸
        x = np.arange(len(closes))
        slope, intercept = np.polyfit(x, closes, 1)
        predicted = slope * x + intercept
        ss_res = np.sum((closes - predicted) ** 2)
        ss_tot = np.sum((closes - np.mean(closes)) ** 2)
        r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0

        first_close = closes[0]
        year_return = (current_price - first_close) / first_close * 100

        high_1y = closes.max()
        low_1y = closes.min()
        pct_from_high = (high_1y - current_price) / high_1y * 100

        time.sleep(0.5)

        # 配息
        div_df = fetch("TaiwanStockDividend", sid, THREE_YEARS_AGO)
        latest_div = 0
        div_years_count = 0
        if not div_df.empty:
            for col in ["CashEarningsDistribution", "CashStaticDistribution"]:
                if col in div_df.columns:
                    div_df[col] = pd.to_numeric(div_df[col], errors="coerce").fillna(0)
            if "CashEarningsDistribution" in div_df.columns:
                div_df["total_cash"] = div_df.get("CashEarningsDistribution", 0)
                if "CashStaticDistribution" in div_df.columns:
                    div_df["total_cash"] = div_df["total_cash"] + div_df["CashStaticDistribution"]
            else:
                div_df["total_cash"] = 0

            if "date" in div_df.columns:
                div_df["year"] = pd.to_datetime(div_df["date"]).dt.year
                years_with_div = div_df[div_df["total_cash"] > 0]["year"].nunique()
                div_years_count = years_with_div

            if len(div_df) > 0:
                latest_div = div_df["total_cash"].iloc[-1]

        div_yield = (latest_div / current_price * 100) if current_price > 0 and latest_div > 0 else 0

        time.sleep(0.5)

        # EPS
        fin_df = fetch("TaiwanStockFinancialStatements", sid, THREE_YEARS_AGO)
        eps_trend = "N/A"
        avg_eps = 0
        if not fin_df.empty and "type" in fin_df.columns:
            eps_df = fin_df[fin_df["type"] == "EPS"].copy()
            if not eps_df.empty:
                eps_df["value"] = pd.to_numeric(eps_df["value"], errors="coerce")
                eps_df = eps_df.dropna(subset=["value"])
                if len(eps_df) >= 2:
                    avg_eps = eps_df["value"].mean()
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

        print(f"  {sid} {sname}: 價{current_price:.1f} 漲{year_return:.1f}% R²={r_squared:.2f} "
              f"殖{div_yield:.1f}% 配{div_years_count}年 EPS={eps_trend} avgEPS={avg_eps:.2f}")

        # 篩選條件
        if slope < 0 and year_return < -5:
            print(f"    -> 排除：下跌趨勢")
            continue
        if year_return > 80:
            print(f"    -> 排除：暴漲股")
            continue
        if eps_trend == "衰退":
            print(f"    -> 排除：EPS衰退")
            continue
        if avg_eps < 0.3:
            print(f"    -> 排除：EPS太低({avg_eps:.2f})")
            continue

        # 評分
        score = 0

        # 1. 趨勢分（R² * 正斜率）
        if slope > 0:
            score += min(r_squared * 25, 25)
        else:
            score += max(r_squared * 5, 0)  # 微跌但穩定也給一點分

        # 2. 年漲幅合理性
        if 5 <= year_return <= 30:
            score += 20
        elif 0 <= year_return < 5:
            score += 12
        elif 30 < year_return <= 50:
            score += 10
        elif -5 <= year_return < 0:
            score += 5

        # 3. 殖利率
        if div_yield >= 6:
            score += 25
        elif div_yield >= 5:
            score += 22
        elif div_yield >= 4:
            score += 18
        elif div_yield >= 3:
            score += 15
        elif div_yield >= 2:
            score += 10
        elif div_yield >= 1:
            score += 5

        # 4. 配息穩定度
        score += min(div_years_count * 5, 15)

        # 5. EPS
        if eps_trend == "成長":
            score += 15
        elif eps_trend == "穩定":
            score += 10

        # 6. 低基期
        if 5 <= pct_from_high <= 25:
            score += 10
        elif pct_from_high < 5:
            score += 5

        results.append({
            "stock_id": sid,
            "stock_name": sname,
            "price": current_price,
            "year_return_pct": round(year_return, 1),
            "slope": round(slope, 4),
            "r_squared": round(r_squared, 3),
            "div_yield_pct": round(div_yield, 2),
            "div_years": div_years_count,
            "eps_trend": eps_trend,
            "avg_eps": round(avg_eps, 2),
            "pct_from_high": round(pct_from_high, 1),
            "score": round(score, 1),
        })

    except Exception as e:
        print(f"  {sid} {sname}: 錯誤 {e}")
        continue

# 輸出結果
print("\n" + "=" * 110)
print("                        台股存股篩選結果 — TOP 10")
print("=" * 110)

if not results:
    print("沒有符合所有條件的股票")
    exit(0)

results_df = pd.DataFrame(results).sort_values("score", ascending=False)

print(f"\n{'排名':>4} | {'代號':>6} | {'股名':<8} | {'現價':>6} | {'年漲%':>6} | {'R²':>5} | {'殖利率%':>7} | {'配息年':>4} | {'EPS':>4} | {'平均EPS':>7} | {'距高%':>5} | {'分數':>5}")
print("-" * 110)

top10 = results_df.head(10)
for rank, (_, row) in enumerate(top10.iterrows(), 1):
    print(f"{rank:>4} | {row['stock_id']:>6} | {row['stock_name']:<8} | "
          f"{row['price']:>6.1f} | {row['year_return_pct']:>6.1f} | "
          f"{row['r_squared']:>5.3f} | {row['div_yield_pct']:>7.2f} | "
          f"{row['div_years']:>4} | {row['eps_trend']:>4} | "
          f"{row['avg_eps']:>7.2f} | {row['pct_from_high']:>5.1f} | "
          f"{row['score']:>5.1f}")

print("\n" + "-" * 110)
print("\n各股推薦理由：")

for rank, (_, row) in enumerate(top10.iterrows(), 1):
    reasons = []
    if row["slope"] > 0 and row["r_squared"] >= 0.5:
        reasons.append("股價穩定向上")
    elif row["slope"] > 0:
        reasons.append("股價趨勢向上")
    if row["year_return_pct"] >= 5:
        reasons.append(f"年漲{row['year_return_pct']:.0f}%合理")
    if row["div_yield_pct"] >= 5:
        reasons.append(f"殖利率{row['div_yield_pct']:.1f}%（高）")
    elif row["div_yield_pct"] >= 3:
        reasons.append(f"殖利率{row['div_yield_pct']:.1f}%")
    if row["div_years"] >= 3:
        reasons.append(f"近{row['div_years']}年穩定配息")
    if row["eps_trend"] == "成長":
        reasons.append("EPS成長中")
    elif row["eps_trend"] == "穩定":
        reasons.append("EPS穩定")
    if row["pct_from_high"] >= 10:
        reasons.append(f"距高點{row['pct_from_high']:.0f}%有空間")
    if not reasons:
        reasons.append("綜合評分較高")
    print(f"\n  {rank}. [{row['stock_id']}] {row['stock_name']}（現價 {row['price']:.1f} 元）")
    print(f"     {' / '.join(reasons)}")

# 也列出所有通過的（如果超過10支）
if len(results_df) > 10:
    print(f"\n\n--- 其他通過篩選的候選股（第 11~{len(results_df)} 名）---")
    for rank, (_, row) in enumerate(results_df.iloc[10:].iterrows(), 11):
        print(f"  {rank}. {row['stock_id']} {row['stock_name']} "
              f"（{row['price']:.1f}元, 殖利率{row['div_yield_pct']:.1f}%, "
              f"EPS {row['eps_trend']}, 分數{row['score']}）")

print("\n" + "=" * 110)
print("注意：以上為量化模型篩選結果，僅供參考。投資前請自行研究基本面與產業前景。")
print("=" * 110)
