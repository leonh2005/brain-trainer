// ── 常數 ────────────────────────────────────────────────────────────────────
const COST_RATE = 0.435;
const SIGNAL_DEFS = [
  'VWAP突破', 'OBV領先創高', 'KD鈍化≥80', 'MACD0軸上金叉',
  'RSI5穿RSI10', '預估量爆增', '外盤比≥65%', '委買委賣差',
  '昨量單K', '單K倍量', '超越開盤量', '量爆top30', 'MACD背離'
];

// ── 圖表物件 ─────────────────────────────────────────────────────────────────
let chart, candleSeries, anchorSeries;
let ma5Series, ma10Series, ma20Series;
let volChart, volSeries, volAnchor;
let macdChart, difSeries, macdSignalSeries, macdHistSeries, macdAnchor;
let rsiChart, rsiSeries, rsiAnchor;
let obvChart, obvSeries, obvAnchor;

// ── 回放狀態 ─────────────────────────────────────────────────────────────────
let allBars = [], shownBars = [];
let playIndex = 0, isPlaying = false, playTimer = null, speed = 1;
let avg5 = 0, yadayVol = 0, yadayHigh = 0, yadayLow = 0, yadayClose = 0;
let position = null, trades = [], totalPnl = 0;
let _markers = [];

// ── 指標開關狀態 ─────────────────────────────────────────────────────────────
let maState = { 5: true, 10: true, 20: true };
let pivotVisible = false, cdpVisible = false;
let pivotLines = [], cdpLinesList = [];

// ── 指標計算狀態（逐 bar 遞增）──────────────────────────────────────────────
const K12 = 2 / 11, K26 = 2 / 24, K9 = 2 / 9, RSI_K = 1 / 14;
let ema12St = null, ema26St = null, signalSt = null;
let rsiGain = null, rsiLoss = null, rsiPrevC = null;
let obvVal = 0, obvPrevC = null;

// ── 圖表初始化 ───────────────────────────────────────────────────────────────
function initChart() {
  // ── 主圖 ──────────────────────────────────────
  const el = document.getElementById('chart');
  chart = LightweightCharts.createChart(el, {
    layout:      { background: { color: '#0d1117' }, textColor: '#8b949e' },
    grid:        { vertLines: { color: '#21262d' }, horzLines: { color: '#21262d' } },
    crosshair:   { mode: LightweightCharts.CrosshairMode.Normal },
    rightPriceScale: { borderColor: '#30363d' },
    timeScale:   { borderColor: '#30363d', timeVisible: true, secondsVisible: false,
                   shiftVisibleRangeOnNewBar: false },
    width: el.clientWidth, height: el.clientHeight,
  });

  candleSeries = chart.addCandlestickSeries({
    upColor: '#f85149', downColor: '#3fb950',
    borderUpColor: '#f85149', borderDownColor: '#3fb950',
    wickUpColor: '#f85149', wickDownColor: '#3fb950',
  });

  // 均線（疊加主圖）
  const lineOpts = { crosshairMarkerVisible: false, lastValueVisible: false, priceLineVisible: false, lineWidth: 1 };
  ma5Series  = chart.addLineSeries({ ...lineOpts, color: '#f0883e' });
  ma10Series = chart.addLineSeries({ ...lineOpts, color: '#58a6ff' });
  ma20Series = chart.addLineSeries({ ...lineOpts, color: '#bc8cff' });

  // 時間錨點（透明，撐住 09:00~13:30 範圍）
  anchorSeries = chart.addLineSeries({
    color: 'rgba(0,0,0,0)', lineWidth: 0,
    crosshairMarkerVisible: false, lastValueVisible: false, priceLineVisible: false,
    priceScaleId: 'anchor',
  });
  chart.priceScale('anchor').applyOptions({ visible: false });

  // ── 副圖共用設定 ────────────────────────────────
  const subOpts = (id) => {
    const e = document.getElementById(id);
    return {
      layout:      { background: { color: '#0d1117' }, textColor: '#8b949e' },
      grid:        { vertLines: { color: '#21262d' }, horzLines: { color: '#21262d' } },
      rightPriceScale: { borderColor: '#30363d' },
      timeScale:   { visible: false, shiftVisibleRangeOnNewBar: false },
      crosshair:   { mode: LightweightCharts.CrosshairMode.Normal },
      handleScroll: false, handleScale: false,
      width: e.clientWidth, height: e.clientHeight,
    };
  };

  // ── 成交量副圖 ─────────────────────────────────
  volChart = LightweightCharts.createChart(document.getElementById('vol-chart'), subOpts('vol-chart'));
  volChart.applyOptions({ watermark: { visible: true, fontSize: 10, horzAlign: 'left', vertAlign: 'top', color: '#484f58', text: 'Volume' } });
  volSeries = volChart.addHistogramSeries({ color: '#30363d', priceFormat: { type: 'volume' } });
  volChart.priceScale('right').applyOptions({ scaleMargins: { top: 0.1, bottom: 0.05 } });
  volAnchor = _addSubAnchor(volChart);

  // ── MACD 副圖 ──────────────────────────────────
  macdChart = LightweightCharts.createChart(document.getElementById('macd-chart'), subOpts('macd-chart'));
  macdChart.applyOptions({ watermark: { visible: true, fontSize: 10, horzAlign: 'left', vertAlign: 'top', color: '#484f58', text: 'MACD (10,23,8)' } });
  macdHistSeries   = macdChart.addHistogramSeries({ color: '#30363d' });
  difSeries        = macdChart.addLineSeries({ ...lineOpts, color: '#58a6ff' });
  macdSignalSeries = macdChart.addLineSeries({ ...lineOpts, color: '#f0883e' });
  macdAnchor = _addSubAnchor(macdChart);

  // ── RSI 副圖 ───────────────────────────────────
  rsiChart = LightweightCharts.createChart(document.getElementById('rsi-chart'), subOpts('rsi-chart'));
  rsiChart.applyOptions({ watermark: { visible: true, fontSize: 10, horzAlign: 'left', vertAlign: 'top', color: '#484f58', text: 'RSI (14)' } });
  rsiSeries = rsiChart.addLineSeries({ ...lineOpts, color: '#3fb950' });
  rsiSeries.createPriceLine({ price: 70, color: '#f8514980', lineWidth: 1, lineStyle: 2, title: '70' });
  rsiSeries.createPriceLine({ price: 30, color: '#3fb95080', lineWidth: 1, lineStyle: 2, title: '30' });
  rsiChart.priceScale('right').applyOptions({ scaleMargins: { top: 0.05, bottom: 0.05 } });
  rsiAnchor = _addSubAnchor(rsiChart);

  // ── OBV 副圖 ───────────────────────────────────
  obvChart = LightweightCharts.createChart(document.getElementById('obv-chart'), subOpts('obv-chart'));
  obvChart.applyOptions({ watermark: { visible: true, fontSize: 10, horzAlign: 'left', vertAlign: 'top', color: '#484f58', text: 'OBV' } });
  obvSeries = obvChart.addLineSeries({ ...lineOpts, color: '#bc8cff' });
  obvAnchor = _addSubAnchor(obvChart);

  // ── 時間軸同步：主圖 → 副圖 ─────────────────────
  const subCharts = [volChart, macdChart, rsiChart, obvChart];
  chart.timeScale().subscribeVisibleTimeRangeChange((range) => {
    if (!range) return;
    subCharts.forEach(c => { try { c && c.timeScale().setVisibleRange(range); } catch(e) {} });
  });

  // ── Resize ──────────────────────────────────────
  new ResizeObserver(() => {
    chart.applyOptions({ width: el.clientWidth, height: el.clientHeight });
    [['vol-chart', volChart], ['macd-chart', macdChart],
     ['rsi-chart', rsiChart], ['obv-chart', obvChart]].forEach(([id, c]) => {
      const e = document.getElementById(id);
      if (c && e.style.display !== 'none') c.applyOptions({ width: e.clientWidth, height: e.clientHeight });
    });
  }).observe(document.getElementById('chart-area'));
}

function _addSubAnchor(c) {
  const a = c.addLineSeries({
    color: 'rgba(0,0,0,0)', lineWidth: 0,
    crosshairMarkerVisible: false, lastValueVisible: false, priceLineVisible: false,
    priceScaleId: 'sa',
  });
  c.priceScale('sa').applyOptions({ visible: false });
  return a;
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
  allBars = [];          // 清掉舊資料（resetReplay 本身不清，僅在重載時手動清）
  setStatus('載入K棒資料...');

  const data = await fetch(`/api/kbars?stock=${stockId}&date=${dateStr}`).then(r => r.json());
  if (data.error) { setStatus('錯誤: ' + data.error); showLoading(false); return; }

  allBars    = data.bars;
  avg5       = data.avg5;
  yadayVol   = data.yday_vol;
  yadayHigh  = data.yday_high  || 0;
  yadayLow   = data.yday_low   || 0;
  yadayClose = data.yday_close || 0;

  if (!allBars.length) { setStatus('該日無資料'); showLoading(false); return; }

  // 副圖時間錨點先設（value: 0，price scale 隱藏，不干擾 Y 軸）
  // 必須在主圖 anchor 之前，否則 subscribeVisibleTimeRangeChange 觸發時副圖尚無資料
  const subAnchorData = allBars.map(b => ({ time: toTs(b.ts), value: 0 }));
  [volAnchor, macdAnchor, rsiAnchor, obvAnchor].forEach(a => a.setData(subAnchorData));

  // 主圖時間錨點（設定後觸發 subscribeVisibleTimeRangeChange）
  const mid = allBars[Math.floor(allBars.length / 2)].close;
  anchorSeries.setData(allBars.map(b => ({ time: toTs(b.ts), value: mid })));

  // 鎖定可見範圍
  const firstDate = allBars[0].ts.slice(0, 10);
  const range = { from: toTs(firstDate + ' 09:00'), to: toTs(allBars[allBars.length - 1].ts) };
  chart.timeScale().setVisibleRange(range);
  [volChart, macdChart, rsiChart, obvChart].forEach(c => c.timeScale().setVisibleRange(range));

  // 畫三關價 / CDP（如果已開啟）
  if (pivotVisible) drawPivotLines();
  if (cdpVisible)   drawCDPLines();

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

// ── 指標計算 ─────────────────────────────────────────────────────────────────
function calcMA(n) {
  if (shownBars.length < n) return null;
  return shownBars.slice(-n).reduce((s, b) => s + b.close, 0) / n;
}

function updateMAs(ts) {
  const v5 = calcMA(5), v10 = calcMA(10), v20 = calcMA(20);
  if (v5  !== null) ma5Series.update({ time: ts, value: v5 });
  if (v10 !== null) ma10Series.update({ time: ts, value: v10 });
  if (v20 !== null) ma20Series.update({ time: ts, value: v20 });
}

function updateMACDNext(ts, bar) {
  const c = bar.close;
  ema12St  = ema12St  === null ? c : c * K12 + ema12St  * (1 - K12);
  ema26St  = ema26St  === null ? c : c * K26 + ema26St  * (1 - K26);
  const dif  = ema12St - ema26St;
  signalSt   = signalSt === null ? dif : dif * K9 + signalSt * (1 - K9);
  const hist = dif - signalSt;
  difSeries.update({ time: ts, value: dif });
  macdSignalSeries.update({ time: ts, value: signalSt });
  macdHistSeries.update({ time: ts, value: hist, color: hist >= 0 ? '#3fb95099' : '#f8514999' });
}

function updateRSINext(ts, bar) {
  const c = bar.close;
  if (rsiPrevC === null) { rsiPrevC = c; return; }
  const delta = c - rsiPrevC;
  rsiPrevC = c;
  const gain = delta > 0 ? delta : 0;
  const loss = delta < 0 ? -delta : 0;
  rsiGain = rsiGain === null ? gain : gain * RSI_K + rsiGain * (1 - RSI_K);
  rsiLoss = rsiLoss === null ? loss : loss * RSI_K + rsiLoss * (1 - RSI_K);
  const rs  = rsiLoss < 1e-10 ? Infinity : rsiGain / rsiLoss;
  const rsi = rs === Infinity ? 100 : 100 - 100 / (1 + rs);
  rsiSeries.update({ time: ts, value: rsi });
}

function updateOBVNext(ts, bar) {
  if (obvPrevC !== null) {
    if (bar.close > obvPrevC)      obvVal += bar.volume;
    else if (bar.close < obvPrevC) obvVal -= bar.volume;
  }
  obvPrevC = bar.close;
  obvSeries.update({ time: ts, value: obvVal });
}

// ── 三關價 & CDP ─────────────────────────────────────────────────────────────
function drawPivotLines() {
  if (!yadayHigh || !yadayLow || !yadayClose) return;
  const yh = yadayHigh, yl = yadayLow, yc = yadayClose;
  const pivot = (yh + yl + yc) / 3;
  pivotLines = [
    { price: pivot * 2 - yl,  color: '#f85149', title: '壓力' },
    { price: pivot,            color: '#8b949e', title: '平衡' },
    { price: pivot * 2 - yh,  color: '#3fb950', title: '支撐' },
  ].map(d => candleSeries.createPriceLine({ lineWidth: 1, lineStyle: 2, axisLabelVisible: true, ...d }));
}

function drawCDPLines() {
  if (!yadayHigh || !yadayLow || !yadayClose) return;
  const yh = yadayHigh, yl = yadayLow, yc = yadayClose;
  const cdp = (yh + yl + yc * 2) / 4;
  cdpLinesList = [
    { price: cdp + (yh - yl),  color: '#f0883e', title: 'AH'  },
    { price: cdp * 2 - yl,     color: '#d29922', title: 'NH'  },
    { price: cdp,               color: '#8b949e', title: 'CDP', lineStyle: 0 },
    { price: cdp * 2 - yh,     color: '#58a6ff', title: 'NL'  },
    { price: cdp - (yh - yl),  color: '#388bfd', title: 'AL'  },
  ].map(d => candleSeries.createPriceLine({ lineWidth: 1, lineStyle: 2, axisLabelVisible: true, ...d }));
}

// ── 指標開關 ─────────────────────────────────────────────────────────────────
function toggleMA(n) {
  maState[n] = !maState[n];
  const s = { 5: ma5Series, 10: ma10Series, 20: ma20Series }[n];
  s.applyOptions({ visible: maState[n] });
  document.getElementById('ind-ma' + n).classList.toggle('active', maState[n]);
}

function togglePivot() {
  pivotVisible = !pivotVisible;
  if (pivotVisible) drawPivotLines();
  else { pivotLines.forEach(pl => candleSeries.removePriceLine(pl)); pivotLines = []; }
  document.getElementById('ind-pivot').classList.toggle('active', pivotVisible);
}

function toggleCDP() {
  cdpVisible = !cdpVisible;
  if (cdpVisible) drawCDPLines();
  else { cdpLinesList.forEach(pl => candleSeries.removePriceLine(pl)); cdpLinesList = []; }
  document.getElementById('ind-cdp').classList.toggle('active', cdpVisible);
}

function togglePanel(name) {
  const el  = document.getElementById(name + '-chart');
  const btn = document.getElementById('ind-' + name);
  const isVisible = el.style.display !== 'none';
  el.style.display = isVisible ? 'none' : '';
  btn.classList.toggle('active', !isVisible);
  if (!isVisible) {
    const c = { vol: volChart, macd: macdChart, rsi: rsiChart, obv: obvChart }[name];
    if (c) {
      c.applyOptions({ width: el.clientWidth, height: el.clientHeight });
      const range = chart.timeScale().getVisibleRange();
      if (range) c.timeScale().setVisibleRange(range);
    }
  }
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

  // K棒
  candleSeries.update({ time: ts, open: bar.open, high: bar.high, low: bar.low, close: bar.close });

  // 成交量
  volSeries.update({ time: ts, value: bar.volume,
    color: bar.close >= bar.open ? '#f8514966' : '#3fb95066' });

  shownBars.push(bar);
  playIndex++;

  // 指標更新
  updateMAs(ts);
  updateMACDNext(ts, bar);
  updateRSINext(ts, bar);
  updateOBVNext(ts, bar);

  document.getElementById('current-time').textContent = bar.ts.substring(11, 16);
  if (position) updatePnl(bar.close);
  if (shownBars.length >= 5) fetchSignals();

  setStatus(`${bar.ts.substring(11,16)}  ${bar.close.toFixed(1)}元  ${playIndex}/${allBars.length}根`);
  scheduleNext();
}

function setSpeed(s) {
  speed = s;
  ['0_5','1','3','10','30'].forEach(x => {
    const el = document.getElementById('s' + x);
    if (el) el.classList.toggle('active', parseFloat(x.replace('_','.')) === s);
  });
}

function resetReplay() {
  pause();
  playIndex = 0; shownBars = [];
  // allBars 保留，讓重置後仍可重播（loadKbars 呼叫前會重新賦值）

  // 清空所有 series 資料
  candleSeries  && candleSeries.setData([]);
  anchorSeries  && anchorSeries.setData([]);
  ma5Series     && ma5Series.setData([]);
  ma10Series    && ma10Series.setData([]);
  ma20Series    && ma20Series.setData([]);
  volSeries     && volSeries.setData([]);
  volAnchor     && volAnchor.setData([]);
  difSeries     && difSeries.setData([]);
  macdSignalSeries && macdSignalSeries.setData([]);
  macdHistSeries   && macdHistSeries.setData([]);
  macdAnchor    && macdAnchor.setData([]);
  rsiSeries     && rsiSeries.setData([]);
  rsiAnchor     && rsiAnchor.setData([]);
  obvSeries     && obvSeries.setData([]);
  obvAnchor     && obvAnchor.setData([]);

  // 移除三關價 / CDP 水平線
  if (candleSeries) {
    pivotLines.forEach(pl => candleSeries.removePriceLine(pl));
    cdpLinesList.forEach(pl => candleSeries.removePriceLine(pl));
  }
  pivotLines = []; cdpLinesList = [];

  // 重置指標計算狀態
  ema12St = ema26St = signalSt = null;
  rsiGain = rsiLoss = rsiPrevC = null;
  obvVal = 0; obvPrevC = null;

  // 重置持倉 / UI
  _markers = [];
  position = null;
  document.getElementById('position-price').textContent = '無持倉';
  document.getElementById('position-pnl').textContent   = '—';
  document.getElementById('position-pnl').className     = 'pos-flat';
  const hasData = allBars.length > 0;
  document.getElementById('buy-btn').disabled   = true;
  document.getElementById('sell-btn').disabled  = true;
  document.getElementById('play-btn').disabled  = !hasData;
  document.getElementById('reset-btn').disabled = !hasData;
  document.getElementById('play-btn').textContent = '▶ 播放';
  document.getElementById('play-btn').classList.remove('active');
  document.getElementById('current-time').textContent = '--:--';
  updateSignals(Object.fromEntries(SIGNAL_DEFS.map(n => [n, false])), 0, false);
  setStatus(hasData ? `共 ${allBars.length} 根K棒，按播放開始` : '請選擇股票和日期');
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
