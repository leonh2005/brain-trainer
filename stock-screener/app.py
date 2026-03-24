from flask import Flask, render_template, jsonify
from shioaji_client import get_snapshot_all, get_daily_candles, get_intraday_candles
from indicators import build_chart_payload, detect_signals
from dotenv import load_dotenv
import os

load_dotenv()

app = Flask(__name__)


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/radar')
def radar():
    """總成交量前 20（不限價格）"""
    stocks = get_snapshot_all()

    result = []
    for s in stocks:
        price = s.get('closePrice') or 0
        vol   = s.get('tradeVolume') or 0
        sym   = s.get('symbol', '')
        if not sym or price <= 0 or vol <= 0:
            continue
        result.append({
            'symbol':     sym,
            'name':       s.get('name', ''),
            'price':      price,
            'change_pct': s.get('changePercent') or 0,
            'volume':     vol,
        })

    result.sort(key=lambda x: x['volume'], reverse=True)
    return jsonify(result[:20])


@app.route('/api/stock/<symbol>/daily')
def stock_daily(symbol):
    df = get_daily_candles(symbol, days=90)
    if df.empty:
        return jsonify({'error': 'no data'}), 404

    payload = build_chart_payload(df, time_col='date', is_intraday=False)
    payload['signals'] = detect_signals(df)
    return jsonify(payload)


@app.route('/api/stock/<symbol>/intraday')
def stock_intraday(symbol):
    df = get_intraday_candles(symbol)
    if df.empty:
        return jsonify({'error': 'no data'}), 404

    payload = build_chart_payload(df, time_col='datetime', is_intraday=True)
    # 分K 不重算四位一體（樣本太少），只算攻擊量
    if len(df) >= 2:
        last_vol = float(df['volume'].iloc[-1])
        avg_vol = float(df['volume'].mean())
        payload['signals'] = {
            'four_in_one': False,
            'vol_attack': last_vol >= avg_vol * 2,
            'obv_breakout': False,
            'macd_ok': False,
            'rsi_ok': False,
            'trap': None,
        }
    return jsonify(payload)


if __name__ == '__main__':
    app.run(debug=True, port=5001, use_reloader=False)
