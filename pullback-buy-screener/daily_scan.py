#!/usr/bin/env python3
"""回後買上漲選股 — 每日自動掃描，結果存檔供 command-center 被動讀取（比照 swing_candidates.json 格式）"""
import json
import logging
from datetime import datetime

import screener

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('daily_scan')

OUT_PATH = '/tmp/pullback_candidates.json'
N_UNIVERSE = 600


def main():
    try:
        results = screener.scan(N_UNIVERSE)
    except Exception as e:
        log.error(f'掃描失敗: {e}')
        return
    out = {'date': datetime.now().strftime('%Y-%m-%d %H:%M'), 'results': results}
    with open(OUT_PATH, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    log.info(f'掃描完成，{len(results)} 檔候選，已寫入 {OUT_PATH}')


if __name__ == '__main__':
    main()
