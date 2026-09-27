"""大眾運輸路線查詢 — 從住家（或指定出發地）到任意台灣地址

資料源：Transitous（社群 MOTIS 引擎，免金鑰）為主，台鐵官方 PTX 班表為備援。
地址定位：只用 Nominatim（細節見下方「地址定位」段落的實測註解）。

回傳格式沿用儀表板慣例：{'ok', 'data'|'error', 'updated'}
"""
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

TPE = timezone(timedelta(hours=8))

HOME_ADDR = '新北市樹林區復興路295巷13號'
HOME_COORD = (24.988665, 121.419166)  # OSM 門牌點位

TRANSITOUS = 'https://api.transitous.org/api/v1/plan'
NOMINATIM = 'https://nominatim.openstreetmap.org/search'
OSRM_FOOT = 'https://routing.openstreetmap.de/routed-foot/route/v1/foot/{a};{b}'
PTX_STATIONS = 'https://ptx.transportdata.tw/MOTC/v2/Rail/TRA/Station?$format=JSON'
PTX_TIMETABLE = ('https://ptx.transportdata.tw/MOTC/v2/Rail/TRA/'
                 'DailyTimetable/OD/{o}/to/{d}/{day}?$format=JSON')

UA = 'command-center-route-card/1.0 (personal use)'

# LIGHT_RAIL 不在 MOTIS 合法清單內，帶了會回 400（實測）
TRANSIT_MODES = ['HIGHSPEED_RAIL', 'LONG_DISTANCE', 'REGIONAL_RAIL', 'SUBURBAN',
                 'SUBWAY', 'TRAM', 'BUS', 'COACH', 'FERRY', 'FUNICULAR', 'AERIAL_LIFT']

MODE_LABEL = {
    'WALK': '步行', 'BIKE': '自行車', 'CAR': '開車',
    'HIGHSPEED_RAIL': '高鐵', 'LONG_DISTANCE': '台鐵對號', 'REGIONAL_FAST_RAIL': '台鐵',
    'REGIONAL_RAIL': '台鐵區間', 'SUBURBAN': '台鐵', 'SUBWAY': '捷運', 'TRAM': '輕軌',
    'BUS': '公車', 'COACH': '客運', 'FERRY': '渡輪', 'FUNICULAR': '纜車',
    'AERIAL_LIFT': '纜車', 'GONDOLA': '纜車', 'TROLLEYBUS': '公車',
}

_MAX_ROUTES = 3


# ── HTTP ────────────────────────────────────────

def _get(url: str, timeout: int = 20, as_json: bool = True):
    req = urllib.request.Request(url, headers={
        'User-Agent': UA,
        'Accept': 'application/json, text/xml, */*',
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw) if as_json else raw


# ── 地址定位 ─────────────────────────────────────
#
# 只用 Nominatim。實測：OSM /map 對小 bbox 也會回 600KB+ 且 15 秒載不完（不符
# OSM 使用政策），Overpass 從本機被 406 擋掉、mirror 逾時，Photon 對台灣地址全空。
# Nominatim 其實有台灣門牌，但**查詢格式要是「號碼在前」**才命中：
#   452號 延平路 中壢區 桃園市 → 24.9583334,121.2239289（門牌級）
#   桃園市中壢區興南里延平路452號 → 0 筆
# 門牌覆蓋不完整，所以下面用多層退路，查不到就退到街道級並在 UI 標示。

_addr_cache: dict = {}
_last_nominatim = 0.0
_NOMINATIM_GAP = 1.1  # Nominatim 政策：每秒最多 1 次

_POI_CLASSES = {'office', 'amenity', 'shop', 'building', 'tourism', 'leisure',
                'healthcare', 'government', 'railway', 'public_transport'}

# 站牌、月台這類節點在 Nominatim 的 class 是 highway，光看 class 會被誤判成街道
_POI_TYPES = {('highway', 'bus_stop'), ('highway', 'platform'), ('highway', 'station'),
              ('railway', 'station'), ('railway', 'halt'), ('railway', 'tram_stop'),
              ('public_transport', 'platform'), ('public_transport', 'station')}


def _parse_addr(addr: str) -> dict:
    s = re.sub(r'\s+', '', addr)
    def grab(pat):
        m = re.search(pat, s)
        return m.group(1) if m else ''
    tail = re.split(r'[區鄉鎮市里]', s)[-1]
    street = re.match(r'([一-鿿]{1,8}?(?:路|街|大道)(?:[一二三四五六七八九十]+段)?)', tail)
    return {
        'house': grab(r'(\d+)(?:之\d+)?號'),
        'lane': grab(r'(\d+)巷'),
        'alley': grab(r'(\d+)弄'),
        'street': street.group(1) if street else '',
        # 排除 [市縣區鄉鎮里] 才不會把「桃園市中壢區」拆錯
        'district': grab(r'([^市縣區鄉鎮里]{1,3}[區鄉鎮])'),
        'city': grab(r'([^區鄉鎮里]{2,3}[市縣])'),
    }


def _nominatim(query: str) -> list:
    global _last_nominatim
    gap = time.time() - _last_nominatim
    if gap < _NOMINATIM_GAP:
        time.sleep(_NOMINATIM_GAP - gap)
    _last_nominatim = time.time()
    return _get(f'{NOMINATIM}?' + urllib.parse.urlencode(
        {'q': query, 'format': 'json', 'limit': 1, 'countrycodes': 'tw'}), timeout=20)


def _precision(hit: dict) -> str:
    disp = hit.get('display_name', '')
    if re.search(r'\d+號', disp):
        return 'house'
    if (hit.get('class'), hit.get('type')) in _POI_TYPES:
        return 'poi'
    return 'poi' if hit.get('class') in _POI_CLASSES else 'street'


def _hit_to_coord(key: str, hit: dict, q: str) -> dict:
    out = {'lat': round(float(hit['lat']), 6), 'lon': round(float(hit['lon']), 6),
           'precision': _precision(hit), 'matched': q}
    _addr_cache[key] = out
    return out


def geocode_tw_address(addr: str) -> dict:
    """台灣地址或地標 → {lat, lon, precision}。precision: house | poi | street"""
    key = re.sub(r'\s+', '', addr)
    if key == re.sub(r'\s+', '', HOME_ADDR):
        return {'lat': HOME_COORD[0], 'lon': HOME_COORD[1], 'precision': 'house'}
    if key in _addr_cache:
        return _addr_cache[key]

    p = _parse_addr(key)
    sig = p['street'] or key  # 回傳結果一定要含這串，否則就是模糊比對到別條路
    tries = []
    if p['house'] and p['street']:
        road = p['street'] + (f"{p['lane']}巷" if p['lane'] else '') \
                           + (f"{p['alley']}弄" if p['alley'] else '')
        tries.append(f"{p['house']}號 {road} {p['district']} {p['city']}".strip())
        tries.append(f'{road} {p["district"]} {p["city"]}'.strip())
    tries.append(key)
    if p['street'] and (p['district'] or p['city']):
        # 只留路名會撈到全台同名道路，所以沒有區／市時不做這一層退路
        tries.append(f"{p['street']} {p['district']} {p['city']}".strip())

    weak = None
    for q in dict.fromkeys(t for t in tries if t):
        for hit in _nominatim(q)[:1]:
            disp = hit.get('display_name', '')
            if sig and sig not in disp:
                continue
            if p['house'] and re.search(rf"{p['house']}(?:之\d+)?號", disp):
                return _hit_to_coord(key, hit, q)  # 命中門牌
            weak = weak or (hit, q)
    if weak:
        return _hit_to_coord(key, weak[0], weak[1])
    raise ValueError(f'找不到這個地址，換個寫法或直接打地標名稱：{addr}')


# ── 時間工具 ─────────────────────────────────────

def _hm(iso: str) -> str:
    if not iso:
        return ''
    try:
        return datetime.fromisoformat(iso.replace('Z', '+00:00')).astimezone(TPE).strftime('%H:%M')
    except ValueError:
        return ''


def _hmm(iso: str) -> int:
    """當日 00:00 起算的分鐘數（台北時區）"""
    try:
        dt = datetime.fromisoformat(iso.replace('Z', '+00:00')).astimezone(TPE)
        return dt.hour * 60 + dt.minute
    except (ValueError, AttributeError):
        return 0


def _iso_time(t: str) -> str:
    m = re.fullmatch(r'(\d{1,2}):(\d{2})', t.strip())
    if not m:
        raise ValueError(f'抵達時間格式應為 HH:MM，收到「{t}」')
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 23 or mi > 59:
        raise ValueError(f'抵達時間不合法：{t}')
    return f'{h:02d}:{mi:02d}'


def _iso_day(day: str | None, arrive: str) -> str:
    if day and day.strip():
        try:
            return datetime.strptime(day.strip(), '%Y-%m-%d').strftime('%Y-%m-%d')
        except ValueError:
            raise ValueError(f'日期不合法，格式應為 YYYY-MM-DD，收到「{day}」') from None
    now = datetime.now(TPE)
    if arrive <= now.strftime('%H:%M'):  # 指定時刻今天已過 → 明天
        now += timedelta(days=1)
    return now.strftime('%Y-%m-%d')


# ── Transitous（主要）────────────────────────────

_TRAIN_NO = re.compile(r'tdx_(?:TRA|THSR)_([0-9A-Za-z]+?)_')


def _line_label(leg: dict) -> str:
    m = _TRAIN_NO.search(leg.get('routeId') or '')
    if m:
        return m.group(1)
    return leg.get('routeShortName') or leg.get('displayName') or ''


_NAME_FIX = {'START': '出發地', 'END': '目的地'}


def _name(end: dict) -> str:
    n = (end or {}).get('name') or ''
    return _NAME_FIX.get(n, n)


def _norm_itinerary(it: dict) -> dict:
    legs, walk = [], 0
    for lg in it.get('legs', []):
        mode = lg.get('mode', '')
        dur = lg.get('duration') or 0
        if mode == 'WALK':
            walk += dur
        legs.append({
            'mode': MODE_LABEL.get(mode, mode),
            'line': _line_label(lg),
            'from': _name(lg.get('from')),
            'to': _name(lg.get('to')),
            'start': _hm(lg.get('startTime')),
            'end': _hm(lg.get('endTime')),
            'min': max(1, round(dur / 60)),
            'walk': mode == 'WALK',
        })
    start_min = _hmm(it.get('startTime'))
    end_min = _hmm(it.get('endTime'))
    if end_min < start_min:  # 跨午夜，否則深夜抵達會被誤判成「當天清晨到」
        end_min += 1440
    return {
        'start': _hm(it.get('startTime')),
        'end': _hm(it.get('endTime')),
        'start_min': start_min,
        'end_min': end_min,
        'min': round((it.get('duration') or 0) / 60),
        'walk_min': round(walk / 60),
        'transfers': it.get('transfers', 0),
        'legs': legs,
    }


def plan_via_transitous(o: tuple, d: tuple, arrive_iso: str) -> list:
    params = {
        'fromPlace': f'{o[0]},{o[1]}',
        'toPlace': f'{d[0]},{d[1]}',
        'time': arrive_iso,
        'arriveBy': 'true',
        'numItineraries': '8',
        'transitModes': ','.join(TRANSIT_MODES),
    }
    js = _get(f'{TRANSITOUS}?' + urllib.parse.urlencode(params), timeout=30)
    return [_norm_itinerary(it) for it in js.get('itineraries', [])]


def _rank(routes: list, target_min: int, now_min: int | None = None) -> tuple[list, bool]:
    """①不遲到且最接近目標時刻 ②步行最少 ③總時間最短

    回傳 (前 N 條, 是否趕得上)。只按「總時間最短」排會挑到很早就到、在目的地空等的
    班次（實測：目標 11:20 卻回 10:56 到），所以第一順位是「不遲到的最晚抵達」。

    now_min 給定時（查今天）會濾掉已經開走的班次——引擎的 arriveBy 不知道「現在」。
    """
    if now_min is not None:
        ahead = [r for r in routes if r['start_min'] >= now_min]
        routes = ahead or routes
    feasible = [r for r in routes if r['end_min'] <= target_min]
    if feasible:
        feasible.sort(key=lambda r: (-r['end_min'], r['walk_min'], r['min']))
        return feasible[:_MAX_ROUTES], True
    routes.sort(key=lambda r: (r['end_min'], r['walk_min']))
    return routes[:_MAX_ROUTES], False


# ── 台鐵備援（Transitous 失敗時）──────────────────

_stations: list = []


def _tra_stations() -> list:
    global _stations
    if not _stations:
        for s in _get(PTX_STATIONS, timeout=25):
            pos = s.get('StationPosition') or {}
            if pos.get('PositionLat') is None:
                continue
            _stations.append((s['StationID'], s['StationName']['Zh_tw'],
                              pos['PositionLat'], pos['PositionLon']))
    return _stations


def _nearest_station(coord: tuple):
    return min(_tra_stations(),
               key=lambda s: (s[2] - coord[0]) ** 2 + (s[3] - coord[1]) ** 2)


def _osrm_foot(a: tuple, b: tuple) -> int:
    js = _get(OSRM_FOOT.format(a=f'{a[1]},{a[0]}', b=f'{b[1]},{b[0]}'), timeout=25)
    return max(1, round(js['routes'][0]['duration'] / 60))


def _minutes(t: str) -> int:
    h, m = t.split(':')[:2]
    return int(h) * 60 + int(m)


def plan_via_tra_fallback(o: tuple, d: tuple, day: str, arrive: str,
                          now_min: int | None = None) -> list:
    so, sd = _nearest_station(o), _nearest_station(d)
    if so[0] == sd[0]:
        raise ValueError('起訖點太近，台鐵備援不適用')
    walk_o = _osrm_foot(o, (so[2], so[3]))
    walk_d = _osrm_foot((sd[2], sd[3]), d)
    target = _minutes(arrive)

    best = None
    for t in _get(PTX_TIMETABLE.format(o=so[0], d=sd[0], day=day), timeout=25):
        dep = t['OriginStopTime']['DepartureTime']
        arr = t['DestinationStopTime']['ArrivalTime']
        dep_min, arr_min = _minutes(dep), _minutes(arr)
        if arr_min < dep_min:  # 跨午夜班次，否則「00:20」會被當成 20 分而誤判趕得上
            arr_min += 1440
        if arr_min + walk_d > target:  # 含步行趕不上就跳過
            continue
        if now_min is not None and dep_min - walk_o < now_min:  # 已經開走
            continue
        if best is None or dep_min > best[0]:  # 取最晚一班（最大化賴床時間）
            best = (dep_min, arr_min, dep, arr, t['DailyTrainInfo'])
    if best is None:
        raise ValueError(f'{arrive} 前沒有可抵達的台鐵班次')
    dep_min, arr_min, dep, arr, info = best
    ride = arr_min - dep_min  # 用處理過跨午夜的 arr_min，否則夜車車程會算成負的
    leave = max(0, dep_min - walk_o)
    leave_hm = f'{leave // 60:02d}:{leave % 60:02d}'
    return [{
        'start': leave_hm, 'end': arr[:5], 'end_min': arr_min,
        'min': walk_o + ride + walk_d, 'walk_min': walk_o + walk_d, 'transfers': 0,
        'legs': [
            {'mode': '步行', 'line': '', 'from': '出發地', 'to': so[1],
             'start': leave_hm, 'end': dep[:5], 'min': walk_o, 'walk': True},
            {'mode': MODE_LABEL.get('REGIONAL_RAIL', '台鐵'), 'line': info['TrainNo'],
             'from': so[1], 'to': sd[1], 'start': dep[:5], 'end': arr[:5],
             'min': max(1, ride), 'walk': False},
            {'mode': '步行', 'line': '', 'from': sd[1], 'to': '目的地',
             'start': arr[:5], 'end': '', 'min': walk_d, 'walk': True},
        ],
    }]


# ── 主流程 ───────────────────────────────────────

def plan(origin: str = '', dest: str = '', arrive: str = '', day: str | None = None,
         buffer_min: int = 10) -> dict:
    try:
        o_addr = (origin or '').strip() or HOME_ADDR
        if not (dest or '').strip():
            raise ValueError('請輸入目的地地址')
        arrive = _iso_time(arrive)
        day = _iso_day(day, arrive)
        buffer_min = max(0, min(60, int(buffer_min)))
        target = max(0, _minutes(arrive) - buffer_min)  # 提早 buffer_min 分鐘當作實質目標
        target_hm = f'{target // 60:02d}:{target % 60:02d}'
        now = datetime.now(TPE)
        now_min = now.hour * 60 + now.minute if day == now.strftime('%Y-%m-%d') else None

        o_geo = geocode_tw_address(o_addr)
        d_geo = geocode_tw_address(dest.strip())
        o, d = (o_geo['lat'], o_geo['lon']), (d_geo['lat'], d_geo['lon'])

        notice = ''
        try:
            routes, feasible = _rank(
                plan_via_transitous(o, d, f'{day}T{target_hm}:00+08:00'), target, now_min)
            source = 'transitous'
        except Exception as e:
            routes, feasible = _rank(
                plan_via_tra_fallback(o, d, day, target_hm, now_min), target, now_min)
            source = 'tra-fallback'
            notice = f'路線引擎暫時無法使用（{type(e).__name__}），已降級為台鐵班表'

        if not routes:
            raise ValueError('這個時間點查不到路線，換個時間或地址試試')
        if not feasible:
            notice = (notice + '；' if notice else '') + f'⚠️ {target_hm} 前到不了，以下是最近的班次'
        elif now_min is not None and target <= now_min:
            notice = (notice + '；' if notice else '') + '⚠️ 這個時間今天已經過了，建議改選明天'

        return {'ok': True, 'updated': '', 'data': {
            'source': source, 'notice': notice, 'feasible': feasible,
            'arrive': f'{day} {arrive}', 'buffer_min': buffer_min, 'target': target_hm,
            'origin': {'addr': o_addr, **o_geo},
            'dest': {'addr': dest.strip(), **d_geo},
            'routes': routes,
        }}
    except Exception as e:
        return {'ok': False, 'error': f'{type(e).__name__}: {e}', 'updated': ''}


if __name__ == '__main__':
    import sys
    t0 = time.time()
    out = plan(*sys.argv[1:])
    print(json.dumps(out, ensure_ascii=False, indent=1))
    print(f'耗時 {time.time() - t0:.1f}s', file=sys.stderr)
