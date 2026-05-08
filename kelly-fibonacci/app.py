from flask import Flask, render_template, request, jsonify, send_file
import io
import yfinance as yf
from kelly import raw_kelly, adjusted_kelly, position_sizes
from fibonacci import get_fib_params
from cycles import resonance_score
from montecarlo import run_simulation, simulation_stats
from excel_builder import build_excel

app = Flask(__name__)


def _compute(data: dict) -> tuple[dict, dict, object]:
    """Shared calculation pipeline. Returns (params, results, curves)."""
    capital = float(data['capital'])
    win_rate = float(data['win_rate']) / 100.0
    fib_level = float(data['fib_level'])
    kitchin = float(data['kitchin'])
    juglar = float(data['juglar'])
    kuznets = float(data['kuznets'])
    kondratiev = float(data['kondratiev'])

    fib_params = get_fib_params(fib_level)
    odds = fib_params['reward_ratio']
    fib_multiplier = fib_params['entry_multiplier']

    cycle_score, cycle_multiplier = resonance_score(kitchin, juglar, kuznets, kondratiev)
    raw_k = raw_kelly(win_rate, odds)
    adj_k = adjusted_kelly(win_rate, odds, cycle_multiplier, fib_multiplier)

    params = {
        'capital': capital, 'win_rate': win_rate * 100, 'odds': odds,
        'fib_level': fib_level, 'kitchin': kitchin, 'juglar': juglar,
        'kuznets': kuznets, 'kondratiev': kondratiev,
    }
    results_base = {
        'raw_kelly': round(raw_k * 100, 2),
        'odds': odds,
        'fib_multiplier': fib_multiplier,
        'cycle_score': cycle_score,
        'cycle_multiplier': cycle_multiplier,
        'adj_kelly': round(adj_k * 100, 2),
    }

    if adj_k <= 0:
        results_base['full_kelly_amt'] = 0.0
        results_base['half_kelly_amt'] = 0.0
        results_base['no_edge'] = True
        return params, results_base, None

    sizes = position_sizes(capital, adj_k)
    curves = run_simulation(win_rate, adj_k, capital, n_trades=200, n_simulations=1000)
    stats = simulation_stats(curves, capital)

    results_base['full_kelly_amt'] = sizes['full_kelly']
    results_base['half_kelly_amt'] = sizes['half_kelly']
    results_base['stats'] = stats
    results_base['no_edge'] = False

    return params, results_base, curves


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/calculate', methods=['POST'])
def calculate():
    data = request.get_json()
    if not data:
        return jsonify({'error': 'invalid request'}), 400
    try:
        params, results, curves = _compute(data)
    except (KeyError, ValueError) as e:
        return jsonify({'error': str(e)}), 400
    return jsonify(results)


@app.route('/download', methods=['POST'])
def download():
    data = request.get_json()
    if not data:
        return jsonify({'error': 'invalid request'}), 400
    try:
        params, results, curves = _compute(data)
    except (KeyError, ValueError) as e:
        return jsonify({'error': str(e)}), 400
    if curves is None:
        return jsonify({'error': 'no edge: Kelly fraction is non-positive'}), 400

    xlsx_bytes = build_excel(params, results, curves)
    return send_file(
        io.BytesIO(xlsx_bytes),
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name='kelly_fibonacci_model.xlsx',
    )


@app.route('/stock-info', methods=['POST'])
def stock_info():
    data = request.get_json()
    symbol = (data.get('symbol') or '').strip()
    if not symbol:
        return jsonify({'error': 'symbol required'}), 400

    ticker_sym = symbol + '.TW' if symbol.isdigit() else symbol
    try:
        ticker = yf.Ticker(ticker_sym)
        hist = ticker.history(period='3mo')
        if hist.empty:
            return jsonify({'error': f'找不到 {ticker_sym}'}), 404
        return jsonify({
            'symbol': ticker_sym,
            'current': round(float(hist['Close'].iloc[-1]), 2),
            'high': round(float(hist['High'].max()), 2),
            'low': round(float(hist['Low'].min()), 2),
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


FIB_LEVELS = [23.6, 38.2, 50.0, 61.8, 78.6]
SWING_WINDOW = 10      # 左右各 10 根判斷波段頂底
MAX_LOOKFORWARD = 60   # 最多往後看 60 根判斷勝負


def _detect_swings(prices: list[float], window: int) -> tuple[list[int], list[int]]:
    highs, lows = [], []
    for i in range(window, len(prices) - window):
        chunk = prices[i - window: i + window + 1]
        if prices[i] == max(chunk):
            highs.append(i)
        if prices[i] == min(chunk):
            lows.append(i)
    return highs, lows


def _backtest_symbol(ticker_sym: str) -> dict:
    df = yf.download(ticker_sym, period='2y', progress=False, auto_adjust=True)
    if df.empty:
        raise ValueError(f'找不到 {ticker_sym}')

    close = df[('Close', ticker_sym)].dropna()
    prices = close.tolist()

    swing_highs, swing_lows = _detect_swings(prices, SWING_WINDOW)

    stats: dict[str, dict] = {str(lvl): {'wins': 0, 'losses': 0, 'reward': [], 'risk': []} for lvl in FIB_LEVELS}

    for hi in swing_highs:
        prior_lows = [l for l in swing_lows if l < hi]
        if not prior_lows:
            continue
        lo = max(prior_lows)
        swing_h = prices[hi]
        swing_l = prices[lo]

        if (swing_h - swing_l) / swing_l < 0.05:   # 漲幅 < 5% 的波段忽略
            continue

        stop = swing_l * 0.99

        for fib in FIB_LEVELS:
            key = str(fib)
            entry = swing_h - (swing_h - swing_l) * fib / 100
            if entry <= stop:
                continue

            target = swing_h
            reward_pct = (target - entry) / entry * 100
            risk_pct = (entry - stop) / entry * 100

            triggered = False
            outcome = None
            for j in range(hi + 1, min(hi + 1 + MAX_LOOKFORWARD * 2, len(prices))):
                p = prices[j]
                if not triggered:
                    if p <= entry:
                        triggered = True
                else:
                    if p >= target:
                        outcome = 'win'
                        break
                    if p <= stop:
                        outcome = 'loss'
                        break

            if outcome == 'win':
                stats[key]['wins'] += 1
                stats[key]['reward'].append(reward_pct)
                stats[key]['risk'].append(risk_pct)
            elif outcome == 'loss':
                stats[key]['losses'] += 1
                stats[key]['reward'].append(reward_pct)
                stats[key]['risk'].append(risk_pct)

    levels = {}
    best_fib, best_ev = None, -999
    for key, s in stats.items():
        total = s['wins'] + s['losses']
        if total < 3:
            levels[key] = {'win_rate': None, 'wins': s['wins'], 'losses': s['losses'],
                           'total': total, 'odds': None, 'ev': None}
            continue
        wr = s['wins'] / total
        avg_reward = sum(s['reward']) / len(s['reward'])
        avg_risk = sum(s['risk']) / len(s['risk'])
        odds = round(avg_reward / avg_risk, 2) if avg_risk else 0
        ev = round(wr * odds - (1 - wr), 3)
        levels[key] = {
            'win_rate': round(wr * 100, 1),
            'wins': s['wins'], 'losses': s['losses'], 'total': total,
            'odds': odds, 'ev': ev,
        }
        if ev > best_ev:
            best_ev = ev
            best_fib = key

    return {'levels': levels, 'best_fib': best_fib, 'symbol': ticker_sym,
            'total_swings': len(swing_highs)}


@app.route('/backtest', methods=['POST'])
def backtest():
    data = request.get_json()
    symbol = (data.get('symbol') or '').strip()
    if not symbol:
        return jsonify({'error': 'symbol required'}), 400
    ticker_sym = symbol + '.TW' if symbol.isdigit() else symbol
    try:
        result = _backtest_symbol(ticker_sym)
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    return jsonify(result)


@app.route('/market-preset', methods=['GET'])
def market_preset():
    return jsonify({
        'kitchin': 1.0,
        'juglar': 1.5,
        'kuznets': -0.5,
        'kondratiev': 1.5,
        'reasoning': {
            'kitchin': '2026 Q2：全球 PMI 回升，企業補庫存循環啟動，AI 硬體需求旺盛 → +1.0',
            'juglar': 'AI 資本支出潮（Nvidia/超大規模資料中心）帶動固定資產投資高峰 → +1.5',
            'kuznets': '台灣/日本/中國人口老化壓力持續，美國住宅市場偏緊但動能受限 → −0.5',
            'kondratiev': 'AI 技術革命早期擴散期（2020s），類比 1990s 網路浪潮初期 → +1.5',
        },
    })


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5700, debug=False)
