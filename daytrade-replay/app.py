#!/usr/bin/env python3
"""台股當沖回放模擬系統 — port 5400"""

from flask import Flask, jsonify, render_template, request
import data
import signals as sig_mod

app = Flask(__name__)


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/stocks')
def api_stocks():
    try:
        stocks = data.get_top30_stocks()
        return jsonify({'stocks': stocks})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/kbars')
def api_kbars():
    stock_id = request.args.get('stock', '')
    date_str = request.args.get('date', '')
    if not stock_id or not date_str:
        return jsonify({'error': 'stock and date required'}), 400

    try:
        bars = data.get_1min_kbars(stock_id, date_str)
        avg5, yday_vol, yday_high, yday_low, yday_close = data.get_daily_stats(stock_id, date_str)
        return jsonify({'bars': bars, 'avg5': avg5, 'yday_vol': yday_vol,
                        'yday_high': yday_high, 'yday_low': yday_low, 'yday_close': yday_close})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/signals', methods=['POST'])
def api_signals():
    body = request.get_json()
    if not body:
        return jsonify({'error': 'JSON body required'}), 400

    bars     = body.get('bars', [])
    avg5     = int(body.get('avg5', 0))
    yday_vol = int(body.get('yday_vol', 0))

    try:
        result = sig_mod.run_signals(bars, avg5, yday_vol)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/dates')
def api_dates():
    stock_id = request.args.get('stock', '')
    if not stock_id:
        return jsonify({'error': 'stock required'}), 400
    try:
        dates = data.get_available_dates(stock_id)
        return jsonify({'dates': dates})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/index_data')
def api_index_data():
    stock_id = request.args.get('stock', '')
    date_str = request.args.get('date', '')
    if not stock_id or not date_str:
        return jsonify({'error': 'stock and date required'}), 400
    try:
        taiex_bars = data.get_taiex_1min(date_str)
        industry   = data.get_stock_sector(stock_id)
        sector     = data.get_sector_1min(industry, date_str)
        return jsonify({'taiex_bars': taiex_bars, 'industry': industry, 'sector': sector})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/subscribe', methods=['POST'])
def api_subscribe():
    body = request.get_json() or {}
    stock_id = body.get('stock', '')
    if not stock_id:
        return jsonify({'error': 'stock required'}), 400
    try:
        data.subscribe_realtime(stock_id)
        return jsonify({'ok': True, 'stock': stock_id})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    print('[daytrade-replay] 啟動 http://localhost:5400')
    app.run(host='0.0.0.0', port=5400, debug=False)
