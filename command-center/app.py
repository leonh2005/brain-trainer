"""AI 指揮中心 — 統一入口儀表板（port 5950，對既有服務全唯讀）"""
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse
from fastapi.templating import Jinja2Templates
from fastapi import Request
from pydantic import BaseModel
import uvicorn
import os

import agent as agent_mod
import jobs as jobs_mod
import sources

app = FastAPI(title='command-center')
templates = Jinja2Templates(directory='templates')


@app.get('/api/health')
def health():
    return {'status': 'ok', 'service': 'command-center'}


@app.get('/market-dashboard')
def market_dashboard():
    """市場恐慌儀表板靜態報告（每日 07:30 由 market-dashboard 服務產生）。"""
    path = f'{sources.CC}/market-dashboard/index.html'
    if not os.path.exists(path):
        raise HTTPException(404, '市場恐慌儀表板尚未產生')
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


@app.get('/api/life/rabbit')
def life_rabbit():
    return sources.rabbit()


@app.get('/api/life/skilltree')
def life_skilltree():
    return sources.skilltree()


@app.get('/api/hay')
def hay():
    return sources.hay()


@app.get('/api/life/sim-invest')
def life_sim_invest():
    return sources.sim_invest()


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


if __name__ == '__main__':
    uvicorn.run(app, host='127.0.0.1', port=5950)
