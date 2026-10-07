"""分析分類掃描結果：by=sales 撈到的都是高銷量商品，比關鍵字抽樣準得多。"""
import json
import re
import sys

STATE = sys.argv[1] if len(sys.argv) > 1 else "/tmp/shopee_cat_state.json"
THRESHOLD = 200_000

state = json.load(open(STATE))
shops = {}
cat_of_item = {}

for key, v in state["cats"].items():
    cname = v.get("name", key)
    for it in v["items"]:
        sid = it.get("shopid")
        if not sid:
            continue
        price = (it.get("price") or 0) / 100000
        sold = it.get("sold") or 0
        hist = it.get("historical_sold") or 0
        s = shops.setdefault(sid, {
            "shopid": sid, "name": it.get("shop_name", ""),
            "loc": it.get("shop_location", ""),
            "month_rev": 0.0, "hist_rev": 0.0, "n": 0,
            "max_item_rev": 0.0, "max_item": "",
            "verified": it.get("shopee_verified"),
            "official": it.get("is_official_shop"),
            "cats": set(),
        })
        rev = price * sold
        s["month_rev"] += rev
        s["hist_rev"] += price * hist
        s["n"] += 1
        s["cats"].add(cname)
        if rev > s["max_item_rev"]:
            s["max_item_rev"] = rev
            s["max_item"] = (it.get("name") or "")[:36]

for s in shops.values():
    m = re.search(r"(?<!\d)(\d{8})(?!\d)", s["name"])
    s["taxid"] = m.group(1) if m else None
    s["cats"] = sorted(s["cats"])

ranked = sorted(shops.values(), key=lambda x: -x["month_rev"])
no_id = [s for s in ranked if not s["taxid"]]
sus = [s for s in no_id if not s.get("official") and not s.get("verified")]
hits = [s for s in sus if s["month_rev"] >= THRESHOLD or s["max_item_rev"] >= THRESHOLD]

print(f"分類 {len(state['cats'])} 個｜商品 {sum(len(v['items']) for v in state['cats'].values())} 件")
print(f"賣場 {len(ranked)}｜店名未揭露統編 {len(no_id)}｜非官方/優選 {len(sus)}")
print(f"\n未揭露統編 + 非官方/優選 + 月營業額估 ≥ {THRESHOLD:,}：{len(hits)} 家\n")
print(f"{'賣場名稱':36} {'月營業額估':>12} {'單品最大':>11} {'件數':>4}  {'認證':6} 地區")
for s in hits:
    tag = "官方" if s.get("official") else ("優選" if s.get("verified") else "")
    print(f"{s['name'][:34]:36} {s['month_rev']:>12,.0f} {s['max_item_rev']:>11,.0f} "
          f"{s['n']:>4}  {tag:6} {s['loc']}")

json.dump(ranked, open("/tmp/cat_ranked.json", "w"), ensure_ascii=False, indent=1)
json.dump(hits, open("/tmp/cat_hits.json", "w"), ensure_ascii=False, indent=1)
print(f"\n→ /tmp/cat_ranked.json（全部）、/tmp/cat_hits.json（命中）")
