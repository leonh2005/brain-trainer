# 支撐位偵測功能 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 command-center 的隔日沖候選、自選股（均線警報卡）、回後買候選（新卡片）上，讓 Steven 能點擊股票查看多維度支撐位分析（波段低點、趨勢線、均線、整數關卡、K線形態、成交量共6種訊號）。

**Architecture:** 新增共用模組 `command-center/support.py` 做支撐位偵測，透過既有的 `shioaji-gateway`(5455) `/daily_ohlcv` 抓250天日K。`command-center/sources.py` 新增 `/api/support/{code}` 端點（5分鐘快取）供各卡片呼叫。回後買選股(pullback-buy-screener, 5960)目前無每日排程與存檔，新增 `daily_scan.py` + LaunchAgent 讓它跟隔日沖一樣被動供 command-center 讀取。前端新增彈窗 UI，在既有卡片的股票旁加小圖示觸發。

**Tech Stack:** Python 3 (FastAPI + 既有 sources.py 慣例)、原生 JS（無框架，沿用 index.html 既有寫法）、launchd LaunchAgent。

**Spec:** `docs/superpowers/specs/2026-08-20-support-level-detection-design.md`

## Global Constraints

- 支撐位計算全部基於 250 天日K，資料不足的訊號直接跳過，不強算、不中斷整體流程。
- 6 種訊號一次到位實作（不分階段）：波段低點、趨勢線、均線、整數關卡、K線形態(輔助)、成交量確認(強度加權)。
- 支撐位輸出格式固定：`{price, strength(1-5), sources:[...]}`。
- 不建立正式 pytest unit test，改用真實資料人工核對合理性（技術分析邏輯難定義「正確答案」）。
- 所有新 API 端點遵循既有 `sources.py` 的 `@_wrap` 回傳 `{ok,data|error,updated}` 慣例。
- pullback 每日掃描沿用既有 `screener.scan()`，不重寫掃描邏輯本身。

---

## Task 1: support.py — K線形態與成交量輔助函式

**Files:**
- Create: `command-center/support.py`

**Interfaces:**
- Produces: `is_hammer(bar) -> bool`、`is_doji(bar) -> bool`、`is_bullish_engulfing(prev, cur) -> bool`、`has_long_lower_shadow(bar) -> bool`、`bottoming_pattern(bars, idx) -> bool`、`volume_confirmed(bars, idx) -> bool`。`bar` 是 `{date,open,high,low,close,volume}` dict。

- [ ] **Step 1: 建立 support.py 並寫入 K 線形態判斷函式**

```python
"""支撐位偵測 — 波段低點、趨勢線、均線、整數關卡、K線形態、成交量 六種訊號綜合判斷
輸入日K陣列（[{date,open,high,low,close,volume}]，舊到新），輸出支撐位列表 [{price,strength,sources}]"""


def is_hammer(bar):
    """錘子線/倒錘子線：下影線或上影線長度 >= 實體2倍，且實體佔全幅 <= 35%"""
    o, h, l, c = bar['open'], bar['high'], bar['low'], bar['close']
    rng = h - l
    if rng <= 0:
        return False
    body = abs(c - o)
    upper = h - max(o, c)
    lower = min(o, c) - l
    if body / rng > 0.35:
        return False
    return lower >= body * 2 or upper >= body * 2


def is_doji(bar):
    """十字星：實體佔全幅 <= 8%"""
    o, h, l, c = bar['open'], bar['high'], bar['low'], bar['close']
    rng = h - l
    if rng <= 0:
        return False
    return abs(c - o) / rng <= 0.08


def is_bullish_engulfing(prev, cur):
    """看漲吞沒：前一天收黑，今天收紅且實體完全吞沒前一天實體"""
    prev_bear = prev['close'] < prev['open']
    cur_bull = cur['close'] > cur['open']
    if not (prev_bear and cur_bull):
        return False
    return cur['open'] <= prev['close'] and cur['close'] >= prev['open']


def has_long_lower_shadow(bar):
    """長下影線：下影線長度 >= 實體1.5倍，且下影線佔全幅 >= 40%"""
    o, h, l, c = bar['open'], bar['high'], bar['low'], bar['close']
    rng = h - l
    if rng <= 0:
        return False
    body = abs(c - o)
    lower = min(o, c) - l
    return lower >= body * 1.5 and lower / rng >= 0.4


def bottoming_pattern(bars, idx):
    """idx這根K棒是否為止跌形態（錘子/十字星/吞沒/長下影線任一）"""
    bar = bars[idx]
    if is_hammer(bar) or is_doji(bar) or has_long_lower_shadow(bar):
        return True
    if idx > 0 and is_bullish_engulfing(bars[idx - 1], bar):
        return True
    return False


def volume_confirmed(bars, idx):
    """idx這根K棒的量是否 >= 前5日均量"""
    if idx < 5:
        return False
    vol = bars[idx]['volume']
    avg5 = sum(b['volume'] for b in bars[idx - 5:idx]) / 5
    return avg5 > 0 and vol >= avg5
```

- [ ] **Step 2: 手動驗證合理性**

Run:
```bash
cd ~/CCProject/command-center && python3 -c "
import support
bar_hammer = {'open':100,'high':102,'low':90,'close':101,'volume':1000}
bar_normal = {'open':100,'high':105,'low':98,'close':104,'volume':1000}
print('hammer test (應True):', support.is_hammer(bar_hammer))
print('normal test (應False):', support.is_hammer(bar_normal))
print('doji test (應True):', support.is_doji({'open':100,'high':103,'low':97,'close':100.2,'volume':1000}))
"
```
Expected: 三行都印出符合註解的布林值（True/False/True）。

- [ ] **Step 3: Commit**

```bash
cd ~/CCProject && git add command-center/support.py
git commit -m "feat: 支撐位偵測 — K線形態與成交量輔助函式"
```

---

## Task 2: support.py — 波段低點水平支撐 + 整數關卡

**Files:**
- Modify: `command-center/support.py`

**Interfaces:**
- Consumes: `bottoming_pattern(bars, idx)`、`volume_confirmed(bars, idx)`（Task 1）
- Produces: `find_swing_lows(bars, window=5) -> list[int]`（回傳index列表）、`swing_low_support(bars) -> list[dict]`、`round_number_support(bars, current_price) -> list[dict]`。回傳的每個 dict 是 `{price:float, strength:int, sources:list[str]}`。

- [ ] **Step 1: 新增波段低點與整數關卡函式**

```python
def find_swing_lows(bars, window=5):
    """找局部低點：前後各window天內，該天low為最低"""
    lows = []
    for i in range(window, len(bars) - window):
        lo = bars[i]['low']
        neighborhood = bars[i - window:i + window + 1]
        if lo == min(b['low'] for b in neighborhood):
            lows.append(i)
    return lows


def swing_low_support(bars):
    """訊號1：波段低點水平支撐 — 相近低點(±2%)分群，出現≥2次才算有效支撐"""
    idxs = find_swing_lows(bars)
    if not idxs:
        return []
    groups = []
    for i in idxs:
        price = bars[i]['low']
        placed = False
        for g in groups:
            gprice = sum(g['prices']) / len(g['prices'])
            if abs(price - gprice) / gprice <= 0.02:
                g['prices'].append(price)
                g['idxs'].append(i)
                placed = True
                break
        if not placed:
            groups.append({'prices': [price], 'idxs': [i]})
    levels = []
    for g in groups:
        if len(g['idxs']) < 2:
            continue
        avg_price = sum(g['prices']) / len(g['prices'])
        strength = 1 + len(g['idxs'])
        sources = [f"波段低點×{len(g['idxs'])}次"]
        for i in g['idxs']:
            if bottoming_pattern(bars, i):
                strength += 1
                sources.append(f"{bars[i]['date']}止跌K線")
            if volume_confirmed(bars, i):
                strength += 1
                sources.append(f"{bars[i]['date']}量能放大")
        levels.append({'price': round(avg_price, 2), 'strength': min(strength, 5), 'sources': sources})
    return levels


def round_number_support(bars, current_price):
    """訊號4：整數關卡 — 依現價量級自動選單位，找現價下方最近的1-2個整數價位"""
    if current_price >= 1000:
        unit = 100
    elif current_price >= 100:
        unit = 50
    elif current_price >= 20:
        unit = 10
    else:
        unit = 5
    lower = (int(current_price) // unit) * unit
    candidates = [lower] if lower < current_price else []
    candidates.append(lower - unit)
    levels = []
    for level_price in candidates:
        if level_price <= 0:
            continue
        strength = 1
        sources = [f"整數關卡{level_price}"]
        for b in bars:
            if abs(b['low'] - level_price) / level_price <= 0.015 and has_long_lower_shadow(b):
                strength += 1
                sources.append(f"{b['date']}長下影線")
        levels.append({'price': float(level_price), 'strength': min(strength, 5), 'sources': sources})
    return levels
```

- [ ] **Step 2: 手動驗證合理性**

用真實股票（大立光3008）的日K跑一次，核對輸出的價位是否落在合理範圍（現價附近，不是天文數字或負數）：

```bash
cd ~/CCProject/command-center && python3 -c "
import json, urllib.request, support
d = json.loads(urllib.request.urlopen('http://localhost:5455/daily_ohlcv?code=3008&days=250').read())
bars = d['bars']
print('swing_low_support:')
for lv in support.swing_low_support(bars): print(' ', lv)
print('round_number_support (現價', bars[-1]['close'], '):')
for lv in support.round_number_support(bars, bars[-1]['close']): print(' ', lv)
"
```
Expected: 印出的支撐價位都落在近期股價波動範圍內（不會是離現價很遠的異常值），`sources` 內容有意義。

- [ ] **Step 3: Commit**

```bash
cd ~/CCProject && git add command-center/support.py
git commit -m "feat: 支撐位偵測 — 波段低點與整數關卡訊號"
```

---

## Task 3: support.py — 趨勢線支撐 + 均線支撐 + 主整合函式

**Files:**
- Modify: `command-center/support.py`

**Interfaces:**
- Consumes: `find_swing_lows(bars)`、`volume_confirmed(bars, idx)`、`bottoming_pattern(bars, idx)`、`swing_low_support(bars)`、`round_number_support(bars, current_price)`（Task 1-2）
- Produces: `trendline_support(bars) -> list[dict]`、`ma_support(bars) -> list[dict]`、`analyze_support(bars) -> list[dict]`（最終給 sources.py 呼叫的主函式，依price由高到低排序）

- [ ] **Step 1: 新增趨勢線、均線、主整合函式**

```python
def trendline_support(bars):
    """訊號2：上升趨勢線支撐 — 取最近2~3個波段低點做線性回歸，斜率須為正才算上升趨勢；
    若近3天收盤明顯跌破線且量能配合，視為失效不列入"""
    idxs = find_swing_lows(bars)
    if len(idxs) < 2:
        return []
    recent = idxs[-3:] if len(idxs) >= 3 else idxs[-2:]
    xs, ys = recent, [bars[i]['low'] for i in recent]
    n = len(xs)
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    denom = sum((x - mean_x) ** 2 for x in xs)
    if denom == 0:
        return []
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denom
    if slope <= 0:
        return []
    intercept = mean_y - slope * mean_x
    last_idx = len(bars) - 1
    line_price = slope * last_idx + intercept
    current_close = bars[-1]['close']
    for i in range(max(0, last_idx - 2), last_idx + 1):
        line_at_i = slope * i + intercept
        if bars[i]['close'] < line_at_i * 0.995 and volume_confirmed(bars, i):
            return []
    if line_price >= current_close:
        return []
    return [{
        'price': round(line_price, 2), 'strength': 3,
        'sources': [f"上升趨勢線(連接{bars[recent[0]]['date']}~{bars[recent[-1]]['date']}低點)"],
    }]


def _ma_series(bars, n):
    if len(bars) < n:
        return None
    return [sum(b['close'] for b in bars[i - n + 1:i + 1]) / n for i in range(n - 1, len(bars))]


def ma_support(bars):
    """訊號3：均線動態支撐 MA20/60/120/250 — 均線本身須向上，現價須在均線上方才算有效支撐"""
    current_close = bars[-1]['close']
    levels = []
    for n in (20, 60, 120, 250):
        if len(bars) < n + 5:
            continue
        ma_series = _ma_series(bars, n)
        ma_now, ma_5ago = ma_series[-1], ma_series[-6] if len(ma_series) >= 6 else ma_series[0]
        if ma_now <= ma_5ago or current_close < ma_now:
            continue
        strength = 2
        sources = [f"MA{n}向上"]
        offset = len(bars) - len(ma_series)
        for i in range(max(0, len(bars) - 20), len(bars)):
            j = i - offset
            if 0 <= j < len(ma_series):
                ma_i = ma_series[j]
                if abs(bars[i]['low'] - ma_i) / ma_i <= 0.015 and bottoming_pattern(bars, i):
                    strength += 1
                    sources.append(f"{bars[i]['date']}止跌於MA{n}")
        levels.append({'price': round(ma_now, 2), 'strength': min(strength, 5), 'sources': sources})
    return levels


def analyze_support(bars):
    """整合6種訊號，合併相近價位(±1.5%)，回傳依price由高到低排序的支撐位列表"""
    if len(bars) < 30:
        return []
    all_levels = (swing_low_support(bars) + trendline_support(bars)
                  + ma_support(bars) + round_number_support(bars, bars[-1]['close']))
    merged = []
    for lv in sorted(all_levels, key=lambda x: x['price'], reverse=True):
        placed = False
        for m in merged:
            if abs(lv['price'] - m['price']) / m['price'] <= 0.015:
                m['strength'] = min(5, m['strength'] + 1)
                m['sources'] += lv['sources']
                m['price'] = round((m['price'] + lv['price']) / 2, 2)
                placed = True
                break
        if not placed:
            merged.append(dict(lv))
    merged.sort(key=lambda x: x['price'], reverse=True)
    return merged
```

- [ ] **Step 2: 手動驗證合理性**

```bash
cd ~/CCProject/command-center && python3 -c "
import json, urllib.request, support
for code in ('3008', '3034', '2637'):
    d = json.loads(urllib.request.urlopen(f'http://localhost:5455/daily_ohlcv?code={code}&days=250').read())
    bars = d['bars']
    if bars and bars[-1]['date'] == __import__('datetime').datetime.now().strftime('%Y-%m-%d'):
        bars = bars[:-1]
    print(f'--- {code} 現價 {bars[-1][\"close\"]} ---')
    for lv in support.analyze_support(bars):
        print(' ', lv['price'], '★'*lv['strength'], lv['sources'])
"
```
Expected: 每檔都印出至少1個支撐位，價位分布在現價附近或以下，strength在1-5之間，sources有具體內容。人工核對這些價位是否貼近該股實際圖表上看得出來的支撐區（可搭配 `curl http://localhost:5455/daily_ohlcv?code=3008\&days=30` 肉眼比對近期低點）。

- [ ] **Step 3: Commit**

```bash
cd ~/CCProject && git add command-center/support.py
git commit -m "feat: 支撐位偵測 — 趨勢線、均線訊號與主整合函式"
```

---

## Task 4: sources.py `/api/support` 端點

**Files:**
- Modify: `command-center/sources.py`
- Modify: `command-center/app.py`

**Interfaces:**
- Consumes: `support.analyze_support(bars)`（Task 3）、既有 `_proxy()`、`@_wrap`（sources.py 既有）
- Produces: `sources.support(code: str) -> {ok, data:{code,levels}, updated}`（給 app.py 呼叫）

- [ ] **Step 1: sources.py 新增 support() function**

在 `command-center/sources.py` 的 `_price_streak()` 函式後面（約第188行之後）新增：

```python
import support as support_mod

_support_cache: dict = {}


def _support_levels(code: str):
    """算支撐位，5分鐘記憶體快取（避免同一股票被多張卡片重複打 gateway）"""
    now = time.time()
    hit = _support_cache.get(code)
    if hit and now - hit[0] < 300:
        return hit[1]
    d = _proxy(f'http://localhost:5455/daily_ohlcv?code={code}&days=250', ttl=3600)
    bars = d.get('bars') or []
    today_str = datetime.now().strftime('%Y-%m-%d')
    if bars and bars[-1].get('date') == today_str:
        bars = bars[:-1]
    levels = support_mod.analyze_support(bars)
    _support_cache[code] = (now, levels)
    return levels


@_wrap
def support(code: str):
    levels = _support_levels(code)
    return {'code': code, 'levels': levels}, datetime.now().strftime('%Y-%m-%d %H:%M')
```

`import support as support_mod` 加到檔案最上方 import 區塊（第2-8行附近，跟其他 import 放一起）。

- [ ] **Step 2: app.py 新增路由**

在 `command-center/app.py` 的 `/api/stock/{symbol}` 路由（約第241行）後面新增：

```python
@app.get('/api/support/{symbol}')
def support_query(symbol: str):
    if not (symbol.isdigit() and 4 <= len(symbol) <= 6):
        raise HTTPException(400, 'invalid symbol')
    return sources.support(symbol)
```

- [ ] **Step 3: 驗證語法與重啟服務**

```bash
python3 -c "import ast; ast.parse(open('/Users/steven/CCProject/command-center/sources.py').read()); ast.parse(open('/Users/steven/CCProject/command-center/app.py').read()); print('ok')"
launchctl kickstart -k gui/$(id -u)/com.steven.command-center
sleep 3
curl -s http://localhost:5950/api/health
curl -s http://localhost:5950/api/support/3008 | python3 -m json.tool
```
Expected: health回傳ok，`/api/support/3008` 回傳 `{"ok":true,"data":{"code":"3008","levels":[...]},"updated":"..."}`。

- [ ] **Step 4: Commit**

```bash
cd ~/CCProject && git add command-center/sources.py command-center/app.py
git commit -m "feat: 新增 /api/support 支撐位查詢端點"
```

---

## Task 5: 前端 — 支撐位彈窗 UI

**Files:**
- Modify: `command-center/templates/index.html`

**Interfaces:**
- Consumes: `GET /api/support/{code}`（Task 4）、既有 `$()`, `el()`, `fmt()`, `esc()` helper（index.html既有）
- Produces: `supportBtn(code) -> string`（HTML片段，插入既有pill/row模板字串中）、`openSupportModal(code)`、`closeSupportModal()`（全域函式）

- [ ] **Step 1: 新增 modal HTML（放在 `<div class="toast" id="toast"></div>` 之後，`<script>` 標籤之前）**

```html
<div class="modal-overlay" id="support-modal-overlay" style="display:none" onclick="if(event.target===this)closeSupportModal()">
  <div class="modal-box">
    <div class="modal-head"><span id="support-modal-title"></span><span class="modal-close" onclick="closeSupportModal()">✕</span></div>
    <div class="modal-body" id="support-modal-body"></div>
  </div>
</div>
```

- [ ] **Step 2: 新增 modal CSS（加到 `<style>` 區塊末尾）**

```css
.modal-overlay{position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:999;display:flex;align-items:center;justify-content:center}
.modal-box{background:var(--card);border:1px solid var(--border);border-radius:var(--radius,10px);max-width:400px;width:90%;max-height:70vh;overflow-y:auto;padding:16px}
.modal-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;font-weight:600}
.modal-close{cursor:pointer;color:var(--muted)}
.support-level{padding:8px 0;border-bottom:1px solid var(--border-soft)}
.support-level:last-child{border-bottom:none}
.support-price{font-family:var(--mono);font-size:14px;color:var(--fg)}
.support-src{font-size:11px;color:var(--muted);margin-top:3px}
```

- [ ] **Step 3: 新增 JS 函式（加在 `streakTxt()` 函式後面，約第373行）**

```js
function supportBtn(code){
  if(!code)return '';
  return ` <span style="cursor:pointer;font-size:11px" onclick="openSupportModal('${code}')" title="查看支撐位">📊</span>`;
}
function openSupportModal(code){
  const overlay=$('#support-modal-overlay');
  $('#support-modal-title').textContent=code+' 支撐位分析';
  $('#support-modal-body').innerHTML='<div class="empty">載入中...</div>';
  overlay.style.display='flex';
  fetch('/api/support/'+code).then(r=>r.json()).then(res=>{
    if(!res.ok){$('#support-modal-body').innerHTML=`<div class="empty">資料不足，無法分析（${esc(res.error||'')}）</div>`;return}
    const levels=(res.data&&res.data.levels)||[];
    if(!levels.length){$('#support-modal-body').innerHTML='<div class="empty">未偵測到明顯支撐位</div>';return}
    $('#support-modal-body').innerHTML=levels.map(lv=>{
      const stars='★'.repeat(lv.strength)+'☆'.repeat(5-lv.strength);
      const srcLines=(lv.sources||[]).map(s=>`<div>・${esc(s)}</div>`).join('');
      return `<div class="support-level"><div class="support-price">${fmt(lv.price)} <span style="color:var(--accent)">${stars}</span></div><div class="support-src">${srcLines}</div></div>`;
    }).join('');
  }).catch(()=>{$('#support-modal-body').innerHTML='<div class="empty">讀取失敗</div>'});
}
function closeSupportModal(){$('#support-modal-overlay').style.display='none'}
```

- [ ] **Step 4: 掛到 renderSwing — 修改 pill 組裝那行**

找到（約第431行）：
```js
        b.innerHTML=tkLink(r.name,r.code,' style="font-size:12px"')+priceTxt+scoreTxt+streakBadge+buyBadge;
```
改成：
```js
        b.innerHTML=tkLink(r.name,r.code,' style="font-size:12px"')+priceTxt+scoreTxt+streakBadge+buyBadge+supportBtn(r.code);
```

- [ ] **Step 5: 掛到 renderDaytrade — 修改 pill 組裝那行**

找到（約第471行）：
```js
        b.innerHTML=tkLink(name,code,' style="font-size:12px"')+priceTxt+scoreTxt+streakBadge;
```
改成：
```js
        b.innerHTML=tkLink(name,code,' style="font-size:12px"')+priceTxt+scoreTxt+streakBadge+supportBtn(code);
```

- [ ] **Step 6: 掛到 renderMA — 修改 row 組裝那行**

找到（約第618行）：
```js
    row.innerHTML=`${tkLink(m.name||code,code)}<span class="mini" style="margin-left:6px">${type}</span><span class="spacer"></span><span class="chg ${above?'up':'down'}" style="font-size:11px">${above?'▲上':'▼下'} ${fmt(m.price)}</span><span class="mini" style="margin-left:8px">${m.at}</span>`;
```
改成：
```js
    row.innerHTML=`${tkLink(m.name||code,code)}${supportBtn(code)}<span class="mini" style="margin-left:6px">${type}</span><span class="spacer"></span><span class="chg ${above?'up':'down'}" style="font-size:11px">${above?'▲上':'▼下'} ${fmt(m.price)}</span><span class="mini" style="margin-left:8px">${m.at}</span>`;
```

- [ ] **Step 7: 重啟服務並用 Playwright 驗證**

```bash
launchctl kickstart -k gui/$(id -u)/com.steven.command-center
sleep 3
curl -s http://localhost:5950/ -o /dev/null -w "%{http_code}\n"
```
Expected: 200。接著用 Playwright 開 `http://localhost:5950/`，點隔日沖候選卡片裡任一檔的 📊 圖示，確認彈窗跳出並顯示支撐位列表，點 ✕ 或彈窗外圍能關閉。

- [ ] **Step 8: Commit**

```bash
cd ~/CCProject && git add command-center/templates/index.html
git commit -m "feat: 新增支撐位彈窗 UI，掛到隔日沖/當沖/均線警報卡片"
```

---

## Task 6: pullback-buy-screener 每日自動掃描 + LaunchAgent

**Files:**
- Create: `pullback-buy-screener/daily_scan.py`
- Create: `~/Library/LaunchAgents/com.steven.pullback-daily-scan.plist`

**Interfaces:**
- Consumes: `screener.scan(n_universe) -> list[dict]`（既有，`pullback-buy-screener/screener.py`）
- Produces: `/tmp/pullback_candidates.json`（格式 `{date, results:[...]}`，比照 `/tmp/swing_candidates.json`）

- [ ] **Step 1: 建立 daily_scan.py**

```python
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
```

- [ ] **Step 2: 手動跑一次驗證**

```bash
cd ~/CCProject/pullback-buy-screener && python3 daily_scan.py
python3 -c "import json; d=json.load(open('/tmp/pullback_candidates.json')); print(d['date'], len(d['results']), '檔'); print(d['results'][0] if d['results'] else '無候選')"
```
Expected: 印出日期時間、候選檔數（可能是0，取決於當天市況），若有候選印出第一筆的欄位（code/name/close/change_pct等）。

- [ ] **Step 3: 建立 LaunchAgent plist**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.steven.pullback-daily-scan</string>
    <key>ProgramArguments</key>
    <array>
        <string>/opt/homebrew/bin/python3</string>
        <string>/Users/steven/CCProject/pullback-buy-screener/daily_scan.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/Users/steven/CCProject/pullback-buy-screener</string>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>14</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>/Users/steven/CCProject/logs/pullback_daily_scan.out</string>
    <key>StandardErrorPath</key>
    <string>/Users/steven/CCProject/logs/pullback_daily_scan.err</string>
    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
```

存到 `~/Library/LaunchAgents/com.steven.pullback-daily-scan.plist`（跟 `com.steven.gdrive-sort.plist` 同樣不限定星期，非交易日重跑一次無害，TWSE API當天無資料就是空結果）。

- [ ] **Step 4: 載入並驗證 LaunchAgent**

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.steven.pullback-daily-scan.plist
launchctl kickstart -k gui/$(id -u)/com.steven.pullback-daily-scan
sleep 5
cat /Users/steven/CCProject/logs/pullback_daily_scan.out
```
Expected: log 顯示「掃描完成」訊息（跟 Step 2 手動跑的結果一致或更新）。

- [ ] **Step 5: Commit**

```bash
cd ~/CCProject && git add pullback-buy-screener/daily_scan.py
git commit -m "feat: 回後買選股新增每日自動掃描腳本"
```
（`~/Library/LaunchAgents/` 不在 git repo 內，不用 commit，只需部署。）

---

## Task 7: command-center 新增「回後買候選」卡片

**Files:**
- Modify: `command-center/sources.py`
- Modify: `command-center/templates/index.html`

**Interfaces:**
- Consumes: `/tmp/pullback_candidates.json`（Task 6）、既有 `_stock_sector()`, `_price_streak()`, `_wrap`（sources.py既有）
- Produces: `sources.pullback() -> {ok, data:{date,results}, updated}`、註冊進 `SIGNALS` dict 供 `/api/signals/pullback` 存取

- [ ] **Step 1: sources.py 新增 pullback() function**

在 `swing()` function 後面（約第259行之後）新增：

```python
@_wrap
def pullback():
    p = '/tmp/pullback_candidates.json'
    d = _read_json(p)
    results = d.get('results', [])
    for r in results:
        r['sector'] = _stock_sector(r.get('code', ''))
        r['streak'] = _price_streak(r.get('code', ''))
    return d, d.get('date', _mtime(p))
```

- [ ] **Step 2: 註冊進 SIGNALS dict**

找到（約第635-639行）：
```python
SIGNALS = {
    'daytrade': daytrade, 'swing': swing, 'intraday': intraday, 'ma': ma,
    'chips': chips, 'news': news, 'market-fear': market_fear,
    'disposition': disposition,
}
```
改成：
```python
SIGNALS = {
    'daytrade': daytrade, 'swing': swing, 'intraday': intraday, 'ma': ma,
    'chips': chips, 'news': news, 'market-fear': market_fear,
    'disposition': disposition, 'pullback': pullback,
}
```

- [ ] **Step 3: 前端新增卡片定義**

在 `command-center/templates/index.html` 的 SIGNALS 陣列（約第309-316行）找到：
```js
  {id:'swing',   ic:'🌙', title:'隔日沖候選', span:'', url:'/api/signals/swing', render:renderSwing, site:'/svc/5500/'},
```
在它後面新增一行：
```js
  {id:'pullback',ic:'📈', title:'回後買候選', span:'', url:'/api/signals/pullback', render:renderPullback, site:'/svc/5960/'},
```

- [ ] **Step 4: 新增 renderPullback 函式**

在 `renderSwing()` 函式後面（約第441行之後）新增：

```js
function renderPullback(d){
  const rs=(d&&d.results)||[];
  const box=el('div');
  if(!rs.length){
    box.appendChild(el('div','empty','今日無符合回後買條件的候選'));
  } else {
    const groups=groupBySector(rs,r=>r.code,r=>r.sector);
    groups.forEach((rows,sector)=>{
      const head=el('div','mini','🏷️ '+sector);
      head.style.cssText='margin-top:8px;color:var(--text-dim);font-size:11px';
      box.appendChild(head);
      const pills=el('div');
      pills.style.cssText='display:flex;flex-wrap:wrap;gap:7px';
      sortCandidates(rows).forEach(r=>{
        const priceTxt=r.close!=null?` <span style="font-family:var(--mono);color:var(--text-dim)">${fmt(r.close)}</span>`:'';
        const chgTxt=r.change_pct!=null?` <span class="chg ${chgCls(r.change_pct)}" style="font-size:11px">${arrow(r.change_pct)}${fmt(Math.abs(r.change_pct))}%</span>`:'';
        const streakBadge=streakTxt(r)?' '+streakTxt(r):'';
        const b=el('span','pill');
        b.innerHTML=tkLink(r.name,r.code,' style="font-size:12px"')+priceTxt+chgTxt+streakBadge+supportBtn(r.code);
        pills.appendChild(b);
      });
      box.appendChild(pills);
    });
  }
  return box;
}
```

- [ ] **Step 5: 驗證**

```bash
python3 -c "import ast; ast.parse(open('/Users/steven/CCProject/command-center/sources.py').read()); print('ok')"
launchctl kickstart -k gui/$(id -u)/com.steven.command-center
sleep 3
curl -s http://localhost:5950/api/signals/pullback | python3 -m json.tool
```
Expected: 回傳 `{"ok":true,"data":{"date":...,"results":[...]},"updated":...}`，跟 `/tmp/pullback_candidates.json` 內容一致（含補上的 sector/streak）。接著用 Playwright 開 command-center 首頁，確認「回後買候選」卡片出現、股票點📊能彈出支撐位。

- [ ] **Step 6: Commit**

```bash
cd ~/CCProject && git add command-center/sources.py command-center/templates/index.html
git commit -m "feat: command-center 新增回後買候選卡片"
```

---

## Task 8: 整合驗證

**Files:** 無新檔案，純驗證既有整合結果

- [ ] **Step 1: 全端點健康檢查**

```bash
curl -s http://localhost:5950/api/health
curl -s http://localhost:5950/api/signals/swing -o /dev/null -w "swing: %{http_code}\n"
curl -s http://localhost:5950/api/signals/daytrade -o /dev/null -w "daytrade: %{http_code}\n"
curl -s http://localhost:5950/api/signals/pullback -o /dev/null -w "pullback: %{http_code}\n"
curl -s http://localhost:5950/api/signals/ma -o /dev/null -w "ma: %{http_code}\n"
curl -s http://localhost:5950/api/support/3008 -o /dev/null -w "support: %{http_code}\n"
```
Expected: 全部 200。

- [ ] **Step 2: 用 Playwright 走一次完整流程**

開 `http://localhost:5950/`，依序：
1. 確認隔日沖候選、當沖候選、回後買候選、均線警報 四張卡片都有資料或正確顯示「無候選」空狀態。
2. 點隔日沖候選裡任一檔的 📊，確認彈窗顯示支撐位列表（價位+星等+來源明細），點 ✕ 關閉。
3. 點均線警報卡片裡任一檔的 📊，同樣驗證彈窗正常。
4. 手動改網址測試資料不足情境：`curl http://localhost:5950/api/support/0000`（不存在的代碼），確認回傳 `{"ok":false,...}` 且不會導致其他 API 或頁面掛掉。

- [ ] **Step 3: 確認 pullback LaunchAgent 明天會自動觸發**

```bash
launchctl list | grep pullback-daily-scan
```
Expected: 有這個 label 且 PID/status 正常（0 或有效 PID，不是異常錯誤碼）。

- [ ] **Step 4: 最終 commit（若前面步驟有微調）**

```bash
cd ~/CCProject && git status --short
# 若有殘留未commit的修正，補一個commit
git add -A -- command-center pullback-buy-screener
git commit -m "chore: 支撐位偵測功能整合驗證微調"
```
（若 Step 1-3 都通過且無需修正，此步驟可跳過。）

---

## Self-Review 摘要

- **Spec coverage**：spec 的 6 種訊號（Task 1-3）、`/api/support` 端點（Task 4）、彈窗UI（Task 5）、pullback整合+LaunchAgent（Task 6）、pullback卡片（Task 7）、錯誤處理（Task 4的資料不足回傳ok:false + Task 5彈窗顯示錯誤訊息 + Task 6的daily_scan失敗保留舊檔）、測試方式（Task 1-3人工核對、Task 6 log驗證、Task 8 curl+Playwright）都有對應任務覆蓋。
- **Placeholder scan**：所有 Step 均含完整可執行程式碼，無 TBD。
- **Type consistency**：`analyze_support(bars)` 回傳的 `{price,strength,sources}` 格式在 Task 3/4/5 一致；`sources.support(code)` 回傳的 `{code,levels}` 格式在 Task 4/5 一致；`daily_scan.py` 寫出的 `{date,results}` 格式跟 Task 7 的 `pullback()` 讀取格式一致。
