"""AI 指揮中心 — 統一入口儀表板（port 5950，對既有服務全唯讀）"""
import ipaddress
import re
import secrets

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi import Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
import uvicorn
import os
import json

import agent as agent_mod
import jobs as jobs_mod
import route_planner
import sources

# 卡片連結透過 /svc/<port>/... 走反向代理，讓外網（Cloudflare Tunnel）也連得到
# 各服務的本機頁面，不必各自對外開洞。僅白名單內的 port 可被代理。
#
# 5990（learn-system）刻意不在名單內：它的 POST /api/questions/<id>/answer 會把
# 請求內容當程式碼執行，而這裡會轉發所有方法（含 POST）。本服務綁 0.0.0.0 且
# BasicAuthMiddleware 對私有來源 IP 跳過驗證（見該類別說明），一旦可被代理，
# 內網任何一台機器都能送程式碼進來執行——它是這份名單裡唯一會執行呼叫者程式碼
# 的服務，其餘都是唯讀資訊卡。該服務只在本機 http://127.0.0.1:5990/ 提供；
# command-center 的學習系統卡片仍以伺服器端的 sources._proxy 讀它的
# /api/domains，不受影響（見 sources.py 的 learn_system）。
PROXY_PORTS = {5070, 5100, 5250, 5300, 5350, 5400, 5460, 5500, 5501,
               5650, 5750, 5800, 5810, 5850, 5905, 5910, 5960, 5970, 5980,
               7799, 8188}
_PROXY_HOP_HEADERS = {
    'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
    'te', 'trailers', 'transfer-encoding', 'upgrade', 'content-encoding', 'content-length',
}

app = FastAPI(title='command-center')
templates = Jinja2Templates(directory='templates')

# 卡片／區塊排序存後端檔案（原本在瀏覽器 localStorage，換裝置或清快取就還原）
_CARD_ORDER_FILE = f'{sources.CC}/command-center/card_order.json'

# 均線監控追蹤標的（與 scripts/ma_monitor.py 共用的設定檔；可用 /ma-watchlist 編輯）
_MA_WATCHLIST_FILE = f'{sources.CC}/config/ma_watchlist.json'

_AUTH_USER, _AUTH_PASS = open(
    f'{sources.CC}/.secrets/command_center_auth.txt', encoding='utf-8'
).read().strip().split(':', 1)

# 一鍵登入用 token（給手機／Telegram 內建瀏覽器用：那些環境不彈 Basic Auth 輸入框）。
# 檔案不存在時 _TOKEN 為空字串，該路徑自動停用，只剩密碼登入。
COOKIE_NAME = 'cc_token'
COOKIE_MAX_AGE = 365 * 24 * 3600
_TOKEN_FILE = f'{sources.CC}/.secrets/command_center_token.txt'
_TOKEN = open(_TOKEN_FILE, encoding='utf-8').read().strip() if os.path.exists(_TOKEN_FILE) else ''


class BasicAuthMiddleware(BaseHTTPMiddleware):
    """經 Cloudflare Tunnel 暴露到外網時的最低限度保護（外流網址也連不進去）。"""

    async def dispatch(self, request: Request, call_next):
        if request.url.path == '/api/health':
            return await call_next(request)
        # 內網直連（非經 Cloudflare Tunnel 轉發）免登入，Tunnel 對外流量仍要密碼
        if 'cf-connecting-ip' not in request.headers:
            try:
                if ipaddress.ip_address(request.client.host).is_private:
                    return await call_next(request)
            except ValueError:
                pass
        if _TOKEN:
            # ?k=<token> 一鍵登入：先種 cookie 再 302 導回不帶 token 的網址，
            # 避免 token 留在網址列，或經 Referer 洩漏給頁面載入的外部資源。
            # 比對 bytes：compare_digest 對含非 ASCII 的 str 會直接 TypeError（曾讓 ?k=中文 回 500）
            if secrets.compare_digest(request.query_params.get('k', '').encode(), _TOKEN.encode()):
                url = request.url.remove_query_params('k')
                resp = RedirectResponse(f"{url.path}?{url.query}" if url.query else url.path,
                                        status_code=302)
                resp.set_cookie(COOKIE_NAME, _TOKEN, max_age=COOKIE_MAX_AGE,
                                httponly=True, samesite='lax', secure=True)
                return resp
            if secrets.compare_digest(request.cookies.get(COOKIE_NAME, '').encode(), _TOKEN.encode()):
                return await call_next(request)
        creds = await HTTPBasic(auto_error=False)(request)
        if not (isinstance(creds, HTTPBasicCredentials)
                and secrets.compare_digest(creds.username, _AUTH_USER)
                and secrets.compare_digest(creds.password, _AUTH_PASS)):
            return Response(status_code=401, headers={'WWW-Authenticate': 'Basic'})
        return await call_next(request)


app.add_middleware(BasicAuthMiddleware)


async def _proxy_to(port: int, path: str, request: Request) -> Response:
    if port not in PROXY_PORTS:
        raise HTTPException(404, '未知的服務 port')
    url = f'http://127.0.0.1:{port}/{path}'
    headers = {k: v for k, v in request.headers.items() if k.lower() not in ('host', 'authorization')}
    body = await request.body()

    # 用 stream=True 邊收邊轉發，不整包緩衝在記憶體裡才回傳。
    # SSE（如 daily-stock-analysis 的 tasks/stream）等長連線用 client.request()
    # 整包等待會卡到逾時才吐資料，前端永遠看不到即時進度。
    client = httpx.AsyncClient(timeout=None, follow_redirects=False)
    try:
        req = client.build_request(
            request.method, url, params=request.query_params,
            headers=headers, content=body,
        )
        resp = await client.send(req, stream=True)
    except httpx.RequestError as e:
        await client.aclose()
        raise HTTPException(502, f'轉發到 :{port} 失敗：{e}')

    resp_headers = {k: v for k, v in resp.headers.items() if k.lower() not in _PROXY_HOP_HEADERS}
    location = resp_headers.get('location')
    if location:
        # 把後端服務自己的絕對網址改寫回 /svc/<port>/... ，避免瀏覽器直接連去 localhost（外網連不到）
        location = re.sub(rf'^https?://(127\.0\.0\.1|localhost):{port}', f'/svc/{port}', location)
        if location.startswith('/') and not location.startswith(f'/svc/{port}'):
            location = f'/svc/{port}{location}'
        resp_headers['location'] = location

    async def _body_iterator():
        try:
            async for chunk in resp.aiter_bytes():
                yield chunk
        finally:
            await resp.aclose()
            await client.aclose()

    return StreamingResponse(_body_iterator(), status_code=resp.status_code, headers=resp_headers)


@app.api_route('/svc/{port}/{path:path}', methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH'])
async def svc_proxy(port: int, path: str, request: Request):
    return await _proxy_to(port, path, request)


@app.api_route('/svc/{port}', methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH'])
async def svc_proxy_root(port: int, request: Request):
    return await _proxy_to(port, '', request)


@app.get('/api/health')
def health(request: Request):
    """外部監控（Cloudflare Tunnel）直接打這支，回自己的狀態。但被代理頁面裡的前端
    也會用相對路徑打 /api/health，這時 Referer 帶 /svc/<port>/，要轉給該服務自己的
    health，不然畫面會一直顯示 command-center 的假結果，誤判成服務異常。"""
    m = re.search(r'/svc/(\d+)(?:/|$|\?)', request.headers.get('referer', ''))
    if m and int(m.group(1)) in PROXY_PORTS:
        return RedirectResponse(f'/svc/{m.group(1)}/api/health', status_code=307)
    return {'status': 'ok', 'service': 'command-center'}


@app.get('/swing-history', response_class=HTMLResponse)
def swing_history(request: Request):
    """隔日沖候選歷史命中率複查頁。"""
    return templates.TemplateResponse(request, 'swing_history.html', sources.swing_history())


@app.get('/daytrade-history', response_class=HTMLResponse)
def daytrade_history(request: Request):
    """當沖候選歷史命中率複查頁。"""
    return templates.TemplateResponse(request, 'daytrade_history.html', sources.daytrade_history())


@app.get('/ma-watchlist', response_class=HTMLResponse)
def ma_watchlist_page(request: Request):
    """均線監控追蹤標的編輯頁。"""
    return templates.TemplateResponse(request, 'ma_watchlist.html', {})


@app.get('/api/stock-lookup')
def stock_lookup(q: str = ''):
    """代號／名稱模糊查詢，轉發 shioaji-gateway /stock_search（編輯追蹤清單用）。"""
    if not q.strip():
        return {'results': []}
    try:
        r = httpx.get('http://127.0.0.1:5455/stock_search', params={'q': q}, timeout=5)
        return r.json()
    except Exception:
        return {'results': []}


@app.get('/market-dashboard')
def market_dashboard():
    """市場恐慌儀表板靜態報告（每日 07:30 由 market-dashboard 服務產生）。"""
    path = f'{sources.CC}/market-dashboard/index.html'
    if not os.path.exists(path):
        raise HTTPException(404, '市場恐慌儀表板尚未產生')
    return FileResponse(path)


@app.get('/tools/lotto649')
def tool_lotto649():
    path = f'{sources.CC}/lotto649/index.html'
    if not os.path.exists(path):
        raise HTTPException(404, '找不到 lotto649/index.html')
    return FileResponse(path)


@app.get('/tools/dan-koe-restart')
def tool_dan_koe_restart():
    path = f'{sources.CC}/dan-koe-restart/index.html'
    if not os.path.exists(path):
        raise HTTPException(404, '找不到 dan-koe-restart/index.html')
    return FileResponse(path)


@app.get('/api/signals/{name}')
def signals(name: str):
    fn = sources.SIGNALS.get(name)
    if fn is None:
        raise HTTPException(404, f'unknown signal: {name}')
    return fn()


@app.get('/api/portfolio')
def portfolio():
    return sources.portfolio()


@app.get('/api/health-all')
def health_all():
    return sources.health_all()


@app.get('/api/life/skilltree')
def life_skilltree():
    return sources.skilltree()


@app.get('/api/life/sim-invest')
def life_sim_invest():
    return sources.sim_invest()


@app.get('/api/life/market-analysis')
def life_market_analysis():
    return sources.market_analysis()


@app.get('/api/life/daily-stock-analysis')
def life_daily_stock_analysis():
    return sources.daily_stock_analysis()


@app.get('/api/life/guru-tracker')
def life_guru_tracker():
    return sources.guru_tracker()


@app.get('/api/life/market-cycle')
def life_market_cycle():
    return sources.market_cycle()


@app.get('/api/life/learn-system')
def life_learn_system():
    return sources.learn_system()


@app.get('/api/life/orchestrator')
def life_orchestrator():
    return sources.orchestrator()


@app.get('/api/route/plan')
def route_plan(dest: str, arrive: str, origin: str = '', day: str = '', buffer: int = 10):
    """大眾運輸路線查詢（出發地預設住家）。同步查詢約 3-5 秒，故不進 60 秒輪詢。"""
    return route_planner.plan(origin=origin, dest=dest, arrive=arrive,
                              day=day or None, buffer_min=buffer)


@app.get('/api/jobs')
def jobs_list():
    return jobs_mod.list_jobs()


@app.post('/api/jobs/{jid}/run')
def jobs_run(jid: str):
    try:
        return jobs_mod.run(jid)
    except KeyError:
        raise HTTPException(404, f'unknown job: {jid}')


@app.get('/api/jobs/{jid}/status')
def jobs_status(jid: str):
    try:
        return jobs_mod.status(jid)
    except KeyError:
        raise HTTPException(404, f'unknown job: {jid}')


@app.get('/api/stock/{symbol}')
def stock_query(symbol: str):
    if not (symbol.isdigit() and 4 <= len(symbol) <= 6):
        raise HTTPException(400, 'invalid symbol')
    return sources.stock_query(symbol)


@app.get('/api/support/{symbol}')
def support_query(symbol: str):
    if not (symbol.isdigit() and 4 <= len(symbol) <= 6):
        raise HTTPException(400, 'invalid symbol')
    return sources.support(symbol)


class CardOrder(BaseModel):
    key: str
    ids: list[str]


@app.get('/api/card-order')
def get_card_order():
    try:
        with open(_CARD_ORDER_FILE, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


@app.post('/api/card-order')
def set_card_order(req: CardOrder):
    if req.key not in ('signals', 'life', 'tools', 'blocks'):
        raise HTTPException(400, f'unknown key: {req.key}')
    data = {}
    try:
        with open(_CARD_ORDER_FILE, encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        pass
    data[req.key] = [str(x) for x in req.ids][:300]
    tmp = _CARD_ORDER_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, _CARD_ORDER_FILE)
    return {'ok': True}


class WatchlistStock(BaseModel):
    code: str
    name: str
    exchange: str = 'TSE'


class MaWatchlist(BaseModel):
    stocks: list[WatchlistStock]


@app.get('/api/ma-watchlist')
def get_ma_watchlist():
    try:
        with open(_MA_WATCHLIST_FILE, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {'stocks': []}


@app.post('/api/ma-watchlist')
def set_ma_watchlist(req: MaWatchlist):
    stocks, seen = [], set()
    for s in req.stocks[:50]:
        code = s.code.strip()
        if not re.fullmatch(r'\d{4}', code) or code in seen:
            raise HTTPException(400, f'無效或重複的股票代號：{code}')
        seen.add(code)
        stocks.append({
            'code': code,
            'name': s.name.strip() or code,
            'exchange': s.exchange if s.exchange in ('TSE', 'OTC') else 'TSE',
        })
    tmp = _MA_WATCHLIST_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump({'stocks': stocks}, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _MA_WATCHLIST_FILE)
    sources._ma_names_cache = None   # 清快取，讓卡片名稱立即更新
    return {'ok': True, 'stocks': stocks}


class ChatRequest(BaseModel):
    prompt: str


@app.post('/api/chat')
def chat(req: ChatRequest):
    if not req.prompt.strip():
        raise HTTPException(400, 'empty prompt')
    return StreamingResponse(agent_mod.chat_stream(req.prompt.strip()),
                             media_type='text/event-stream')


@app.get('/', response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, 'index.html')


@app.api_route('/{full_path:path}', methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH'])
async def svc_proxy_fallback(full_path: str, request: Request):
    """被代理頁面裡的根相對連結／資源路徑（如 /rabbit、/static/x.css）不會帶 /svc/<port> 前綴，
    靠 Referer 找出它是從哪個服務頁面來的。用 307 導回 /svc/<port>/... ，讓網址列與後續
    Referer 都留在代理路徑下，下一層連結才不會跟丟。找不到來源就照常 404。"""
    m = re.search(r'/svc/(\d+)(?:/|$|\?)', request.headers.get('referer', ''))
    if m and int(m.group(1)) in PROXY_PORTS:
        target = f"/svc/{m.group(1)}/{full_path}"
        if request.url.query:
            target += f"?{request.url.query}"
        return RedirectResponse(target, status_code=307)
    raise HTTPException(404, 'not found')


if __name__ == '__main__':
    uvicorn.run(app, host='0.0.0.0', port=5950)
