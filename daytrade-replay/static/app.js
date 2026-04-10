// ── 常數 ────────────────────────────────────────────────────────────────────
const COST_RATE = 0.435; // % (手續費 + 當沖證交稅)
const SIGNAL_DEFS = [
  'VWAP突破', 'OBV領先創高', 'KD鈍化≥80', 'MACD0軸上金叉',
  'RSI5穿RSI10', '預估量爆增', '外盤比≥65%', '委買委賣差',
  '昨量單K', '單K倍量', '超越開盤量', '量爆top30', 'MACD背離'
];

// ── 狀態 ────────────────────────────────────────────────────────────────────
let chart, candleSeries, volumeSeries;
let allBars = [];          // 完整一日 1分K
let shownBars = [];        // 已顯示的 K 棒
let playIndex = 0;
let isPlaying = false;
let playTimer = null;
let speed = 1;
let avg5 = 0, yadayVol = 0;

let position = null;       // { price, time }
let trades = [];
let totalPnl = 0;

// ── 初始化圖表 ──────────────────────────────────────────────────────────────
function initChart() {
  const el = document.getElementById('chart');
  chart = LightweightCharts.createChart(el, {
    layout: { background: { color: '#0d1117' }, textColor: '#8b949e' },
    grid: { vertLines: { color: '#21262d' }, horzLines: { color: '#21262d' } },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    rightPriceScale: { borderColor: '#30363d' },
    timeScale: { borderColor: '#30363d', timeVisible: true, secondsVisible: false },
    width: el.clientWidth,
    height: el.clientHeight,
  });

  candleSeries = chart.addCandlestickSeries({
    upColor:   '#f85149',
    downColor: '#3fb950',
    borderUpColor:   '#f85149',
    borderDownColor: '#3fb950',
    wickUpColor:   '#f85149',
    wickDownColor: '#3fb950',
  });

  volumeSeries = chart.addHistogramSeries({
    color: '#30363d',
    priceFormat: { type: 'volume' },
    priceScaleId: 'vol',
  });
  chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });

  // 視窗 resize
  const ro = new ResizeObserver(() => {
    chart.applyOptions({ width: el.clientWidth, height: el.clientHeight });
  });
  ro.observe(el);
}

// ── 訊號燈初始化 ─────────────────────────────────────────────────────────────
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
    row.className = 'signal-row';
    if (val === null) {
      dot.classList.add('na');
    } else if (val === true) {
      dot.classList.add('on');
      row.classList.add('triggered');
    }
  }
  document.getElementById('confidence-text').textContent = confidence + '%';
  document.getElementById('confidence-bar').style.width = confidence + '%';

  const badge = document.getElementById('trigger-badge');
  badge.style.display = trigger ? 'block' : 'none';
}

// ── 股票/日期載入 ─────────────────────────────────────────────────────────────
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
  const res = await fetch('/api/dates?stock=' + stockId);
  const { dates } = await res.json();
  const sel = document.getElementById('date-select');
  sel.innerHTML = '<option value="">選日期...</option>';
  for (const d of dates.reverse()) {
    const opt = document.createElement('option');
    opt.value = d;
    opt.textContent = d;
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

  const res = await fetch(`/api/kbars?stock=${stockId}&date=${dateStr}`);
  const data = await res.json();
  if (data.error) { setStatus('錯誤: ' + data.error); showLoading(false); return; }

  allBars = data.bars;
  avg5 = data.avg5;
  yadayVol = data.yday_vol;

  if (allBars.length === 0) {
    setStatus('該日無資料');
    showLoading(false);
    return;
  }

  // 清除圖表
  candleSeries.setData([]);
  volumeSeries.setData([]);
  shownBars = [];
  playIndex = 0;

  document.getElementById('play-btn').disabled = false;
  document.getElementById('reset-btn').disabled = false;
  document.getElementById('buy-btn').disabled = false;

  showLoading(false);
  setStatus(`共 ${allBars.length} 根K棒，按播放開始`);
}

// ── 回放控制 ─────────────────────────────────────────────────────────────────
function togglePlay() {
  if (isPlaying) pause();
  else play();
}

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
    if (playIndex >= allBars.length) {
      setStatus('回放結束');
      pause();
      // 有持倉強制平倉
      if (position) forceClose();
    }
    return;
  }
  const delay = Math.max(50, 1000 / speed);
  playTimer = setTimeout(advanceBar, delay);
}

async function advanceBar() {
  if (playIndex >= allBars.length) { scheduleNext(); return; }

  const bar = allBars[playIndex];
  const ts  = Math.floor(new Date(bar.ts).getTime() / 1000);

  const candle = { time: ts, open: bar.open, high: bar.high, low: bar.low, close: bar.close };
  const vol    = { time: ts, value: bar.volume, color: bar.close >= bar.open ? '#f8514966' : '#3fb95066' };

  candleSeries.update(candle);
  volumeSeries.update(vol);
  shownBars.push(bar);
  playIndex++;

  // 更新時間顯示
  const timeStr = bar.ts.substring(11, 16);
  document.getElementById('current-time').textContent = timeStr;

  // 更新即時損益
  if (position) updatePnl(bar.close);

  // 呼叫訊號 API（每根都算，速度快時可降頻）
  if (shownBars.length >= 5) {
    fetchSignals();
  }

  setStatus(`${timeStr}  ${bar.close.toFixed(1)} 元  已播 ${playIndex}/${allBars.length} 根`);
  scheduleNext();
}

function setSpeed(s) {
  speed = s;
  ['1','3','10','30'].forEach(x => {
    document.getElementById('s'+x).classList.toggle('active', parseInt(x) === s);
  });
}

function resetReplay() {
  pause();
  playIndex = 0;
  shownBars = [];
  allBars = [];
  candleSeries && candleSeries.setData([]);
  volumeSeries && volumeSeries.setData([]);
  position = null;
  document.getElementById('position-price').textContent = '無持倉';
  document.getElementById('position-pnl').textContent = '—';
  document.getElementById('position-pnl').className = 'pos-flat';
  document.getElementById('buy-btn').disabled = true;
  document.getElementById('sell-btn').disabled = true;
  document.getElementById('play-btn').disabled = true;
  document.getElementById('play-btn').textContent = '▶ 播放';
  document.getElementById('play-btn').classList.remove('active');
  document.getElementById('current-time').textContent = '--:--';
  updateSignals(Object.fromEntries(SIGNAL_DEFS.map(n => [n, false])), 0, false);
  setStatus('請選擇股票和日期');
  // 不清交易紀錄，方便同一日多次練習
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
    // 標記進出場點
    if (data.trigger && shownBars.length > 0) {
      const last = shownBars[shownBars.length - 1];
      const ts = Math.floor(new Date(last.ts).getTime() / 1000);
      candleSeries.setMarkers(
        (candleSeries.markers ? candleSeries.markers() : []).concat({
          time: ts, position: 'belowBar', color: '#f0883e',
          shape: 'arrowUp', text: '⚡',
        })
      );
    }
  } catch(e) {
    // 靜默失敗
  } finally {
    sigPending = false;
  }
}

// ── 手動交易 ─────────────────────────────────────────────────────────────────
function buy() {
  if (!isPlaying && playIndex === 0) return;
  if (position) return; // 已有持倉

  const bar = shownBars[shownBars.length - 1];
  if (!bar) return;

  position = { price: bar.close, time: bar.ts.substring(11, 16) };
  document.getElementById('position-price').textContent =
    `均價 ${bar.close.toFixed(2)} 元`;
  document.getElementById('position-pnl').textContent = '0.00%';
  document.getElementById('position-pnl').className = 'pos-flat';
  document.getElementById('buy-btn').disabled = true;
  document.getElementById('sell-btn').disabled = false;

  // 標記買入點
  const ts = Math.floor(new Date(bar.ts).getTime() / 1000);
  addMarker(ts, 'belowBar', '#f85149', 'arrowUp', '買');
}

function sell() {
  if (!position) return;
  const bar = shownBars[shownBars.length - 1];
  if (!bar) return;

  const pnl = calcPnl(position.price, bar.close);
  recordTrade(position.time, bar.ts.substring(11, 16), position.price, bar.close, pnl);

  // 標記賣出點
  const ts = Math.floor(new Date(bar.ts).getTime() / 1000);
  addMarker(ts, 'aboveBar', '#3fb950', 'arrowDown', '賣');

  position = null;
  document.getElementById('position-price').textContent = '無持倉';
  document.getElementById('position-pnl').textContent = '—';
  document.getElementById('position-pnl').className = 'pos-flat';
  document.getElementById('buy-btn').disabled = false;
  document.getElementById('sell-btn').disabled = true;
}

function forceClose() {
  if (!position) return;
  const bar = shownBars[shownBars.length - 1];
  if (!bar) { position = null; return; }
  const pnl = calcPnl(position.price, bar.close);
  recordTrade(position.time, bar.ts.substring(11, 16), position.price, bar.close, pnl, true);
  position = null;
}

function calcPnl(entry, exit) {
  return ((exit - entry) / entry * 100) - COST_RATE;
}

function updatePnl(curPrice) {
  if (!position) return;
  const unrealized = (curPrice - position.price) / position.price * 100 - COST_RATE;
  const el = document.getElementById('position-pnl');
  el.textContent = (unrealized >= 0 ? '+' : '') + unrealized.toFixed(2) + '%';
  el.className = unrealized > 0 ? 'pos-up' : unrealized < 0 ? 'pos-down' : 'pos-flat';
}

function recordTrade(entryTime, exitTime, entryPrice, exitPrice, pnl, forced = false) {
  totalPnl += pnl;
  trades.push({ entryTime, exitTime, entryPrice, exitPrice, pnl, forced });

  const content = document.getElementById('trades-content');
  const dir = pnl >= 0 ? 'up' : 'down';
  const sign = pnl >= 0 ? '+' : '';
  const item = document.createElement('div');
  item.className = 'trade-item';
  item.innerHTML = `
    <span>${entryTime}買@${entryPrice.toFixed(1)}</span><br>
    <span>${exitTime}賣@${exitPrice.toFixed(1)}</span>
    ${forced ? '<span style="color:#d29922"> (強平)</span>' : ''}
    <span class="pnl ${dir}"> ${sign}${pnl.toFixed(2)}%</span>
  `;
  content.insertBefore(item, content.firstChild);

  const tEl = document.getElementById('total-pnl-val');
  const sign2 = totalPnl >= 0 ? '+' : '';
  tEl.textContent = sign2 + totalPnl.toFixed(2) + '%';
  tEl.className = totalPnl > 0 ? 'pos-up' : totalPnl < 0 ? 'pos-down' : 'pos-flat';
}

// ── K棒標記 ──────────────────────────────────────────────────────────────────
let _markers = [];
function addMarker(time, position, color, shape, text) {
  _markers.push({ time, position, color, shape, text });
  // 排序後設定（lightweight-charts 要求時間遞增）
  _markers.sort((a, b) => a.time - b.time);
  candleSeries.setMarkers(_markers);
}

// ── 工具函數 ─────────────────────────────────────────────────────────────────
function setStatus(msg) {
  document.getElementById('status-bar').textContent = msg;
}

function showLoading(show) {
  document.getElementById('loading').classList.toggle('show', show);
}

// ── 啟動 ─────────────────────────────────────────────────────────────────────
initChart();
initSignals();
loadStocks();
