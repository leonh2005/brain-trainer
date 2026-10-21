# -*- coding: utf-8 -*-
"""隔日沖留池監控：候選保留 10 個交易日，盤中出現發動訊號就推 Telegram。

觸發條件（門檻先用預設值，之後回測再調）：
- 接近漲停：當日漲幅 >= 7%
- 突破候選日高：現價 > 候選日最高價，且成交金額 >= 近 5 日均額 1.5 倍

同日同標的同類型只通知一次。資料來源：swing_track.json（候選歷史）＋ shioaji-gateway。
用法：python3 swing_pool_watch.py [--dry]
"""
import fcntl
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime

CC = os.path.expanduser('~/CCProject')
TRACK = f'{CC}/telebot/data/swing_track.json'
STATE = f'{CC}/data/swing_pool_alerts.json'
LOCK = f'{CC}/data/swing_pool_watch.lock'
GW = 'http://127.0.0.1:5455'
TOKEN_FILE = f'{CC}/.secrets/telegram_token.txt'
CHAT_ID = '7556217543'

POOL_DAYS = 10
ALERT_PCT = 7.0
BREAK_VOL_MULT = 1.5


def get_json(url):
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.loads(r.read())


def load_pool():
    with open(TRACK, encoding='utf-8') as f:
        track = json.load(f)
    dates = sorted(track)[-POOL_DAYS:]
    pool = {}
    for d in dates:
        for c in track[d].get('candidates', []):
            pool[c['code']] = {'name': c.get('name', ''), 'cand_date': d,
                               'cand_close': c.get('close')}
    return pool


def daily_bars(code, days=15):
    url = f'{GW}/daily_ohlcv?' + urllib.parse.urlencode({'code': code, 'days': days})
    res = get_json(url)
    return res.get('bars', []) if res.get('ok') else []


def send(text):
    token = open(TOKEN_FILE).read().strip()
    data = urllib.parse.urlencode({'chat_id': CHAT_ID, 'text': text}).encode()
    req = urllib.request.Request(f'https://api.telegram.org/bot{token}/sendMessage', data=data)
    urllib.request.urlopen(req, timeout=15).read()


def main():
    dry = '--dry' in sys.argv
    now = datetime.now()
    if not dry and (now.weekday() >= 5 or not (9 <= now.hour <= 13) or (now.hour == 13 and now.minute > 35)):
        print('非盤中時段，結束')
        return 0

    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    lock = open(LOCK, 'w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print('另一個監控執行中，跳過')
        return 0

    pool = load_pool()
    if not pool:
        print('池內沒有候選（近 10 個交易日無名單）')
        return 0

    alerts = json.load(open(STATE)) if os.path.exists(STATE) else {}
    today = date.today().isoformat()
    alerts.setdefault(today, {})

    codes = list(pool)
    res = get_json(f'{GW}/snapshot?' + urllib.parse.urlencode({'codes': ','.join(codes)}))
    if not res.get('ok'):
        print('取快照失敗：', res.get('error'))
        return 1
    snaps = res['data']

    fired = 0
    for code, info in pool.items():
        sn = snaps.get(code)
        if not sn or sn.get('close') is None:
            continue
        price, rate = sn['close'], sn.get('change_rate') or 0.0
        bars = daily_bars(code)
        if not bars:
            continue
        prev5 = bars[-6:-1] if len(bars) >= 6 else bars[:-1]
        avg_amt5 = sum(b['close'] * b['volume'] for b in prev5) / max(len(prev5), 1)
        cand_high = next((b['high'] for b in bars if b['date'] == info['cand_date']), None)
        today_amt = sn.get('total_amount') or 0

        reasons = []
        if rate >= ALERT_PCT:
            reasons.append(f'接近漲停（{rate:+.1f}%）')
        if cand_high and price > cand_high and avg_amt5 and today_amt >= avg_amt5 * BREAK_VOL_MULT:
            reasons.append(f'帶量突破候選日高 {cand_high:.1f}')
        if not reasons:
            continue

        key = code
        done = alerts[today].get(key, {}).get('reasons', [])
        new = [r for r in reasons if r.split('（')[0] not in done]
        if not new:
            continue

        msg = (f'🚀 隔日沖池發動：{code} {info["name"]}\n'
               f'現價 {price:.2f}（{rate:+.1f}%）｜觸發：{"、".join(new)}\n'
               f'候選日 {info["cand_date"]}（收 {info["cand_close"]}）\n'
               f'https://tw.stock.yahoo.com/quote/{code}.TW')
        print(msg.replace('\n', ' | '))
        if not dry:
            send(msg)
            alerts[today][key] = {'reasons': done + [r.split('（')[0] for r in new]}
        fired += 1

    if not dry and fired:
        with open(STATE, 'w') as f:
            json.dump(alerts, f, ensure_ascii=False)
    print(f'檢查 {len(pool)} 檔，觸發 {fired} 則{"（dry run，未推播）" if dry else ""}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
