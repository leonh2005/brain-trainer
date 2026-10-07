"""對「未揭露統編 + 非官方/優選」按月營業額排序的前 N 家，
逐個開賣場首頁抓 company_id（統編）與 company_name（公司名）。
不做任何篩除，只把查到的統編填進欄位，供人工核對。
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.expanduser("~/CCProject"))
from selenium import webdriver
from selenium.webdriver.firefox.options import Options

import shopee_taxid_scan as S

TOPN = int(sys.argv[1]) if len(sys.argv) > 1 else 300
OUT = sys.argv[2] if len(sys.argv) > 2 else "/tmp/shop_taxid_top300.json"
GAP = 0.6

RANKED = "/tmp/cat_ranked.json"
shops = json.load(open(RANKED))
shops = [s for s in shops if not s["taxid"] and not s.get("official") and not s.get("verified")]
shops = sorted(shops, key=lambda x: -x["month_rev"])[:TOPN]
print(f"目標 {len(shops)} 家（未揭露統編、非官方/優選，按月營業額排序）", flush=True)


SHOPEE_TAXID = "56801904"   # 蝦皮自己的統編（頁尾），不可誤收


def extract(html):
    """取賣場統編與公司名，兩種來源都要看：
       1) 蝦皮官方欄位  company_id / company_name（顯示在「公司統編:」區塊）
       2) 賣家自己打在賣場介紹的「統編:12345678」或「統一編號:12345678」
    """
    if not html:
        return None, None
    # 1) 官方欄位
    m = re.search(r'"company_id"\s*:\s*"?(\d{8})"?', html)
    if m and m.group(1) != SHOPEE_TAXID:
        c = re.search(r'"company_name"\s*:\s*"([^"]{1,60})"', html)
        return m.group(1), (c.group(1) if c else None)
    # 2) 賣場介紹自填
    cname = re.search(r"公司名稱[：:\s]{0,3}([^\s<、,，|]{2,30})", html)
    for pat in (r"統編[：:\s]{0,3}(\d{8})", r"統一編號[：:\s]{0,3}(\d{8})"):
        for tid in re.findall(pat, html):
            if tid != SHOPEE_TAXID:
                return tid, (cname.group(1) if cname else None)
    return None, None


S.ensure_profile()
S.backup_firefox_ini()
opts = Options()
opts.add_argument("--headless")
opts.add_argument("-profile")
opts.add_argument(S.PROFILE_WORK)
drv = webdriver.Firefox(options=opts)
drv.set_window_size(1440, 900)
drv.set_page_load_timeout(45)
drv.set_script_timeout(45)


def js(url):
    literal = json.dumps(str(url))
    script = ("const cb = arguments[0];"
              f"fetch({literal}, {{credentials: 'include'}})"
              ".then(x => x.text()).then(t => cb(t)).catch(() => cb(''));")
    return drv.execute_async_script(script)


try:
    drv.get("https://shopee.tw/")
    time.sleep(5)
    for i, s in enumerate(shops, 1):
        sid = s["shopid"]
        s["shop_taxid"] = None
        s["shop_company"] = None
        s["account"] = None
        try:
            a = json.loads(js(f"/api/v4/shop/get_shop_base?shopid={sid}")).get("data", {}).get("account")
            acct = a.get("username") if isinstance(a, dict) else a
            s["account"] = acct
            if acct:
                time.sleep(GAP)
                html = js(f"https://shopee.tw/{acct}")
                s["shop_taxid"], s["shop_company"] = extract(html)
        except Exception as e:
            pass
        tid = s["shop_taxid"] or "—"
        print(f"{i:3}/{len(shops)} {s['name'][:26]:28} {str(s['account'] or '?'):18} {tid:>10} "
              f"{s['shop_company'] or ''}", flush=True)
        if i % 20 == 0:
            json.dump(shops, open(OUT, "w"), ensure_ascii=False, indent=1)
        time.sleep(GAP)
    json.dump(shops, open(OUT, "w"), ensure_ascii=False, indent=1)
    n = sum(1 for s in shops if s["shop_taxid"])
    print(f"\n完成：{len(shops)} 家，其中 {n} 家賣場首頁有統編 → {OUT}", flush=True)
finally:
    try:
        drv.quit()
    except Exception:
        pass
    S.restore_firefox_ini()
