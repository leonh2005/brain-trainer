from flask import Flask, render_template, request, jsonify, send_file
import io
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


if __name__ == '__main__':
    app.run(port=5400, debug=True)
