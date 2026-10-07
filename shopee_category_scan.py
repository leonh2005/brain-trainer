"""改用「分類 × 依銷量排序」掃蝦皮：categoryids=<catid>&by=sales，可翻頁。
結果存 /tmp/shopee_cat_state.json。

用法：
  python3 shopee_cat_scan.py --probe          # 只測 1 個分類翻 2 頁，確認翻頁可用
  python3 shopee_cat_scan.py --pages 2 --gap 12
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.expanduser("~/CCProject"))
from selenium import webdriver
from selenium.webdriver.firefox.options import Options

import shopee_taxid_scan as S

STATE = "/tmp/shopee_cat_state.json"
CATS = "/tmp/shopee_cats.json"


def js_fetch(drv, url):
    script = """
      const cb = arguments[0];
      fetch('__URL__', {credentials: 'include'})
        .then(x => x.text()).then(t => cb(t)).catch(() => cb(''));
    """.replace("__URL__", url)
    return drv.execute_async_script(script)


def search_cat(drv, catid, page):
    u = (f"/api/v4/search/search_items?by=sales&categoryids={catid}"
         f"&limit=60&newest={page * 60}&order=desc&page_type=search&version=2")
    raw = js_fetch(drv, u)
    if not raw or not raw.strip().startswith("{"):
        return None
    try:
        d = json.loads(raw)
    except Exception:
        return None
    return [i["item_basic"] for i in d.get("items", [])]


def load_cats():
    if os.path.exists(CATS):
        return json.load(open(CATS))
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=2)
    ap.add_argument("--gap", type=int, default=12)
    ap.add_argument("--probe", action="store_true")
    args = ap.parse_args()

    cats = load_cats()
    if not cats:
        sys.exit("先跑 shopee_cat_probe.py 取得 /tmp/shopee_cats.json")
    targets = [(c["catid"], c.get("name", "")) for c in cats]
    if args.probe:
        targets = targets[:1]
        args.pages = 2
        print("=== PROBE 模式：只測 1 個分類 2 頁 ===")

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

    state = {"cats": {}}
    if os.path.exists(STATE):
        state = json.load(open(STATE))

    try:
        drv.get("https://shopee.tw/")
        time.sleep(5)
        for catid, name in targets:
            key = str(catid)
            if key in state["cats"] and not args.probe:
                print(f"  [{name}] 已有，跳過")
                continue
            got = []
            for pg in range(args.pages):
                items = None
                for attempt in range(3):
                    items = search_cat(drv, catid, pg)
                    if items is not None:
                        break
                    time.sleep(args.gap)
                if not items:
                    print(f"  [{name}] 第 {pg+1} 頁失敗，停止此分類")
                    break
                got.extend(items)
                print(f"  [{name}] 第 {pg+1} 頁 ✓ {len(items)} 件（此分類累計 {len(got)}）", flush=True)
                if len(items) < 60:
                    break
                time.sleep(args.gap)
            if got:
                state["cats"][key] = {"name": name, "catid": catid, "items": got}
                json.dump(state, open(STATE, "w"), ensure_ascii=False)
            time.sleep(args.gap)
    finally:
        try:
            drv.quit()
        except Exception:
            pass
        S.restore_firefox_ini()

    n_cats = len(state["cats"])
    n_items = sum(len(v["items"]) for v in state["cats"].values())
    print(f"\n完成：{n_cats} 個分類、{n_items} 件商品 → {STATE}")


if __name__ == "__main__":
    main()
