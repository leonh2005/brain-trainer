"""
台股專用工具：FinMind 法人買賣超
"""

from langchain_core.tools import tool
from typing import Annotated
from datetime import datetime, timedelta
import os


def _is_tw_ticker(ticker: str) -> bool:
    base = ticker.replace(".TW", "").replace(".TWO", "")
    return base.isdigit() and len(base) == 4


def _trading_days_ago(n: int) -> str:
    d = datetime.today().date()
    count = 0
    while count < n:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            count += 1
    return str(d)


@tool
def get_tw_institutional_investors(
    ticker: Annotated[str, "台股代碼，格式如 2330.TW 或 3006.TW"],
    curr_date: Annotated[str, "分析日期，yyyy-mm-dd"],
) -> str:
    """
    取得台股三大法人（外資、投信、自營商）近 10 個交易日買賣超資料。
    僅適用於台灣上市/上櫃股票（.TW 結尾）。
    回傳每日買賣超張數及累計，協助判斷法人動向。
    """
    if not _is_tw_ticker(ticker):
        return f"{ticker} 非台股代碼，此工具僅適用台灣上市/上櫃股票。"

    stock_id = ticker.replace(".TW", "").replace(".TWO", "")
    start_date = _trading_days_ago(15)

    try:
        token_path = os.path.expanduser("~/CCProject/.secrets/finmind_token.txt")
        with open(token_path) as f:
            token = f.read().strip()

        from FinMind.data import DataLoader
        dl = DataLoader()
        dl.login_by_token(api_token=token)

        df = dl.taiwan_stock_institutional_investors(
            stock_id=stock_id, start_date=start_date
        )
        if df is None or df.empty:
            return f"{ticker} 近期無法人資料（可能為 ETF 或資料尚未更新）。"

        df = df[df['date'] <= curr_date].tail(30)  # 最近 10 個交易日 × 3 法人

        lines = [f"## {ticker} 三大法人買賣超（近10交易日）\n"]
        lines.append(f"{'日期':<12} {'法人':<10} {'買超(張)':>10} {'賣超(張)':>10} {'淨買(張)':>10}")
        lines.append("-" * 55)

        cumulative = {}
        for _, row in df.iterrows():
            name = row['name']
            net = int((row['buy'] - row['sell']) / 1000)
            cumulative[name] = cumulative.get(name, 0) + net
            lines.append(
                f"{str(row['date']):<12} {name:<10} "
                f"{int(row['buy']/1000):>10,} {int(row['sell']/1000):>10,} {net:>+10,}"
            )

        lines.append("\n### 累計淨買超")
        for name, total in cumulative.items():
            lines.append(f"  {name}: {total:+,} 張")

        return "\n".join(lines)

    except FileNotFoundError:
        return "FinMind token 檔案不存在，請確認 ~/CCProject/.secrets/finmind_token.txt"
    except Exception as e:
        return f"法人資料取得失敗：{e}"
