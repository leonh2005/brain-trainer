// ── 常數 ────────────────────────────────────────────────────────────────────
const BUY_FEE_RATE  = 0.001425;  // 手續費 0.1425%（未打折）
const SELL_FEE_RATE = 0.001425;  // 手續費 0.1425%
const DAY_TRADE_TAX = 0.0015;    // 當沖證交稅 0.15%
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
let position = null, trades = [], totalPnl = 0, totalNTD = 0;
let lotSize = 1;
let _markers = [];

// ── 大盤 / 產業資料 ───────────────────────────────────────────────────────────
let _taiexMap = {};    // { 'HH:MM': close }
let _taiexOpen = 0;    // 開盤第一根收盤
let _sectorMap = {};   // { 'HH:MM': close }
let _sectorOpen = 0;   // 開盤第一根收盤
let _sectorName = '';  // 顯示用產業名稱

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

  // 成交量 hover tooltip（原生 mousemove，不依賴 LW 事件系統）
  const _volTip = document.createElement('div');
  _volTip.id = 'vol-tooltip';
  const _volChartEl = document.getElementById('vol-chart');
  _volChartEl.appendChild(_volTip);
  // capture:true 確保在 LW Charts canvas stopPropagation 之前攔截
  _volChartEl.addEventListener('mousemove', e => {
    const rect = _volChartEl.getBoundingClientRect();
    const x = e.clientX - rect.left;
    try {
      const logical = volChart.timeScale().coordinateToLogical(x);
      if (logical === null || logical === undefined) { _volTip.style.display = 'none'; return; }
      const idx = Math.round(logical);
      if (idx < 0 || idx >= shownBars.length) { _volTip.style.display = 'none'; return; }
      const vol = shownBars[idx].volume;
      const formatted = vol >= 1000 ? (vol / 1000).toFixed(1) + 'K張' : Math.round(vol) + '張';
      _volTip.textContent = formatted;
      _volTip.style.left = Math.min(x + 8, _volChartEl.clientWidth - 80) + 'px';
      _volTip.style.top = '6px';
      _volTip.style.display = 'block';
    } catch(_) { _volTip.style.display = 'none'; }
  }, true);
  _volChartEl.addEventListener('mouseleave', () => { _volTip.style.display = 'none'; }, true);

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
    requestAnimationFrame(syncPriceScales);
  }).observe(document.getElementById('chart-area'));
}

// ── 初始化圖表視窗：顯示全天所有 bar，並同步副圖 ─────────────────────────────
function initChartView() {
  if (!allBars.length) return;
  requestAnimationFrame(() => {
    try {
      // 以邏輯範圍顯示全部 bar（每根 bar 均分寬度，不會因播放根數少而變粗）
      chart.timeScale().setVisibleLogicalRange({ from: -0.5, to: allBars.length - 0.5 });
      // 取主圖時間範圍同步副圖
      const timeRange = chart.timeScale().getVisibleRange();
      if (timeRange) {
        [volChart, macdChart, rsiChart, obvChart].forEach(c => {
          try { c && c.timeScale().setVisibleRange(timeRange); } catch(e) {}
        });
      }
    } catch(e) {}
  });
}

// ── 副圖對齊：同步所有圖表的右側價格軸寬度 ─────────────────────────────────
function syncPriceScales() {
  const charts = [chart, volChart, macdChart, rsiChart, obvChart].filter(Boolean);
  try {
    const maxW = Math.max(...charts.map(c => c.priceScale('right').width()));
    if (maxW > 0) charts.forEach(c => c.priceScale('right').applyOptions({ minimumWidth: maxW }));
  } catch(e) {}
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

  // 主圖 auto-fit 後同步副圖
  initChartView();

  // 資料載入後同步對齊
  setTimeout(syncPriceScales, 200);

  // 畫三關價 / CDP（如果已開啟）
  if (pivotVisible) drawPivotLines();
  if (cdpVisible)   drawCDPLines();

  document.getElementById('play-btn').disabled   = false;
  document.getElementById('reset-btn').disabled  = false;
  document.getElementById('rewind-btn').disabled = false;
  document.getElementById('buy-btn').disabled    = false;
  document.getElementById('sell-btn').disabled   = false;
  showLoading(false);
  setStatus(`共 ${allBars.length} 根K棒，按播放開始`);

  // 非同步載入大盤/產業資料（不阻塞主流程）
  fetchIndexData(stockId, dateStr);
}

async function fetchIndexData(stockId, dateStr) {
  try {
    const res = await fetch(`/api/index_data?stock=${stockId}&date=${dateStr}`);
    const d = await res.json();
    if (d.error) return;

    // 建立 taiex map: HH:MM → close
    _taiexMap = {};
    _taiexOpen = 0;
    for (const bar of (d.taiex_bars || [])) {
      const hhmm = bar.ts.substring(11, 16);
      _taiexMap[hhmm] = bar.close;
      if (_taiexOpen === 0) _taiexOpen = bar.close;
    }

    // 建立 sector map: HH:MM → close（動態，同 TAIEX）
    _sectorMap = {};
    _sectorOpen = 0;
    _sectorName = '';
    const sector = d.sector || {};
    if (sector.name && sector.bars && sector.bars.length) {
      _sectorName = sector.name;
      for (const bar of sector.bars) {
        const hhmm = bar.ts.substring(11, 16);
        _sectorMap[hhmm] = bar.close;
        if (_sectorOpen === 0) _sectorOpen = bar.close;
      }
    }

    // 載入完後立即顯示當前播放位置的值（或 09:01 初始值）
    const curHhmm = shownBars.length
      ? shownBars[shownBars.length - 1].ts.substring(11, 16)
      : '09:01';
    updateTaiexDisplay(curHhmm);
    updateSectorDisplay(curHhmm);
    if (shownBars.length) updateStockDisplay(shownBars[shownBars.length - 1]);
  } catch(e) { /* 忽略，不影響主功能 */ }
}

function updateTaiexDisplay(hhmm) {
  const cur = _taiexMap[hhmm];
  if (!cur || !_taiexOpen) return;
  const chg = cur - _taiexOpen;
  const pct = (chg / _taiexOpen * 100).toFixed(2);
  const sign = chg >= 0 ? '+' : '';
  const cls  = chg >= 0 ? '#f85149' : '#3fb950';
  document.getElementById('taiex-display').innerHTML =
    `大盤: ${Math.round(cur).toLocaleString()} <span style="color:${cls}">${sign}${pct}%</span>`;
}

function updatePosDisplay() {
  // 頂部 bar
  const el = document.getElementById('pos-display');
  if (el) {
    if (!position) {
      el.innerHTML = '持倉 <span style="color:#8b949e">—</span>';
    } else {
      const cls = position.direction === 'long' ? '#f85149' : '#58a6ff';
      const dir = position.direction === 'long' ? '多' : '空';
      el.innerHTML = `持倉 <span style="color:${cls};font-weight:700">${dir} ${position.lots}張</span>`;
    }
  }
  // 右側面板「當前持倉」
  const hl = document.getElementById('holding-lots');
  if (hl) {
    if (!position) {
      hl.textContent = '0 張';
      hl.style.color = '#8b949e';
    } else {
      const cls = position.direction === 'long' ? '#f85149' : '#58a6ff';
      hl.textContent = `${position.lots} 張`;
      hl.style.color = cls;
    }
  }
}

// ── 確認 modal ────────────────────────────────────────────────────────────────
let _pendingAction = null;

function showConfirm(msg, action) {
  document.getElementById('confirm-msg').textContent = msg;
  document.getElementById('confirm-overlay').style.display = 'flex';
  _pendingAction = action;
}

function confirmYes() {
  document.getElementById('confirm-overlay').style.display = 'none';
  if (_pendingAction) { _pendingAction(); _pendingAction = null; }
}

function confirmNo() {
  document.getElementById('confirm-overlay').style.display = 'none';
  _pendingAction = null;
}

function updateStockDisplay(bar) {
  if (!yadayClose) return;
  const chg = bar.close - yadayClose;
  const pct = (chg / yadayClose * 100).toFixed(2);
  const sign = chg >= 0 ? '+' : '';
  const cls  = chg >= 0 ? '#f85149' : '#3fb950';
  document.getElementById('stock-display').innerHTML =
    `${bar.close.toFixed(1)} <span style="color:${cls}">${sign}${pct}%</span>`;
}

function updateSectorDisplay(hhmm) {
  if (!_sectorName) return;
  const cur = _sectorMap[hhmm];
  if (!cur || !_sectorOpen) return;
  const chg = cur - _sectorOpen;
  const pct = (chg / _sectorOpen * 100).toFixed(2);
  const sign = chg >= 0 ? '+' : '';
  const cls  = chg >= 0 ? '#f85149' : '#3fb950';
  document.getElementById('sector-display').innerHTML =
    `${_sectorName}: ${Math.round(cur).toLocaleString()} <span style="color:${cls}">${sign}${pct}%</span>`;
}

function toTs(tsStr) {
  // tsStr 是台灣時間（UTC+8），格式 "YYYY-MM-DD HH:MM"
  // LC v4 以 UTC 顯示，加 8h 讓軸上顯示台灣時間
  const iso = tsStr.replace(' ', 'T') + ':00+08:00';
  return Math.floor(new Date(iso).getTime() / 1000) + 8 * 3600;
}

// ── 費用 & 損益計算 ──────────────────────────────────────────────────────────
function calcRequiredCapital(price, lots) {
  const shares = lots * 1000;
  const buyAmt = price * shares;
  const fee    = Math.ceil(buyAmt * BUY_FEE_RATE);
  return buyAmt + fee;
}

function calcPnlDetails(entryPrice, exitPrice, lots) {
  const shares  = lots * 1000;
  const gross   = (exitPrice - entryPrice) * shares;
  const buyFee  = Math.ceil(entryPrice * shares * BUY_FEE_RATE);
  const sellFee = Math.ceil(exitPrice  * shares * SELL_FEE_RATE);
  const tax     = Math.floor(exitPrice * shares * DAY_TRADE_TAX);
  const netNTD  = gross - buyFee - sellFee - tax;
  const pct     = netNTD / (entryPrice * shares) * 100;
  return { netNTD, pct };
}

function calcPnlDetailsShort(entryPrice, exitPrice, lots) {
  // 先賣後買：entryPrice=放空價，exitPrice=回補價
  const shares  = lots * 1000;
  const gross   = (entryPrice - exitPrice) * shares;
  const sellFee = Math.ceil(entryPrice * shares * SELL_FEE_RATE);
  const buyFee  = Math.ceil(exitPrice  * shares * BUY_FEE_RATE);
  const tax     = Math.floor(entryPrice * shares * DAY_TRADE_TAX);
  const netNTD  = gross - sellFee - buyFee - tax;
  const pct     = netNTD / (entryPrice * shares) * 100;
  return { netNTD, pct };
}

function updateCapitalDisplay(price) {
  if (!price) return;
  const cap = calcRequiredCapital(price, lotSize);
  document.getElementById('capital-required').textContent =
    `所需資金：$${Math.ceil(cap).toLocaleString('zh-TW', { maximumFractionDigits: 0 })}（${lotSize}張，含手續費）`;
}

function changeLots(delta) {
  lotSize = Math.max(1, lotSize + delta);
  document.getElementById('lot-display').textContent = lotSize;
  if (shownBars.length) updateCapitalDisplay(shownBars[shownBars.length - 1].close);
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
  macdHistSeries.update({ time: ts, value: hist, color: hist >= 0 ? '#f8514999' : '#3fb95099' });
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
  updateCapitalDisplay(bar.close);
  if (position) updatePnl(bar.close);
  if (shownBars.length >= 5) fetchSignals();
  updateStockDisplay(bar);
  updateTaiexDisplay(bar.ts.substring(11, 16));
  updateSectorDisplay(bar.ts.substring(11, 16));

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
  position = null; trades = []; totalPnl = 0; totalNTD = 0;
  updatePosDisplay();
  document.getElementById('position-price').textContent = '無持倉';
  document.getElementById('position-pnl').textContent   = '—';
  document.getElementById('position-pnl').className     = 'pos-flat';
  document.getElementById('position-ntd').textContent   = '';
  document.getElementById('position-ntd').className     = 'pos-flat';
  document.getElementById('capital-required').textContent = '所需資金：—';
  document.getElementById('trades-content').innerHTML  = '<div style="color:#8b949e;font-size:12px">尚無交易</div>';
  document.getElementById('total-pnl-val').textContent = '0.00%';
  document.getElementById('total-pnl-val').className   = 'pos-flat';
  document.getElementById('total-ntd-val').textContent = '$0';
  document.getElementById('total-ntd-val').className   = 'pos-flat';
  // 恢復 anchor + 時間軸對齊（清空 series 後 LW 會忘記時間範圍）
  if (allBars.length > 0) {
    const subAnchorData = allBars.map(b => ({ time: toTs(b.ts), value: 0 }));
    [volAnchor, macdAnchor, rsiAnchor, obvAnchor].forEach(a => a.setData(subAnchorData));
    const mid = allBars[Math.floor(allBars.length / 2)].close;
    anchorSeries.setData(allBars.map(b => ({ time: toTs(b.ts), value: mid })));
    // 主圖 auto-fit 後同步副圖
    initChartView();
    setTimeout(syncPriceScales, 200);
  }

  const hasData = allBars.length > 0;
  document.getElementById('buy-btn').disabled    = !hasData;
  document.getElementById('sell-btn').disabled   = !hasData;
  document.getElementById('play-btn').disabled   = !hasData;
  document.getElementById('reset-btn').disabled  = !hasData;
  document.getElementById('rewind-btn').disabled = !hasData;
  document.getElementById('play-btn').textContent = '▶ 播放';
  document.getElementById('play-btn').classList.remove('active');
  document.getElementById('current-time').textContent = '--:--';
  updateSignals(Object.fromEntries(SIGNAL_DEFS.map(n => [n, false])), 0, false);
  setStatus(hasData ? `共 ${allBars.length} 根K棒，按播放開始` : '請選擇股票和日期');
}

// ── 從頭重建指定根數的圖表狀態 ─────────────────────────────────────────────
function rebuildBars(count) {
  ema12St = ema26St = signalSt = null;
  rsiGain = rsiLoss = rsiPrevC = null;
  obvVal = 0; obvPrevC = null;
  shownBars = [];

  candleSeries.setData([]);
  ma5Series.setData([]); ma10Series.setData([]); ma20Series.setData([]);
  volSeries.setData([]);
  difSeries.setData([]); macdSignalSeries.setData([]); macdHistSeries.setData([]);
  rsiSeries.setData([]);
  obvSeries.setData([]);

  for (let i = 0; i < count; i++) {
    const bar = allBars[i];
    const ts  = toTs(bar.ts);
    candleSeries.update({ time: ts, open: bar.open, high: bar.high, low: bar.low, close: bar.close });
    volSeries.update({ time: ts, value: bar.volume,
      color: bar.close >= bar.open ? '#f8514966' : '#3fb95066' });
    shownBars.push(bar);
    updateMAs(ts);
    updateMACDNext(ts, bar);
    updateRSINext(ts, bar);
    updateOBVNext(ts, bar);
  }
  candleSeries.setMarkers(_markers);
}

// ── 退一根K棒 ───────────────────────────────────────────────────────────────
function rewindOneBar() {
  if (playIndex <= 0 || !allBars.length) return;
  pause();
  playIndex--;

  // 若持倉是在這根或之後開的，取消持倉（不記錄交易）
  if (position && position.barIndex >= playIndex) {
    position = null;
    updatePosDisplay();
    document.getElementById('position-price').textContent = '無持倉';
    document.getElementById('position-pnl').textContent   = '—';
    document.getElementById('position-pnl').className     = 'pos-flat';
    document.getElementById('position-ntd').textContent   = '';
    document.getElementById('position-ntd').className     = 'pos-flat';
    document.getElementById('buy-btn').disabled  = false;
    document.getElementById('sell-btn').disabled = false;
  }

  // 移除這根 bar 之後的 marker
  if (playIndex > 0) {
    const cutoff = toTs(allBars[playIndex - 1].ts);
    _markers = _markers.filter(m => m.time <= cutoff);
  } else {
    _markers = [];
  }

  rebuildBars(playIndex);

  if (playIndex > 0) {
    const bar = allBars[playIndex - 1];
    document.getElementById('current-time').textContent = bar.ts.substring(11, 16);
    if (position) updatePnl(bar.close);
    if (shownBars.length >= 5) fetchSignals();
    setStatus(`${bar.ts.substring(11,16)}  ${bar.close.toFixed(1)}元  ${playIndex}/${allBars.length}根`);
  } else {
    document.getElementById('current-time').textContent = '--:--';
    updateSignals(Object.fromEntries(SIGNAL_DEFS.map(n => [n, false])), 0, false);
    setStatus(`共 ${allBars.length} 根K棒，按播放開始`);
  }
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
function _clearPosition() {
  position = null;
  updatePosDisplay();
  document.getElementById('position-price').textContent = '無持倉';
  document.getElementById('position-pnl').textContent   = '—';
  document.getElementById('position-pnl').className     = 'pos-flat';
  document.getElementById('position-ntd').textContent   = '';
  document.getElementById('position-ntd').className     = 'pos-flat';
  document.getElementById('buy-btn').disabled  = false;
  document.getElementById('sell-btn').disabled = false;
}

function buy() {
  if (!shownBars.length) return;
  const bar = shownBars[shownBars.length - 1];
  const wasPlaying = isPlaying;
  if (wasPlaying) pause();
  let msg;
  if (position && position.direction === 'short') {
    msg = `回補空單 ${position.lots}張 @ ${bar.close.toFixed(1)} 元`;
  } else if (position && position.direction === 'long') {
    msg = `加碼多單 ${lotSize}張 @ ${bar.close.toFixed(1)} 元`;
  } else {
    msg = `買多 ${lotSize}張 @ ${bar.close.toFixed(1)} 元`;
  }
  showConfirm(msg, () => { _execBuy(); if (wasPlaying) play(); });
}

function _execBuy() {
  if (!shownBars.length) return;
  const bar = shownBars[shownBars.length - 1];
  if (position && position.direction === 'short') {
    // 回補空單
    const { netNTD, pct } = calcPnlDetailsShort(position.price, bar.close, position.lots);
    recordTrade('short', position.time, bar.ts.substring(11,16), position.price, bar.close, pct, netNTD, position.lots);
    addMarker(toTs(bar.ts), 'belowBar', '#f85149', 'arrowUp', '補');
    _clearPosition();
  } else if (position && position.direction === 'long') {
    // 加碼多單（均價攤入）
    const totalCost = position.price * position.lots + bar.close * lotSize;
    position.lots += lotSize;
    position.price = totalCost / position.lots;
    document.getElementById('position-price').textContent = `多 均${position.price.toFixed(1)} 元 ×${position.lots}張`;
    updatePosDisplay();
    addMarker(toTs(bar.ts), 'belowBar', '#f0883e', 'arrowUp', '加');
  } else {
    // 開多
    position = { price: bar.close, time: bar.ts.substring(11,16), barIndex: playIndex - 1, lots: lotSize, direction: 'long' };
    document.getElementById('position-price').textContent = `多 ${bar.close.toFixed(1)} 元 ×${lotSize}張`;
    updatePosDisplay();
    document.getElementById('position-pnl').textContent  = '0%';
    document.getElementById('position-pnl').className    = 'pos-flat';
    document.getElementById('position-ntd').textContent  = '±$0';
    document.getElementById('position-ntd').className    = 'pos-flat';
    document.getElementById('sell-btn').disabled = false;
    addMarker(toTs(bar.ts), 'belowBar', '#f85149', 'arrowUp', '買');
  }
}

function sell() {
  if (!shownBars.length) return;
  const bar = shownBars[shownBars.length - 1];
  const wasPlaying = isPlaying;
  if (wasPlaying) pause();
  let msg;
  if (position && position.direction === 'long') {
    msg = `賣出平多 ${position.lots}張 @ ${bar.close.toFixed(1)} 元`;
  } else if (position && position.direction === 'short') {
    msg = `加碼空單 ${lotSize}張 @ ${bar.close.toFixed(1)} 元`;
  } else {
    msg = `放空 ${lotSize}張 @ ${bar.close.toFixed(1)} 元`;
  }
  showConfirm(msg, () => { _execSell(); if (wasPlaying) play(); });
}

function _execSell() {
  if (!shownBars.length) return;
  const bar = shownBars[shownBars.length - 1];
  if (position && position.direction === 'long') {
    // 平多
    const { netNTD, pct } = calcPnlDetails(position.price, bar.close, position.lots);
    recordTrade('long', position.time, bar.ts.substring(11,16), position.price, bar.close, pct, netNTD, position.lots);
    addMarker(toTs(bar.ts), 'aboveBar', '#3fb950', 'arrowDown', '賣');
    _clearPosition();
  } else if (position && position.direction === 'short') {
    // 加碼空單（均價攤入）
    const totalCost = position.price * position.lots + bar.close * lotSize;
    position.lots += lotSize;
    position.price = totalCost / position.lots;
    document.getElementById('position-price').textContent = `空 均${position.price.toFixed(1)} 元 ×${position.lots}張`;
    updatePosDisplay();
    addMarker(toTs(bar.ts), 'aboveBar', '#58a6ff', 'arrowDown', '加');
  } else {
    // 開空
    position = { price: bar.close, time: bar.ts.substring(11,16), barIndex: playIndex - 1, lots: lotSize, direction: 'short' };
    document.getElementById('position-price').textContent = `空 ${bar.close.toFixed(1)} 元 ×${lotSize}張`;
    updatePosDisplay();
    document.getElementById('position-pnl').textContent  = '0%';
    document.getElementById('position-pnl').className    = 'pos-flat';
    document.getElementById('position-ntd').textContent  = '±$0';
    document.getElementById('position-ntd').className    = 'pos-flat';
    document.getElementById('buy-btn').disabled = false;
    addMarker(toTs(bar.ts), 'aboveBar', '#3fb950', 'arrowDown', '空');
  }
}

function forceClose() {
  if (!position || !shownBars.length) { position = null; return; }
  const bar = shownBars[shownBars.length - 1];
  if (position.direction === 'short') {
    const { netNTD, pct } = calcPnlDetailsShort(position.price, bar.close, position.lots);
    recordTrade('short', position.time, bar.ts.substring(11,16), position.price, bar.close, pct, netNTD, position.lots, true);
  } else {
    const { netNTD, pct } = calcPnlDetails(position.price, bar.close, position.lots);
    recordTrade('long', position.time, bar.ts.substring(11,16), position.price, bar.close, pct, netNTD, position.lots, true);
  }
  position = null;
}

function updatePnl(cur) {
  const { netNTD, pct } = position.direction === 'short'
    ? calcPnlDetailsShort(position.price, cur, position.lots)
    : calcPnlDetails(position.price, cur, position.lots);
  const pEl = document.getElementById('position-pnl');
  const nEl = document.getElementById('position-ntd');
  const cls = pct > 0 ? 'pos-up' : pct < 0 ? 'pos-down' : 'pos-flat';
  pEl.textContent = (pct >= 0 ? '+' : '') + pct.toFixed(1) + '%';
  pEl.className   = cls;
  const ntdSign = netNTD >= 0 ? '+' : '';
  nEl.textContent = `${ntdSign}$${Math.round(netNTD).toLocaleString()}`;
  nEl.className   = cls;
}

function recordTrade(dir, et, xt, ep, xp, pct, netNTD, lots, forced = false) {
  totalPnl += pct;
  totalNTD += netNTD;
  const cls  = pct >= 0 ? 'up' : 'down';
  const sign = pct >= 0 ? '+' : '';
  const ntdSign = netNTD >= 0 ? '+' : '';
  const item = document.createElement('div');
  item.className = 'trade-item';
  const entryLabel = dir === 'short' ? '空' : '買';
  const exitLabel  = dir === 'short' ? '補' : '賣';
  item.innerHTML = `<span>${et}${entryLabel}@${ep.toFixed(1)} ×${lots}張</span><br>
    <span>${xt}${exitLabel}@${xp.toFixed(1)}</span>${forced ? ' <span style="color:#d29922">(強平)</span>' : ''}
    <span class="pnl ${cls}"> ${sign}${pct.toFixed(1)}% / ${ntdSign}$${netNTD.toLocaleString()}</span>`;
  document.getElementById('trades-content').insertBefore(item,
    document.getElementById('trades-content').firstChild);
  const tEl  = document.getElementById('total-pnl-val');
  const tNEl = document.getElementById('total-ntd-val');
  tEl.textContent  = (totalPnl >= 0 ? '+' : '') + totalPnl.toFixed(1) + '%';
  tEl.className    = totalPnl > 0 ? 'pos-up' : totalPnl < 0 ? 'pos-down' : 'pos-flat';
  const tNSign = totalNTD >= 0 ? '+' : '';
  tNEl.textContent = `${tNSign}$${totalNTD.toLocaleString()}`;
  tNEl.className   = totalNTD > 0 ? 'pos-up' : totalNTD < 0 ? 'pos-down' : 'pos-flat';
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
