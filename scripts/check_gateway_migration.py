#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""驗證 intraday_monitor / daytrade_alert 改走 gateway 後的第一個交易日。

2026-11-12 改動：兩個程式不再自行登入 Shioaji（同帳號多登入會互踢 session，
當天 gateway 被踢 20 次，隔日沖結算連續兩次取不到價）。
這支腳本在隔天早上跑兩次，把關鍵指標推 Telegram：
  09:05  盤中監控啟動後：gateway 就緒、tick 有在流、沒有直連登入
  09:20  當沖掃描後：daytrade 沒自己登入、沒有快照失敗

用法：python3 scripts/check_gateway_migration.py [--expect-daytrade]
"""
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime

CC = os.path.expanduser('~/CCProject')
TOKEN_FILE = f'{CC}/.secrets/telegram_token.txt'
CHAT_ID = '7556217543'
GATEWAY = 'http://localhost:5455'
MONITOR_LOG = f'{CC}/logs/intraday_monitor.log'
DAYTRADE_LOG = f'{CC}/logs/daytrade.log'
GATEWAY_LOG = '/tmp/shioaji-gateway.log'   # LaunchAgent stdout；session down 印在這（SDK log 沒有）
TODAY = date.today().isoformat()


def send(text):
    try:
        with open(TOKEN_FILE) as f:
            token = f.read().strip()
        data = urllib.parse.urlencode({'chat_id': CHAT_ID, 'text': text}).encode()
        urllib.request.urlopen(f'https://api.telegram.org/bot{token}/sendMessage',
                               data=data, timeout=15)
    except Exception as e:
        print(f'[telegram] 發送失敗: {e}')


def tail(path, limit=200_000):
    try:
        with open(path, 'rb') as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - limit))
            return f.read().decode('utf-8', errors='replace')
    except Exception:
        return ''


def check_health():
    try:
        with urllib.request.urlopen(f'{GATEWAY}/health', timeout=8) as r:
            h = json.loads(r.read())
    except Exception as e:
        return [('❌', f'gateway /health 無回應: {e}')]
    ticks = h.get('ticks') or {}
    out = [
        ('✅' if h.get('logged_in') and h.get('state') == 'ok' else '❌',
         f"gateway 登入狀態: {h.get('state')} (gen={h.get('generation')})"),
        ('✅' if (ticks.get('subscribed_codes') or 0) > 0 else '❌',
         f"tick 訂閱檔數: {ticks.get('subscribed_codes')}"),
        ('✅' if (ticks.get('last_tick_age_s') is not None
                  and ticks.get('last_tick_age_s') < 30) else '❌',
         f"最後一筆 tick: {ticks.get('last_tick_age_s')} 秒前"),
        # 這個計數是程序生命週期累計（不是按日重置），僅供參考；真正的「今日」看下面日誌檢查
        ('ℹ️', f"gateway 程序啟動以來 session down: {h.get('session_down_count')} 次"),
    ]
    return out


def since_last_run(log, marker):
    """只取「最後一次啟動」之後的內容：日誌是跨日 append，舊的直連登入紀錄不能算進來。"""
    i = log.rfind(marker)
    return log[i:] if i >= 0 else ''


def check_monitor():
    log = since_last_run(tail(MONITOR_LOG), '[startup] intraday_monitor 啟動')
    out = []
    out.append(('✅' if log else '❌', 'monitor 今日已啟動'))
    out.append(('✅' if 'gateway 就緒' in log else '❌', 'monitor 已連上 gateway'))
    out.append(('❌' if 'Shioaji 暖機完成' in log else '✅', 'monitor 未自行登入 Shioaji'))
    tb = log.count('Traceback (most recent call last)')
    out.append(('✅' if tb == 0 else '❌', f'monitor traceback: {tb} 次'))
    try:
        r = subprocess.run(['pgrep', '-f', 'intraday_monitor.py'],
                           capture_output=True, text=True)
        out.append(('✅' if r.stdout.strip() else '❌', 'monitor 程序在執行中'))
    except Exception:
        pass
    return out


def check_daytrade():
    log = since_last_run(tail(DAYTRADE_LOG), f'[run] {TODAY}')
    out = []
    out.append(('✅' if log else '❌', 'daytrade 今日已執行'))
    out.append(('❌' if '[sj] 永豐 API 登入成功' in log else '✅',
                'daytrade 未自行登入 Shioaji'))
    out.append(('❌' if '[gateway] 全市場快照失敗' in log else '✅',
                'daytrade 全市場快照正常'))
    return out


GEN_FILE = '/tmp/gateway_migration_gen.json'


def check_generation():
    """兩次執行之間的 generation 增量＝盤中重登次數。

    正常：Shioaji 每小時 token 失效會重登 1 次（隔夜 9 小時約 9 次）。
    異常：2026-11-13 的「逾時→重登→重訂閱」迴圈，15 分鐘就跳 5 次。
    """
    try:
        with urllib.request.urlopen(f'{GATEWAY}/health', timeout=8) as r:
            gen = json.loads(r.read()).get('generation')
    except Exception:
        return []
    prev = None
    try:
        with open(GEN_FILE) as f:
            saved = json.load(f)
        if saved.get('date') == TODAY:
            prev = saved.get('gen')
    except Exception:
        pass
    try:
        with open(GEN_FILE, 'w') as f:
            json.dump({'date': TODAY, 'gen': gen}, f)
    except Exception:
        pass
    if prev is None:
        return [('ℹ️', f'generation={gen}（首次執行，下次才能算增量）')]
    delta = (gen or 0) - prev
    return [('✅' if delta <= 3 else '❌',
             f'兩次檢查間重登 {delta} 次（generation {prev}→{gen}，正常 ≤3）')]


def check_gateway_log():
    """SDK 回呼版 session down（`[YYYY-MM-DD HH:MM:SS] session down; api cleared`）。
    注意：_mark_down 觸發的重登不會印這行，真正的重登量看 generation。"""
    log = tail(GATEWAY_LOG, 400_000)
    downs = len(re.findall(rf'\[{TODAY} [0-9:]{{8}}\] session down', log))
    return [('ℹ️', f'SDK 回報 session down 行數: {downs}')]


def main():
    expect_daytrade = '--expect-daytrade' in sys.argv
    checks = check_health() + check_monitor() + check_generation() + check_gateway_log()
    if expect_daytrade:
        checks += check_daytrade()
    fails = sum(1 for icon, _ in checks if icon == '❌')
    header = f"🔍 gateway 遷移驗證 {datetime.now():%m-%d %H:%M}｜" + \
             ('全部正常' if fails == 0 else f'{fails} 項失敗')
    text = '\n'.join([header] + [f'{icon} {msg}' for icon, msg in checks])
    print(text)
    if '--dry' not in sys.argv:
        send(text)


if __name__ == '__main__':
    main()
