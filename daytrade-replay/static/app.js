// ── 常數 ────────────────────────────────────────────────────────────────────
const COST_RATE = 0.435;
const SIGNAL_DEFS = [
  'VWAP突破', 'OBV領先創高', 'KD鈍化≥80', 'MACD0軸上金叉',
  'RSI5穿RSI10', '預估量爆增', '外盤比≥65%', '委買委賣差',
  '昨量單K', '單K倍量', '超越開盤量', '量爆top30', 'MACD背離'
];

// ── 狀態 ────────────────────────────────────────────────────────────────────
let chart, candleSeries, volumeSeries, anchorSeries;
let allBars = [], shownBars = [];
let playIndex = 0, isPlaying = false, playTimer = null, speed = 1;
let avg5 = 0, yadayVol = 0;
let position = null, trades = [], totalPnl = 0;
let _markers = [];

// ── 圖表初始化 ───────────────────────────────────────────────────────────────
function initChart() {
  const el = document.getElementById('chart');
  chart = LightweightCharts.createChart(el, {
    layout:      { background: { color: '#0d1117' }, textColor: '#8b949e' },
    grid:        { vertLines: { color: '#21262d' }, horzLines: { color: '#21262d' } },
    crosshair:   { mode: LightweightCharts.CrosshairMode.Normal },
    rightPriceScale: { borderColor: '#30363d' },
    timeScale:   { borderColor: '#30363d', timeVisible: true, secondsVisible: false,
                   shiftVisibleRangeOnNewBar: false },  // ← 禁止新K棒推動視窗
    width: el.clientWidth, height: el.clientHeight,
  });

  // 真實 K 棒（只含已播放的）
  candleSeries = chart.addCandlestickSeries({
    upColor: '#f85149', downColor: '#3fb950',
    borderUpColor: '#f85149', borderDownColor: '#3fb950',
    wickUpColor: '#f85149', wickDownColor: '#3fb950',
  });

  // 成交量（只含已播放的）
  volumeSeries = chart.addHistogramSeries({
    color: '#30363d', priceFormat: { type: 'volume' }, priceScaleId: 'vol',
  });
  chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });

  // 時間軸錨點（完全不顯示，只撐住 09:01~13:30 的時間範圍）
  anchorSeries = chart.addLineSeries({
    color:                  'rgba(0,0,0,0)',  // 透明
    lineWidth:              0,
    crosshairMarkerVisible: false,
    lastValueVisible:       false,
    priceLineVisible:       false,
    priceScaleId:           'anchor',
  });
  chart.priceScale('anchor').applyOptions({ visible: false });

  new ResizeObserver(() => {
    chart.applyOptions({ width: el.clientWidth, height: el.clientHeight });
  }).observe(el);
}

// ── 訊號燈 ───────────────────────────────────────────────────────────────────
function initSignals() {
  const list = document.getElementById('signals-list');
  list.innerHTML = '';
  for (const name of SIGNAL_DEFS) {
    const row = document.createElement('div');
    row.className = 'signal-row';
    row.id = 'sig-row-' + name;
    row.innerHTML = `<div class="signal-dot" id="sig-dot-${name}"></div>
                     <span class="signal-label">${name}</span>`;
    list.appendChild(row);
  }
}

function updateSignals(signals, confidence, trigger) {
  for (const [name, val] of Object.entries(signals)) {
    const dot = document.getElementById('sig-dot-' + name);
    const row = document.getElementById('sig-row-' + name);
    if (!dot) continue;
    dot.className = 'signal-dot';
    row.className  = 'signal-row';
    if (val === null)  dot.classList.add('na');
    else if (val)    { dot.classList.add('on'); row.classList.add('triggered'); }
  }
  document.getElementById('confidence-text').textContent = confidence + '%';
  document.getElementById('confidence-bar').style.width  = confidence + '%';
  document.getElementById('trigger-badge').style.display = trigger ? 'block' : 'none';
}

// ── 股票 / 日期 ──────────────────────────────────────────────────────────────
async function loadStocks() {
  const res = await fetch('/api/stocks');
  const { stocks } = await res.json();
  const sel = document.getElementById('stock-select');
  sel.innerHTML = '<option value="">選股票...</option>';
  for (const s of stocks) {
    const opt = document.createElement('option');
    opt.value = s.code;
    opt.textContent = `${s.code} ${s.name} (${s.avg_vol.toLocaleString()}張)`;
    sel.appendChild(opt);
  }
  sel.onchange = () => loadDates(sel.value);
}

async function loadDates(stockId) {
  if (!stockId) return;
  setStatus('載入日期...');
  const { dates } = await fetch('/api/dates?stock=' + stockId).then(r => r.json());
  const sel = document.getElementById('date-select');
  sel.innerHTML = '<option value="">選日期...</option>';
  for (const d of [...dates].reverse()) {
    const opt = document.createElement('option');
    opt.value = d; opt.textContent = d;
    sel.appendChild(opt);
  }
  sel.onchange = () => loadKbars(stockId, sel.value);
  setStatus('請選擇日期');
}

async function loadKbars(stockId, dateStr) {
  if (!stockId || !dateStr) return;
  showLoading(true);
  resetReplay();
  setStatus('載入K棒資料...');

  const data = await fetch(`/api/kbars?stock=${stockId}&date=${dateStr}`).then(r => r.json());
  if (data.error) { setStatus('錯誤: ' + data.error); showLoading(false); return; }

  allBars   = data.bars;
  avg5      = data.avg5;
  yadayVol  = data.yday_vol;

  if (!allBars.length) { setStatus('該日無資料'); showLoading(false); return; }

  // 用錨點 series 撐住完整時間軸（09:01 → 13:30）
  // 所有時間戳都放進去，值用中間價（不影響右側 price scale 因為 scale 已隱藏）
  const mid = allBars[Math.floor(allBars.length / 2)].close;
  anchorSeries.setData(allBars.map(b => ({
    time:  toTs(b.ts),
    value: mid,
  })));

  // 鎖定可見範圍到 09:00~13:30
  const firstDate = allBars[0].ts.slice(0, 10); // "YYYY-MM-DD"
  chart.timeScale().setVisibleRange({
    from: toTs(firstDate + ' 09:00'),
    to:   toTs(allBars[allBars.length - 1].ts),
  });

  document.getElementById('play-btn').disabled  = false;
  document.getElementById('reset-btn').disabled = false;
  document.getElementById('buy-btn').disabled   = false;
  showLoading(false);
  setStatus(`共 ${allBars.length} 根K棒，按播放開始`);
}

function toTs(tsStr) {
  // tsStr 是台灣時間（UTC+8），格式 "YYYY-MM-DD HH:MM"
  // LC v4 以 UTC 顯示，加 8h 讓軸上顯示台灣時間
  const iso = tsStr.replace(' ', 'T') + ':00+08:00';
  return Math.floor(new Date(iso).getTime() / 1000) + 8 * 3600;
}

// ── 回放控制 ─────────────────────────────────────────────────────────────────
function togglePlay() { isPlaying ? pause() : play(); }

function play() {
  if (playIndex >= allBars.length) return;
  isPlaying = true;
  document.getElementById('play-btn').textContent = '⏸ 暫停';
  document.getElementById('play-btn').classList.add('active');
  scheduleNext();
}

function pause() {
  isPlaying = false;
  document.getElementById('play-btn').textContent = '▶ 播放';
  document.getElementById('play-btn').classList.remove('active');
  if (playTimer) { clearTimeout(playTimer); playTimer = null; }
}

function scheduleNext() {
  if (!isPlaying || playIndex >= allBars.length) {
    if (playIndex >= allBars.length) { setStatus('回放結束'); pause(); if (position) forceClose(); }
    return;
  }
  playTimer = setTimeout(advanceBar, Math.max(50, 1000 / speed));
}

async function advanceBar() {
  if (playIndex >= allBars.length) { scheduleNext(); return; }

  const bar = allBars[playIndex];
  const ts  = toTs(bar.ts);

  // 只更新真實已播棒（不動 anchorSeries，它一直撐住全日時間軸）
  candleSeries.update({ time: ts, open: bar.open, high: bar.high, low: bar.low, close: bar.close });
  volumeSeries.update({ time: ts, value: bar.volume,
    color: bar.close >= bar.open ? '#f8514966' : '#3fb95066' });

  shownBars.push(bar);
  playIndex++;

  // 時間顯示
  document.getElementById('current-time').textContent = bar.ts.substring(11, 16);

  // 損益更新
  if (position) updatePnl(bar.close);

  // 訊號計算
  if (shownBars.length >= 5) fetchSignals();

  setStatus(`${bar.ts.substring(11,16)}  ${bar.close.toFixed(1)}元  ${playIndex}/${allBars.length}根`);
  scheduleNext();
}

function setSpeed(s) {
  speed = s;
  ['1','3','10','30'].forEach(x =>
    document.getElementById('s'+x).classList.toggle('active', parseInt(x) === s));
}

function resetReplay() {
  pause();
  playIndex = 0; shownBars = []; allBars = [];
  candleSeries  && candleSeries.setData([]);
  volumeSeries  && volumeSeries.setData([]);
  anchorSeries  && anchorSeries.setData([]);
  _markers = [];
  position = null;
  document.getElementById('position-price').textContent = '無持倉';
  document.getElementById('position-pnl').textContent   = '—';
  document.getElementById('position-pnl').className     = 'pos-flat';
  document.getElementById('buy-btn').disabled  = true;
  document.getElementById('sell-btn').disabled = true;
  document.getElementById('play-btn').disabled = true;
  document.getElementById('play-btn').textContent = '▶ 播放';
  document.getElementById('play-btn').classList.remove('active');
  document.getElementById('current-time').textContent = '--:--';
  updateSignals(Object.fromEntries(SIGNAL_DEFS.map(n => [n, false])), 0, false);
  setStatus('請選擇股票和日期');
}

// ── 訊號 API ─────────────────────────────────────────────────────────────────
let sigPending = false;
async function fetchSignals() {
  if (sigPending) return;
  sigPending = true;
  try {
    const res = await fetch('/api/signals', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ bars: shownBars, avg5, yday_vol: yadayVol }),
    });
    const data = await res.json();
    if (data.signals) updateSignals(data.signals, data.confidence, data.trigger);
    if (data.trigger && shownBars.length > 0) {
      const last = shownBars[shownBars.length - 1];
      addMarker(toTs(last.ts), 'belowBar', '#f0883e', 'arrowUp', '⚡');
    }
  } catch(e) {}
  finally { sigPending = false; }
}

// ── 手動交易 ─────────────────────────────────────────────────────────────────
function buy() {
  if (!shownBars.length || position) return;
  const bar = shownBars[shownBars.length - 1];
  position = { price: bar.close, time: bar.ts.substring(11, 16) };
  document.getElementById('position-price').textContent = `均價 ${bar.close.toFixed(2)} 元`;
  document.getElementById('position-pnl').textContent  = '0.00%';
  document.getElementById('position-pnl').className    = 'pos-flat';
  document.getElementById('buy-btn').disabled  = true;
  document.getElementById('sell-btn').disabled = false;
  addMarker(toTs(bar.ts), 'belowBar', '#f85149', 'arrowUp', '買');
}

function sell() {
  if (!position || !shownBars.length) return;
  const bar = shownBars[shownBars.length - 1];
  const pnl = calcPnl(position.price, bar.close);
  recordTrade(position.time, bar.ts.substring(11,16), position.price, bar.close, pnl);
  addMarker(toTs(bar.ts), 'aboveBar', '#3fb950', 'arrowDown', '賣');
  position = null;
  document.getElementById('position-price').textContent = '無持倉';
  document.getElementById('position-pnl').textContent   = '—';
  document.getElementById('position-pnl').className     = 'pos-flat';
  document.getElementById('buy-btn').disabled  = false;
  document.getElementById('sell-btn').disabled = true;
}

function forceClose() {
  if (!position || !shownBars.length) { position = null; return; }
  const bar = shownBars[shownBars.length - 1];
  recordTrade(position.time, bar.ts.substring(11,16), position.price, bar.close,
              calcPnl(position.price, bar.close), true);
  position = null;
}

function calcPnl(entry, exit) { return (exit - entry) / entry * 100 - COST_RATE; }

function updatePnl(cur) {
  const u = calcPnl(position.price, cur);
  const el = document.getElementById('position-pnl');
  el.textContent = (u >= 0 ? '+' : '') + u.toFixed(2) + '%';
  el.className   = u > 0 ? 'pos-up' : u < 0 ? 'pos-down' : 'pos-flat';
}

function recordTrade(et, xt, ep, xp, pnl, forced = false) {
  totalPnl += pnl;
  const dir  = pnl >= 0 ? 'up' : 'down';
  const sign = pnl >= 0 ? '+' : '';
  const item = document.createElement('div');
  item.className = 'trade-item';
  item.innerHTML = `<span>${et}買@${ep.toFixed(1)}</span><br>
    <span>${xt}賣@${xp.toFixed(1)}</span>${forced ? ' <span style="color:#d29922">(強平)</span>' : ''}
    <span class="pnl ${dir}"> ${sign}${pnl.toFixed(2)}%</span>`;
  document.getElementById('trades-content').insertBefore(item,
    document.getElementById('trades-content').firstChild);
  const tEl = document.getElementById('total-pnl-val');
  tEl.textContent = (totalPnl >= 0 ? '+' : '') + totalPnl.toFixed(2) + '%';
  tEl.className   = totalPnl > 0 ? 'pos-up' : totalPnl < 0 ? 'pos-down' : 'pos-flat';
}

function addMarker(time, position, color, shape, text) {
  _markers.push({ time, position, color, shape, text });
  _markers.sort((a, b) => a.time - b.time);
  candleSeries.setMarkers(_markers);
}

// ── 工具 ─────────────────────────────────────────────────────────────────────
function setStatus(msg) { document.getElementById('status-bar').textContent = msg; }
function showLoading(show) { document.getElementById('loading').classList.toggle('show', show); }

// ── 啟動 ─────────────────────────────────────────────────────────────────────
initChart();
initSignals();
loadStocks();
