"""
FinMind 台股資料來源
提供比 yfinance 更準確的台灣股市資料：價格、財報、資產負債表、損益表、現金流量表
"""

from typing import Annotated
from datetime import datetime, timedelta
import os
import pandas as pd


def _load_token() -> str:
    token_path = os.path.expanduser("~/CCProject/.secrets/finmind_token.txt")
    try:
        with open(token_path) as f:
            return f.read().strip()
    except FileNotFoundError:
        raise RuntimeError(f"FinMind token 不存在：{token_path}")


def _get_loader():
    from FinMind.data import DataLoader
    dl = DataLoader()
    dl.login_by_token(api_token=_load_token())
    return dl


def _stock_id(ticker: str) -> str:
    """2330.TW → 2330"""
    return ticker.upper().replace(".TW", "").replace(".TWO", "")


def _is_tw(ticker: str) -> bool:
    base = _stock_id(ticker)
    return base.isdigit() and len(base) == 4


# ── OHLCV 價格 ──────────────────────────────────────────────

def get_stock_data(
    symbol: Annotated[str, "台股代碼，如 2330.TW"],
    start_date: Annotated[str, "開始日期 yyyy-mm-dd"],
    end_date: Annotated[str, "結束日期 yyyy-mm-dd"],
) -> str:
    if not _is_tw(symbol):
        return f"{symbol} 非台股代碼，FinMind 僅支援台灣上市/上櫃股票"

    stock_id = _stock_id(symbol)
    try:
        dl = _get_loader()
        df = dl.taiwan_stock_daily(stock_id=stock_id, start_date=start_date, end_date=end_date)

        if df is None or df.empty:
            return f"FinMind 無 {symbol} 在 {start_date}~{end_date} 的價格資料"

        df = df.rename(columns={
            "date": "Date", "open": "Open", "max": "High",
            "min": "Low", "close": "Close", "Trading_Volume": "Volume",
            "trading_volume": "Volume",
        })
        df["Date"] = pd.to_datetime(df["Date"])
        df = df.set_index("Date").sort_index()

        cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
        df = df[cols].round(2)

        header = f"# Stock data for {symbol.upper()} from {start_date} to {end_date} [FinMind]\n"
        header += f"# Total records: {len(df)}\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        return header + df.to_csv()

    except Exception as e:
        return f"FinMind 價格資料取得失敗 ({symbol})：{e}"


# ── 技術指標（以 FinMind 價格為基礎，沿用 stockstats）──────

def get_indicators(
    symbol: Annotated[str, "台股代碼"],
    indicator: Annotated[str, "技術指標名稱"],
    curr_date: Annotated[str, "分析日期 yyyy-mm-dd"],
    look_back_days: Annotated[int, "回溯天數"],
) -> str:
    if not _is_tw(symbol):
        return f"{symbol} 非台股代碼"

    end_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    start_dt = end_dt - timedelta(days=look_back_days + 60)  # 多抓一些供指標計算

    stock_id = _stock_id(symbol)
    try:
        dl = _get_loader()
        df = dl.taiwan_stock_daily(
            stock_id=stock_id,
            start_date=start_dt.strftime("%Y-%m-%d"),
            end_date=curr_date,
        )
        if df is None or df.empty:
            raise ValueError("empty")

        df = df.rename(columns={
            "date": "Date", "open": "open", "max": "high",
            "min": "low", "close": "close", "trading_volume": "volume",
        })
        df["Date"] = pd.to_datetime(df["Date"])
        df = df.set_index("Date").sort_index()

        from stockstats import StockDataFrame
        stock = StockDataFrame.retype(df.copy())
        values = stock[indicator]

        # 只取 look_back_days 範圍
        cutoff = end_dt - timedelta(days=look_back_days)
        values = values[values.index >= cutoff]

        lines = [f"# {indicator.upper()} for {symbol} (FinMind 價格來源)\n"]
        for dt, v in values.items():
            lines.append(f"{dt.strftime('%Y-%m-%d')}: {round(float(v), 4)}")
        return "\n".join(lines)

    except Exception as e:
        # fallback：告知 caller 改用 yfinance
        return f"FinMind 技術指標計算失敗 ({symbol})，請改用 yfinance：{e}"


# ── 基本面 ──────────────────────────────────────────────────

def get_fundamentals(
    ticker: Annotated[str, "台股代碼"],
    curr_date: Annotated[str, "分析日期"] = None,
) -> str:
    if not _is_tw(ticker):
        return f"{ticker} 非台股代碼"

    stock_id = _stock_id(ticker)
    date_limit = curr_date or datetime.today().strftime("%Y-%m-%d")
    start = (datetime.strptime(date_limit, "%Y-%m-%d") - timedelta(days=730)).strftime("%Y-%m-%d")

    try:
        dl = _get_loader()

        # 財務比率（EPS, ROE, ROA…）
        df = dl.taiwan_stock_financial_statement(stock_id=stock_id, start_date=start, end_date=date_limit)

        lines = [f"# Company Fundamentals for {ticker.upper()} [FinMind]\n"]
        lines.append(f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

        if df is not None and not df.empty:
            df = df[df["date"] <= date_limit].sort_values("date", ascending=False)
            pivot = df.pivot_table(index="date", columns="type", values="value", aggfunc="first")
            lines.append(pivot.to_csv())
        else:
            lines.append("無財務報表資料")

        return "\n".join(lines)

    except Exception as e:
        return f"FinMind 基本面資料取得失敗 ({ticker})：{e}"


# ── 資產負債表 ───────────────────────────────────────────────

def get_balance_sheet(
    ticker: Annotated[str, "台股代碼"],
    freq: Annotated[str, "annual 或 quarterly"] = "quarterly",
    curr_date: Annotated[str, "分析日期"] = None,
) -> str:
    if not _is_tw(ticker):
        return f"{ticker} 非台股代碼"

    stock_id = _stock_id(ticker)
    date_limit = curr_date or datetime.today().strftime("%Y-%m-%d")
    start = (datetime.strptime(date_limit, "%Y-%m-%d") - timedelta(days=730)).strftime("%Y-%m-%d")

    try:
        dl = _get_loader()
        df = dl.taiwan_stock_balance_sheet(stock_id=stock_id, start_date=start, end_date=date_limit)

        if df is None or df.empty:
            return f"FinMind 無 {ticker} 資產負債表資料"

        df = df[df["date"] <= date_limit].sort_values("date", ascending=False)
        pivot = df.pivot_table(index="date", columns="type", values="value", aggfunc="first")

        header = f"# Balance Sheet for {ticker.upper()} ({freq}) [FinMind]\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        return header + pivot.to_csv()

    except Exception as e:
        return f"FinMind 資產負債表取得失敗 ({ticker})：{e}"


# ── 現金流量表 ───────────────────────────────────────────────

def get_cashflow(
    ticker: Annotated[str, "台股代碼"],
    freq: Annotated[str, "annual 或 quarterly"] = "quarterly",
    curr_date: Annotated[str, "分析日期"] = None,
) -> str:
    if not _is_tw(ticker):
        return f"{ticker} 非台股代碼"

    stock_id = _stock_id(ticker)
    date_limit = curr_date or datetime.today().strftime("%Y-%m-%d")
    start = (datetime.strptime(date_limit, "%Y-%m-%d") - timedelta(days=730)).strftime("%Y-%m-%d")

    try:
        dl = _get_loader()
        df = dl.taiwan_stock_cash_flows_statement(stock_id=stock_id, start_date=start, end_date=date_limit)

        if df is None or df.empty:
            return f"FinMind 無 {ticker} 現金流量資料"

        df = df[df["date"] <= date_limit].sort_values("date", ascending=False)
        pivot = df.pivot_table(index="date", columns="type", values="value", aggfunc="first")

        header = f"# Cash Flow for {ticker.upper()} ({freq}) [FinMind]\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        return header + pivot.to_csv()

    except Exception as e:
        return f"FinMind 現金流量資料取得失敗 ({ticker})：{e}"


# ── 損益表 ──────────────────────────────────────────────────

def get_income_statement(
    ticker: Annotated[str, "台股代碼"],
    freq: Annotated[str, "annual 或 quarterly"] = "quarterly",
    curr_date: Annotated[str, "分析日期"] = None,
) -> str:
    if not _is_tw(ticker):
        return f"{ticker} 非台股代碼"

    stock_id = _stock_id(ticker)
    date_limit = curr_date or datetime.today().strftime("%Y-%m-%d")
    start = (datetime.strptime(date_limit, "%Y-%m-%d") - timedelta(days=730)).strftime("%Y-%m-%d")

    try:
        dl = _get_loader()
        df = dl.taiwan_stock_profit_loss_statement(stock_id=stock_id, start_date=start, end_date=date_limit)

        if df is None or df.empty:
            return f"FinMind 無 {ticker} 損益表資料"

        df = df[df["date"] <= date_limit].sort_values("date", ascending=False)
        pivot = df.pivot_table(index="date", columns="type", values="value", aggfunc="first")

        header = f"# Income Statement for {ticker.upper()} ({freq}) [FinMind]\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        return header + pivot.to_csv()

    except Exception as e:
        return f"FinMind 損益表取得失敗 ({ticker})：{e}"


# ── 三大法人（替代 insider_transactions）────────────────────

def get_insider_transactions(
    ticker: Annotated[str, "台股代碼"],
) -> str:
    """台股用三大法人買賣超取代 insider transactions"""
    if not _is_tw(ticker):
        return f"{ticker} 非台股代碼"

    stock_id = _stock_id(ticker)
    end_date = datetime.today().strftime("%Y-%m-%d")
    start_date = (datetime.today() - timedelta(days=30)).strftime("%Y-%m-%d")

    try:
        dl = _get_loader()
        df = dl.taiwan_stock_institutional_investors(stock_id=stock_id, start_date=start_date)

        if df is None or df.empty:
            return f"{ticker} 近期無三大法人資料"

        df = df[df["date"] <= end_date].tail(30)

        lines = [f"## {ticker} 三大法人買賣超（近10交易日）[FinMind]\n"]
        lines.append(f"{'日期':<12} {'法人':<10} {'買超(張)':>10} {'賣超(張)':>10} {'淨買(張)':>10}")
        lines.append("-" * 55)

        cumulative = {}
        for _, row in df.iterrows():
            name = row["name"]
            net = int((row["buy"] - row["sell"]) / 1000)
            cumulative[name] = cumulative.get(name, 0) + net
            lines.append(
                f"{str(row['date']):<12} {name:<10} "
                f"{int(row['buy']/1000):>10,} {int(row['sell']/1000):>10,} {net:>+10,}"
            )

        lines.append("\n### 累計淨買超")
        for name, total in cumulative.items():
            lines.append(f"  {name}: {total:+,} 張")

        return "\n".join(lines)

    except Exception as e:
        return f"FinMind 三大法人資料取得失敗 ({ticker})：{e}"
