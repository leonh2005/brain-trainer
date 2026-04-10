#!/usr/bin/env python3
"""
當沖候選推播 — 週間 09:30 執行
資料來源：永豐金 Shioaji snapshots（主，即時）+ TWSE 公開 API（輔）+ FinMind（期貨法人）
"""

import requests
import pandas as pd
import warnings
import shioaji as sj
import os
from datetime import datetime, date, timedelta
from dotenv import load_dotenv

def trading_days_ago(n):
    from datetime import date, timedelta
    d = date.today()
    count = 0
    while count < n:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            count += 1
    return d.strftime('%Y-%m-%d')

from FinMind.data import DataLoader

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
warnings.filterwarnings('ignore')

# ── 設定 ──────────────────────────────────────────
BOT_TOKEN = "8666778924:AAFMAFKfsfx3opS2CfCBrDYMIx6vcJKACTk"
CHAT_ID   = "7556217543"
TOKEN     = open('/Users/steven/CCProject/.secrets/finmind_token.txt').read().strip()
TODAY     = datetime.today().strftime('%Y-%m-%d')
D5        = trading_days_ago(7)

finmind = DataLoader()
finmind.login_by_token(api_token=TOKEN)

# ── Shioaji 連線（singleton）─────────────────────
_sj_api = None

def _get_sj():
    global _sj_api
    if _sj_api is None:
        _sj_api = sj.Shioaji(simulation=False)
        _sj_api.login(
            api_key=os.environ['SHIOAJI_API_KEY'],
            secret_key=os.environ['SHIOAJI_SECRET_KEY'],
        )
        print('[sj] 永豐 API 登入成功')
    return _sj_api


def get_top_volume_sj(n: int = 20) -> list:
    """Shioaji 全市場掃描，回傳今日成交量前 n 名（含股名、即時行情）"""
    api = _get_sj()
    all_contracts = [
        c for c in api.Contracts.Stocks.TSE
        if hasattr(c, 'code') and c.code.isdigit() and len(c.code) == 4
    ]
    cmap = {c.code: c for c in all_contracts}
    rows = {}
    for i in range(0, len(all_contracts), 200):
        chunk = all_contracts[i:i+200]
        try:
            snaps = api.snapshots(chunk)
            for s in snaps:
                if s.close <= 0 or s.total_volume <= 0:
                    continue
                rng = s.high - s.low
                rows[s.code] = {
                    'code':      s.code,
                    'name':      getattr(cmap.get(s.code), 'name', s.code),
                    'close':     s.close,
                    'chg_pct':   round(float(s.change_rate), 2),
                    'amp_pct':   round(rng / s.low * 100, 2) if s.low > 0 else 0.0,
                    'close_pos': round((s.close - s.low) / rng * 100, 1) if rng > 0 else 50.0,
                    'vol_k':     int(s.total_volume / 1000),
                }
        except Exception as e:
            print(f'[sj] snapshot batch {i} 失敗: {e}')
    if not rows:
        return []
    top = sorted(rows.values(), key=lambda x: x['vol_k'], reverse=True)[:n]
    print(f'[top{n}] ' + ' '.join(f"{r['code']}({r['vol_k']}K)" for r in top[:5]) + ' ...')
    return top


def get_sj_avg5(symbol: str) -> int:
    """Shioaji 歷史日K 算近5日均量（不含今日）"""
    try:
        api = _get_sj()
        contract = api.Contracts.Stocks.get(symbol)
        if contract is None:
            return 0
        start = str(date.today() - timedelta(days=20))
        end   = str(date.today() - timedelta(days=1))
        kb = api.kbars(contract, start=start, end=end)
        df = pd.DataFrame({**kb})
        if df.empty:
            return 0
        df['ts'] = pd.to_datetime(df['ts'])
        df['_d'] = df['ts'].dt.date
        daily = df.groupby('_d').agg(volume=('Volume', 'sum')).reset_index()
        if len(daily) >= 5:
            return int(round(daily['volume'].tail(5).mean(), 0))
    except Exception as e:
        print(f'[sj] avg5 {symbol} 失敗: {e}')
    return 0


def get_avg5_finmind(stock_id: str) -> int:
    """FinMind 近5日均量（Shioaji 失敗時備用）"""
    try:
        D10 = trading_days_ago(15)
        h = finmind.taiwan_stock_daily(stock_id=stock_id, start_date=D10)
        if len(h) >= 5:
            return int(round(h['Trading_Volume'].tail(5).mean() / 1000, 0))
    except Exception:
        pass
    return 0


def send_telegram(text: str):
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
        timeout=10
    )


def get_futures_direction():
    try:
        fut = finmind.taiwan_futures_institutional_investors(futures_id="TX", start_date=D5)
        fi = fut[fut['institutional_investors']=='外資']
        last = fi.iloc[-1]
        diff = int(last['long_open_interest_balance_volume']) - int(last['short_open_interest_balance_volume'])
        return diff, str(last['date'])[:10]
    except:
        return 0, ''


# ── 主程式 ────────────────────────────────────────

top20 = get_top_volume_sj(n=20)
if not top20:
    print('[error] Shioaji 全市場掃描失敗，結束執行')
    send_telegram(f"⚠️ {TODAY} 當沖掃描失敗：Shioaji 無法取得全市場資料，請確認 API 連線")
    exit(1)

candidates = []
for row in top20:
    code      = row['code']
    # 近5日均量：Shioaji kbars，失敗用 FinMind
    avg5 = get_sj_avg5(code)
    if avg5 == 0:
        avg5 = get_avg5_finmind(code)

    if row['amp_pct'] >= 3 and avg5 >= 3000 and row['chg_pct'] >= 1.5:
        candidates.append({
            'code':      code,
            'name':      row['name'],
            'close':     row['close'],
            'chg_pct':   row['chg_pct'],
            'amp_pct':   row['amp_pct'],
            'vol_k':     row['vol_k'],
            'avg5':      avg5,
            'close_pos': row['close_pos'],
        })

fut_diff, fut_date = get_futures_direction()
mkt_dir = "偏多 ↑" if fut_diff > 0 else "偏空 ↓"

# ── 組訊息 ────────────────────────────────────────

lines = [f"📊 <b>當沖候選</b>｜{TODAY} 09:30\n"]
lines.append(f"🌐 大盤外資期貨（{fut_date}）：{mkt_dir}（多空差 {fut_diff:+,} 口）\n")
lines.append("📋 <b>篩選條件</b>")
lines.append("今日量前20（Shioaji即時）＋ 振幅&gt;3% ＋ 近5均量&gt;3000張 ＋ 漲幅&gt;1.5%\n")

if candidates:
    lines.append(f"✅ 符合 {len(candidates)} 檔：\n")
    for c in candidates:
        pos_emoji = "🔴" if c['close_pos'] >= 80 else "🟡" if c['close_pos'] >= 50 else "🟢"
        lines.append(
            f"{pos_emoji} <b>{c['code']} {c['name']}</b>\n"
            f"   收:{c['close']:.1f}  漲:{c['chg_pct']:+.1f}%  振:{c['amp_pct']:.1f}%\n"
            f"   量:{c['vol_k']:,}張  近5均:{c['avg5']:,}張  收盤位:{c['close_pos']:.0f}%\n"
        )
    lines.append("⚡ 進場參考：開盤後5~15分鐘確認方向再進")
    lines.append("🛑 停損：跌破進場價 -1.5% 出清")
else:
    lines.append("❌ 今日無符合當沖條件標的\n建議觀望或等待盤中突破訊號")

lines.append("\n⚠️ 數據參考，非投資建議，買賣自負")

msg = "\n".join(lines)
send_telegram(msg)
print(msg)

# 儲存候選清單供盤中監控腳本使用
import json
candidate_codes = [c['code'] for c in candidates]
with open('/tmp/daytrade_candidates.json', 'w') as f:
    json.dump(candidate_codes, f)
print(f'[daytrade] 候選清單已寫入 /tmp/daytrade_candidates.json: {candidate_codes}')

if _sj_api is not None:
    _sj_api.logout()
    print('[sj] 永豐 API 已登出')
