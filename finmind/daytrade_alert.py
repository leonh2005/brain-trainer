#!/usr/bin/env python3
"""
當沖候選推播 — 週間 09:19 執行
資料來源：永豐金 Shioaji snapshots（個股即時 + 加權指數）+ TWSE 公開 API（輔）+ FinMind（三大法人現貨參考）
"""

import html
import os
import sys
import warnings
from datetime import date, datetime, timedelta

import requests
from ai_analysis import analyze_stock, format_ai_block
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'lib'))
from log_redact import redact  # noqa: E402


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
BOT_TOKEN = open(os.path.expanduser("~/CCProject/.secrets/telegram_token.txt")).read().strip()
CHAT_ID   = "7556217543"
TOKEN     = open('/Users/steven/CCProject/.secrets/finmind_token.txt').read().strip()
TODAY     = datetime.today().strftime('%Y-%m-%d')
D5        = trading_days_ago(7)

def send_telegram(text: str):
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
        timeout=10
    )


finmind = None


def _init_finmind():
    """FinMind 登入（延後到 main 才做，讓測試 import 時不連網）"""
    global finmind
    try:
        finmind = DataLoader()
        finmind.login_by_token(api_token=TOKEN)
    except Exception as e:
        print(f'[finmind] 登入失敗，改用降級模式: {redact(e)}')
        send_telegram(f"⚠️ {TODAY} 當沖掃描：FinMind 連線失敗（{type(e).__name__}），改用降級模式繼續（無前一日法人參考/FinMind均量備援）")
        finmind = None


# ── gateway（HTTP；不自行登入 Shioaji，避免與 gateway 互踢 session）─────────
GATEWAY = 'http://localhost:5455'


def _get_json(path: str, params: dict, timeout: int) -> dict:
    r = requests.get(f'{GATEWAY}{path}', params=params, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    if not data.get('ok'):
        raise RuntimeError(data.get('error') or f'{path} 失敗')
    return data


def get_top_volume_sj(n: int = 20) -> list:
    """全市場快照（gateway），回傳今日成交量前 n 名（維持原本只掃上市的範圍）"""
    try:
        data = _get_json('/market_snapshot', {}, 40)
    except Exception as e:
        print(f'[gateway] 全市場快照失敗: {redact(e)}')
        return []
    rows = {}
    for s in data.get('items', []):
        code = s.get('code') or ''
        if s.get('suffix') != '.TW' or not (code.isdigit() and len(code) == 4):
            continue
        close, vol = s.get('close'), s.get('total_volume') or 0
        if not close or close <= 0 or vol <= 0:
            continue
        high, low = s.get('high'), s.get('low')
        rng = (high - low) if high is not None and low is not None else 0
        rows[code] = {
            'code':      code,
            'name':      s.get('name') or code,
            'close':     close,
            'chg_pct':   round(float(s.get('change_rate') or 0), 2),
            'amp_pct':   round(rng / low * 100, 2) if low and low > 0 else 0.0,
            'close_pos': round((close - low) / rng * 100, 1) if rng > 0 else 50.0,
            'vol_k':     int(vol),  # 單位：張（gateway 的 total_volume 已是張）
        }
    if not rows:
        return []
    top = sorted(rows.values(), key=lambda x: x['vol_k'], reverse=True)[:n]
    print(f'[top{n}] ' + ' '.join(f"{r['code']}({r['vol_k']//1000}K)" for r in top[:5]) + ' ...')
    return top


_bars_cache = {}


def get_daily_bars(symbol: str) -> list:
    """gateway 日K（不含今日）；近5均量與連漲跌天數共用一次查詢"""
    if symbol in _bars_cache:
        return _bars_cache[symbol]
    try:
        data = _get_json('/daily_ohlcv', {'code': symbol, 'days': 20}, 15)
        bars = [b for b in (data.get('bars') or []) if b.get('date') < str(date.today())]
    except Exception as e:
        print(f'[gateway] 日K {symbol} 失敗: {redact(e)}')
        bars = []
    _bars_cache[symbol] = bars
    return bars


def get_sj_avg5(symbol: str) -> int:
    """近5日均量（不含今日）"""
    bars = get_daily_bars(symbol)
    if len(bars) < 5:
        return 0
    return int(round(sum(b['volume'] for b in bars[-5:]) / 5, 0))


def get_price_streak(symbol: str) -> int:
    """連漲跌天數（不含今日，正=連漲、負=連跌、0=無資料或平盤，
    同 chip-tracker/db.py price_streak() 語意）"""
    closes = [b['close'] for b in get_daily_bars(symbol)]
    if len(closes) < 2:
        return 0
    chg_pcts = []  # 新到舊
    for i in range(len(closes) - 1, 0, -1):
        prev, cur = closes[i - 1], closes[i]
        if prev:
            chg_pcts.append((cur - prev) / prev * 100)
    if not chg_pcts or chg_pcts[0] == 0:
        return 0
    up = chg_pcts[0] > 0
    count = 0
    for v in chg_pcts:
        if v == 0 or (v > 0) != up:
            break
        count += 1
    return count if up else -count


def get_avg5_finmind(stock_id: str) -> int:
    """FinMind 近5日均量（gateway 失敗時備用）"""
    if finmind is None:
        return 0
    try:
        D10 = trading_days_ago(15)
        h = finmind.taiwan_stock_daily(stock_id=stock_id, start_date=D10)
        if len(h) >= 5:
            return int(round(h['Trading_Volume'].tail(5).mean() / 1000, 0))
    except Exception:
        pass
    return 0


def get_institutional_net():
    """三大法人現貨買賣超合計（億元），負值=賣超。資料源 FinMind TaiwanStockTotalInstitutionalInvestors 的 total 欄位。"""
    try:
        res = requests.get(
            'https://api.finmindtrade.com/api/v4/data',
            params={'dataset': 'TaiwanStockTotalInstitutionalInvestors', 'start_date': D5, 'token': TOKEN},
            timeout=10,
        )
        rows = res.json().get('data', [])
        totals = [r for r in rows if r.get('name') == 'total']
        if not totals:
            return 0, ''
        last = totals[-1]
        net = (int(last['buy']) - int(last['sell'])) / 1e8  # 元 → 億元
        return round(net, 1), str(last['date'])[:10]
    except Exception:
        return 0, ''


def get_short_candidates():
    """全市場掃空方觀察名單（2026-10 回測 221 檔次平均 -0.29%、7 個月全負，僅供參考）。
    走 gateway /market_snapshot，不另開 Shioaji 直連。"""
    try:
        r = requests.get('http://localhost:5455/market_snapshot', timeout=40)
        items = r.json().get('items', [])
    except Exception as e:
        print(f'[short] 全市場快照失敗: {e}')
        return []
    out = []
    for s in items:
        c, h, l = s.get('close'), s.get('high'), s.get('low')
        ch, v = s.get('change_rate'), s.get('total_volume') or 0
        if not c or not h or not l or l <= 0 or ch is None:
            continue
        amp = (h - l) / l * 100
        if ch <= -1.5 and 3 <= amp < 5 and v >= 500:
            out.append({'code': s['code'], 'name': s.get('name') or s['code'],
                        'close': c, 'chg': ch, 'amp': amp, 'vol': v})
    out.sort(key=lambda x: x['chg'])
    return out


def get_index_intraday():
    """加權指數即時漲跌幅（%）。走 Shioaji gateway（5455，常駐單一連線）。
    回傳 (漲跌幅, 指數值)，失敗回傳 (None, 0.0)"""
    try:
        r = requests.get('http://localhost:5455/snapshot',
                         params={'codes': 'IX0001'}, timeout=8)
        d = r.json().get('data', {}).get('IX0001')
        if d and d.get('change_rate') is not None:
            return round(float(d['change_rate']), 2), float(d['close'])
    except Exception as e:
        print(f'[gateway] 加權指數查詢失敗: {e}')
    return None, 0.0


def get_stock_sector(code: str) -> str:
    """從 FinMind TaiwanStockInfo 取股票產業別，供推播分族群顯示（/tmp 快取避免重複打 API）"""
    import json as _json
    cache_path = f'/tmp/stock_sector_{code}.json'
    if os.path.exists(cache_path):
        try:
            with open(cache_path) as f:
                return _json.load(f).get('industry', '')
        except Exception:
            pass
    try:
        res = requests.get(
            'https://api.finmindtrade.com/api/v4/data',
            params={'dataset': 'TaiwanStockInfo', 'data_id': code, 'token': TOKEN},
            timeout=10,
        )
        rows = res.json().get('data', [])
        industry = rows[0].get('industry_category', '') if rows else ''
        with open(cache_path, 'w') as f:
            _json.dump({'industry': industry}, f, ensure_ascii=False)
        return industry
    except Exception as e:
        print(f'[finmind] 產業別查詢失敗 {code}: {e}')
        return ''


# ── 主程式 ────────────────────────────────────────

def main():
    _init_finmind()

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

        # 2026-10 回測（3~10月、911檔次）：振幅 8-12% 組平均 -1.61%、3-5% 組 -0.69%，
        # 加振幅上限是唯一跨月穩定有效的修改，故設 3%~5%
        if 3 <= row['amp_pct'] < 5 and avg5 >= 3000 and row['chg_pct'] >= 1.5:
            streak = get_price_streak(code)
            if streak < 0:
                print(f'[daytrade] {code} {row["name"]} 連跌{-streak}天，排除')
                continue
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

    inst_net, inst_date = get_institutional_net()
    idx_chg, idx_val = get_index_intraday()
    short_list = get_short_candidates()

    # 大盤即時跌逾 0.5% 時跳過推播。實測「當日開盤跳空」對當日報酬相關性 0.72，
    # 遠優於前一日三大法人買賣超的 0.05，故改用即時指數而非落後一日的法人資料。
    SKIP_THRESHOLD = -0.5
    mkt_bearish_skip = idx_chg is not None and idx_chg <= SKIP_THRESHOLD
    if mkt_bearish_skip and candidates:
        print(f'[daytrade] 大盤即時 {idx_chg:+.2f}%（跌逾 {abs(SKIP_THRESHOLD)}%），跳過本日{len(candidates)}檔候選')
        candidates = []

    # ── 組訊息 ────────────────────────────────────────

    lines = [f"📊 <b>當沖候選</b>｜{TODAY} 09:10\n"]
    if idx_chg is not None:
        lines.append(f"🌐 大盤即時：{idx_val:,.2f}（{idx_chg:+.2f}%）\n")
    else:
        lines.append("🌐 大盤即時：資料取得失敗\n")
    if inst_date:
        lines.append(f"　前一日三大法人現貨：買賣超 {inst_net:+,.1f} 億（{inst_date}）\n")
    lines.append("📋 <b>篩選條件</b>")
    lines.append("今日量前20（Shioaji即時）＋ 3%≤振幅&lt;5% ＋ 近5均量&gt;3000張 ＋ 漲幅&gt;1.5%\n")

    if candidates:
        lines.append(f"✅ 符合 {len(candidates)} 檔：\n")
        sectors = {}
        for c in candidates:
            sectors.setdefault(get_stock_sector(c['code']) or '其他', []).append(c)
        for sector, group in sectors.items():
            lines.append(f"🏷️ <b>{html.escape(sector)}</b>\n")
            for c in group:
                pos_emoji = "🔴" if c['close_pos'] >= 80 else "🟡" if c['close_pos'] >= 50 else "🟢"
                in_price_band = 100 <= c['close'] <= 150
                in_price_band_hi = 1000 <= c['close'] <= 1500
                ai = analyze_stock(
                    code=c['code'], name=c['name'], close=c['close'],
                    chg_pct=c['chg_pct'], amp_pct=c['amp_pct'],
                    vol_k=c['vol_k'], avg5=c['avg5'], close_pos=c['close_pos'],
                    fi_net=0, signal="", strategy="當沖"
                )
                entry = (
                    f"{pos_emoji} <b>{c['code']} {c['name']}</b>\n"
                    f"   收:{c['close']:.1f}  漲:{c['chg_pct']:+.1f}%  振:{c['amp_pct']:.1f}%\n"
                    f"   量:{c['vol_k']:,}張  近5均:{c['avg5']:,}張  收盤位:{c['close_pos']:.0f}%\n"
                    f"{format_ai_block(ai)}"
                )
                if in_price_band:
                    entry = f"💰 <b>100-150價格帶</b>\n<blockquote>{entry}</blockquote>"
                elif in_price_band_hi:
                    entry = f"💎 <b>1000-1500價格帶</b>\n<blockquote>{entry}</blockquote>"
                lines.append(entry + "\n")
        lines.append("⚡ 進場參考：開盤後5~15分鐘確認方向再進")
        lines.append("🛑 停損：跌破進場價 -1.5% 出清")
    elif mkt_bearish_skip:
        lines.append(f"🌧️ 大盤即時跌逾 0.5%（{idx_chg:+.2f}%），今日跳過推播")
    else:
        lines.append("❌ 今日無符合當沖條件標的\n建議觀望或等待盤中突破訊號")

    # 空方觀察：回測無 edge，只列條件符合者，不列入候選、不追蹤
    if short_list:
        lines.append("\n🔻 <b>空方觀察</b>（跌幅&gt;1.5%＋振幅3~5%＋量&gt;500張）")
        for s in short_list[:10]:
            lines.append(f"　{s['code']} {html.escape(s['name'])}  {s['close']:.1f}  {s['chg']:+.1f}%  振{s['amp']:.1f}%  量{s['vol']:,}張")
        lines.append("⚠️ 此名單回測平均為負（7 個月全虧），僅供觀察，不建議交易")

    lines.append("\n⚠️ 數據參考，非投資建議，買賣自負")

    msg = "\n".join(lines)
    send_telegram(msg)
    print(msg)

    # 儲存候選清單供盤中監控腳本使用
    import json

    candidate_list = [{
        'code': c['code'], 'name': c['name'], 'close': c['close'],
        'checks': {'量前20': True, '3%≤振幅<5%': True, '近5日均量≥3000張': True, '漲幅≥1.5%': True},  # 單一AND篩選，能列入候選就是全通過
    } for c in candidates]
    with open(os.path.join(os.path.dirname(__file__), '..', 'telebot', 'data', 'daytrade_candidates.json'), 'w') as f:
        json.dump(candidate_list, f, ensure_ascii=False)
    print(f'[daytrade] 候選清單已寫入 telebot/data/daytrade_candidates.json: {[c["code"] for c in candidate_list]}')

    # 存進當沖 10:30 追蹤紀錄（供 check_daytrade_track.py 比對是否仍上漲）
    try:
        track_path = os.path.join(os.path.dirname(__file__), '..', 'telebot', 'data', 'daytrade_track.json')
        track = {}
        if os.path.exists(track_path):
            with open(track_path, encoding='utf-8') as f:
                track = json.load(f)
        track[TODAY] = {
            'candidates': [
                {'code': c['code'], 'name': c['name'], 'baseline': round(c['close'] / (1 + c['chg_pct'] / 100), 2)}
                for c in candidates
            ],
            'checked': False, 'checked_at': None, 'track_results': [],
        }
        with open(track_path, 'w', encoding='utf-8') as f:
            json.dump(track, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f'[daytrade_track] 寫入失敗: {e}')


if __name__ == '__main__':
    main()
