#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""隔日沖盤後檢查：驗證 13:00 選股流程每一步都真的完成，結果推 Telegram。

為什麼需要：2026-10-28 三處（Telegram／chip-tracker 清單／command-center 卡片）
看似沒更新，實際是深度篩選跑了 160 秒（候選從 13 檔暴增 65 檔），13:03:07 才寫檔。
沒有事後檢查的話，「慢」和「壞」在當下無法分辨。

檢查項目（依資料流順序）：
  1. screener 今日有跑完且耗時合理
  2. swing_candidates.json 日期是今天
  3. near_miss_detail.json 有今天
  4. chip-tracker「隔日沖」清單內容 == swing_candidates
  5. command-center /api/signals/swing 回傳今日資料
  6. gateway 今日重登次數（暴增代表連線不穩）

大盤跌逾 1% 跳過日不算失敗：screener 本來就不寫檔，清單維持昨日。
用法：python3 scripts/swing_postcheck.py [--dry]
排程：每交易日 13:45
"""
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime

CC = os.path.expanduser('~/CCProject')
TOKEN_FILE = f'{CC}/.secrets/telegram_token.txt'
CHAT_ID = '7556217543'
SCREENER_LOG = f'{CC}/logs/screener.log'
CAND = f'{CC}/telebot/data/swing_candidates.json'
NEAR = f'{CC}/telebot/data/near_miss_detail.json'
GATEWAY_LOG = f'{CC}/shioaji-gateway/shioaji.log'
CHIP_API = 'http://127.0.0.1:5950/svc/5850/api/data'
CC_API = 'http://127.0.0.1:5950/api/signals/swing'

# 深度篩選耗時上限（秒）。10/28 是 160.4s / 65 檔；超過這個代表明顯異常
SLOW_THRESHOLD = 300
# gateway 單日重登超過此次數視為異常（正常基準 0-6 次）
RELOGIN_LIMIT = 20


def get_json(url, timeout=30):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def read_json(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def send(text, dry=False):
    if dry:
        print(text)
        return
    with open(TOKEN_FILE) as f:
        token = f.read().strip()
    data = urllib.parse.urlencode({'chat_id': CHAT_ID, 'text': text}).encode()
    req = urllib.request.Request(f'https://api.telegram.org/bot{token}/sendMessage', data=data)
    with urllib.request.urlopen(req, timeout=15) as r:
        r.read()


def screener_status(today):
    """從 log 找今日最後一次選股結果。回傳 (狀態, 說明)。"""
    if not os.path.exists(SCREENER_LOG):
        return 'fail', 'screener.log 不存在'
    markers = []
    with open(SCREENER_LOG, encoding='utf-8', errors='replace') as f:
        for line in f:
            if today not in line:
                continue
            if '選股完成' in line or '跳過選股' in line or '選股失敗' in line:
                markers.append(line.rstrip())
            elif '深度篩選耗時' in line:
                markers.append(line.rstrip())
    if not markers:
        return 'fail', f'log 找不到 {today} 的選股紀錄（排程沒跑？）'
    joined = '\n'.join(markers)
    if '選股失敗' in joined:
        return 'fail', '選股失敗（見 screener.log）'
    if '跳過選股' in joined:
        return 'skip', '大盤跌逾 1%，今日跳過選股'
    if '選股完成' not in joined:
        return 'fail', 'log 只有開始沒有完成（執行中斷）'

    # 取深度篩選耗時
    secs = None
    for line in markers:
        if '深度篩選耗時' in line:
            try:
                secs = float(line.split('深度篩選耗時')[1].split('s')[0].strip())
            except (IndexError, ValueError):
                pass
    if secs is None:
        return 'ok', '選股完成（未記錄耗時）'
    note = f'選股完成，深度篩選 {secs:.1f}s'
    if secs > SLOW_THRESHOLD:
        return 'warn', note + f'（超過 {SLOW_THRESHOLD}s 門檻）'
    return 'ok', note


def check(today=None):
    """回傳 (rows, level)。today 為 None 時用系統日期（供測試傳入其他日期）。"""
    today = today or date.today().isoformat()
    rows = []   # (等級, 項目, 說明)

    status, note = screener_status(today)
    rows.append((status, 'screener', note))
    if status in ('fail',):
        return rows, 'fail'

    # 大盤跳過日：後續檢查不適用，清單維持昨日是預期行為
    if status == 'skip':
        return rows, 'skip'

    # 2. swing_candidates.json
    try:
        d = read_json(CAND)
        cdate = str(d.get('date', ''))[:10]
        n = len(d.get('results', []))
        if cdate == today:
            rows.append(('ok', 'swing_candidates', f'{cdate}，{n} 檔'))
        else:
            rows.append(('fail', 'swing_candidates', f'日期是 {cdate}，不是今天'))
    except Exception as e:
        rows.append(('fail', 'swing_candidates', f'讀取失敗: {e}'))
        d = {}

    # 3. near_miss_detail.json
    try:
        nm = read_json(NEAR)
        if today in nm:
            rows.append(('ok', 'near_miss', f'{len(nm[today] or [])} 檔'))
        else:
            rows.append(('fail', 'near_miss', '沒有今天的紀錄'))
    except Exception as e:
        rows.append(('fail', 'near_miss', f'讀取失敗: {e}'))

    # 4. chip-tracker 隔日沖清單 == swing_candidates
    want = sorted(r['code'] for r in d.get('results', []) if r.get('code'))
    try:
        data = get_json(CHIP_API)
        got = []
        for lst in data.get('lists', []):
            if lst.get('name') == '隔日沖':
                got = sorted(s['code'] for s in lst.get('stocks', []))
        if got == want:
            rows.append(('ok', 'chip-tracker 隔日沖', f'{len(got)} 檔，與候選一致'))
        else:
            rows.append(('fail', 'chip-tracker 隔日沖',
                         f'清單 {got} ≠ 候選 {want}'))
    except Exception as e:
        rows.append(('fail', 'chip-tracker 隔日沖', f'API 失敗: {e}'))

    # 5. command-center 卡片
    try:
        sig = get_json(CC_API)
        if not sig.get('ok'):
            rows.append(('fail', 'command-center', f"error: {sig.get('error')}"))
        else:
            upd = str(sig.get('data', {}).get('date', ''))[:10]
            if upd == today:
                rows.append(('ok', 'command-center', f'卡片資料 {upd}'))
            else:
                rows.append(('fail', 'command-center', f'卡片停在 {upd}'))
    except Exception as e:
        rows.append(('fail', 'command-center', f'API 失敗: {e}'))

    # 6. gateway 重登次數
    try:
        cnt = 0
        if os.path.exists(GATEWAY_LOG):
            with open(GATEWAY_LOG, encoding='utf-8', errors='replace') as f:
                for line in f:
                    if today in line and 'performing new login' in line:
                        cnt += 1
        if cnt > RELOGIN_LIMIT:
            rows.append(('warn', 'gateway 重登', f'{cnt} 次（超過 {RELOGIN_LIMIT}，連線不穩）'))
        else:
            rows.append(('ok', 'gateway 重登', f'{cnt} 次'))
    except Exception as e:
        rows.append(('warn', 'gateway 重登', f'無法統計: {e}'))

    level = 'fail' if any(r[0] == 'fail' for r in rows) else (
        'warn' if any(r[0] == 'warn' for r in rows) else 'ok')
    return rows, level


def main():
    dry = '--dry' in sys.argv
    today = date.today().isoformat()
    rows, level = check(today)

    icon = {'ok': '✅', 'warn': '⚠️', 'fail': '❌', 'skip': '🌧️'}[level]
    title = {'ok': '隔日沖盤後檢查：正常',
             'warn': '隔日沖盤後檢查：有警告',
             'fail': '隔日沖盤後檢查：異常',
             'skip': '隔日沖盤後檢查：今日跳過'}[level]

    lines = [f'{icon} {title}', f'{today} {datetime.now():%H:%M}', '']
    for lv, item, note in rows:
        mark = {'ok': '✓', 'warn': '⚠', 'fail': '✗', 'skip': '—'}[lv]
        lines.append(f'{mark} {item}：{note}')
    send('\n'.join(lines), dry=dry)
    return 1 if level == 'fail' else 0


if __name__ == '__main__':
    sys.exit(main())
