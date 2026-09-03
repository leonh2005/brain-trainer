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

import agent as agent_mod
import jobs as jobs_mod
import sources

# 卡片連結透過 /svc/<port>/... 走反向代理，讓外網（Cloudflare Tunnel）也連得到
# 各服務的本機頁面，不必各自對外開洞。僅白名單內的 port 可被代理。
PROXY_PORTS = {5070, 5100, 5200, 5250, 5300, 5350, 5400, 5460, 5500, 5501,
               5650, 5750, 5800, 5810, 5850, 5905, 5910, 5960, 5970, 7799, 8188}
_PROXY_HOP_HEADERS = {
    'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
    'te', 'trailers', 'transfer-encoding', 'upgrade', 'content-encoding', 'content-length',
}

app = FastAPI(title='command-center')
templates = Jinja2Templates(directory='templates')

_AUTH_USER, _AUTH_PASS = open(
    f'{sources.CC}/.secrets/command_center_auth.txt', encoding='utf-8'
).read().strip().split(':', 1)


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
