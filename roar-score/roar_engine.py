"""
ROAR Score Engine
核心問題：從今天起，先漲 10% 的機率有多少（先跌 10% 之前）？
方法：蒙地卡羅模擬，抽樣歷史日報酬 10,000 條路徑
"""
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import date, timedelta

FINMIND_TOKEN_PATH = '/Users/steven/CCProject/.secrets/finmind_token.txt'
N_SIMULATIONS = 10_000
MAX_DAYS = 252        # 最長模擬一年
TARGET_UP = 0.10
TARGET_DOWN = -0.10


def _is_tw_stock(ticker: str) -> bool:
    return ticker.isdigit() and len(ticker) in (4, 5)


def fetch_history_yfinance(ticker: str, lookback_days: int = 504) -> pd.DataFrame:
    end = date.today()
    start = end - timedelta(days=lookback_days)
    df = yf.download(ticker, start=str(start), end=str(end), progress=False, auto_adjust=True)
    if df.empty:
        raise ValueError(f"找不到 {ticker} 的資料，請確認代號正確")
    df = df[['Close']].rename(columns={'Close': 'close'})
    df.index = pd.to_datetime(df.index)
    # yfinance 可能回傳 MultiIndex
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = ['close']
    return df.sort_index()


def fetch_history_finmind(ticker: str, lookback_days: int = 504) -> pd.DataFrame:
    from FinMind.data import DataLoader
    token = open(FINMIND_TOKEN_PATH).read().strip()
    fm = DataLoader()
    fm.login_by_token(api_token=token)
    end = str(date.today())
    start = str(date.today() - timedelta(days=lookback_days))
    df = fm.taiwan_stock_daily(stock_id=ticker, start_date=start, end_date=end)
    if df.empty:
        raise ValueError(f"找不到 {ticker} 的資料，請確認代號正確")
    df = df[['date', 'close']].set_index('date')
    df.index = pd.to_datetime(df.index)
    return df.sort_index()


def fetch_history(ticker: str) -> tuple[pd.DataFrame, str]:
    """回傳 (df, market_label)"""
    ticker = ticker.strip().upper()
    if _is_tw_stock(ticker):
        df = fetch_history_finmind(ticker)
        label = "TW"
    else:
        df = fetch_history_yfinance(ticker)
        label = "US"
    return df, label


def compute_roar(df: pd.DataFrame) -> dict:
    closes = df['close'].dropna().values.astype(float)
    if len(closes) < 30:
        raise ValueError("歷史資料不足（少於 30 天），無法計算")

    # 日報酬（log return）
    log_returns = np.diff(np.log(closes))
    current_price = closes[-1]

    # 蒙地卡羅：每條路徑隨機抽 MAX_DAYS 個歷史日報酬
    rng = np.random.default_rng(42)
    sampled = rng.choice(log_returns, size=(N_SIMULATIONS, MAX_DAYS), replace=True)
    # 累積路徑（從 1.0 起）
    cum = np.exp(np.cumsum(sampled, axis=1))  # shape (N, MAX_DAYS)

    # 找每條路徑第一次觸碰 +10% 或 -10% 的日期
    hit_up = np.argmax(cum >= (1 + TARGET_UP), axis=1)    # 0 表示從未觸碰
    hit_dn = np.argmax(cum <= (1 + TARGET_DOWN), axis=1)

    never_up = (cum[:, -1] < (1 + TARGET_UP))   # 整條路徑都沒漲到 10%
    never_dn = (cum[:, -1] > (1 + TARGET_DOWN))  # 整條路徑都沒跌到 -10%

    hit_up[never_up] = MAX_DAYS + 1
    hit_dn[never_dn] = MAX_DAYS + 1

    # 排除兩者都沒觸碰的路徑
    decided = ~(never_up & never_dn)
    n_decided = decided.sum()

    if n_decided == 0:
        roar = 50.0
    else:
        up_first = (hit_up[decided] < hit_dn[decided]).sum()
        roar = round(up_first / n_decided * 100, 1)

    # 附帶技術資訊
    closes_s = pd.Series(closes)
    ma20 = closes_s.rolling(20).mean().iloc[-1]
    ma50 = closes_s.rolling(50).mean().iloc[-1]
    ma200 = closes_s.rolling(200).mean().iloc[-1]
    daily_vol = float(np.std(log_returns) * 100)

    prev_close = closes[-2] if len(closes) >= 2 else closes[-1]
    chg = current_price - prev_close
    chg_pct = chg / prev_close * 100

    price_up_target = round(current_price * (1 + TARGET_UP), 2)
    price_dn_target = round(current_price * (1 + TARGET_DOWN), 2)

    return {
        'roar': roar,
        'current_price': round(float(current_price), 2),
        'change': round(float(chg), 2),
        'change_pct': round(float(chg_pct), 2),
        'ma20': round(float(ma20), 2),
        'ma50': round(float(ma50), 2),
        'ma200': round(float(ma200), 2),
        'daily_vol_pct': round(daily_vol, 2),
        'target_up': price_up_target,
        'target_down': price_dn_target,
        'n_simulations': N_SIMULATIONS,
        'n_decided': int(n_decided),
        'history_days': len(closes),
    }


def get_roar_interpretation(score: float) -> dict:
    if score >= 70:
        return {'level': 'high', 'label': '高勝算', 'color': '#22c55e',
                'desc': '上漲 10% 的機率遠大於下跌 10%，適合建立部位或加碼。'}
    elif score >= 50:
        return {'level': 'moderate', 'label': '略偏多', 'color': '#eab308',
                'desc': '多空力道相近，略偏多，建議小部位試探或等待更強訊號。'}
    elif score >= 30:
        return {'level': 'caution', 'label': '謹慎', 'color': '#f97316',
                'desc': '下行風險較大，持有部位可考慮停損或輕倉，不宜追高。'}
    else:
        return {'level': 'danger', 'label': '高風險', 'color': '#ef4444',
                'desc': '追高風險極高，可考慮減碼或買 Put 避險。'}


def analyze(ticker: str) -> dict:
    df, market = fetch_history(ticker)
    result = compute_roar(df)
    result['ticker'] = ticker.upper()
    result['market'] = market
    result['interpretation'] = get_roar_interpretation(result['roar'])

    # 近 60 天收盤（給前端畫圖）
    recent = df.tail(60).reset_index()
    recent.columns = ['date', 'close']
    result['chart_dates'] = recent['date'].dt.strftime('%m/%d').tolist()
    result['chart_prices'] = [round(float(p), 2) for p in recent['close'].tolist()]

    return result
