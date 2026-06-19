from flask import Flask, render_template, request, jsonify
from roar_engine import analyze

app = Flask(__name__)


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/roar', methods=['POST'])
def roar_api():
    data = request.get_json(force=True)
    ticker = (data.get('ticker') or '').strip()
    if not ticker:
        return jsonify({'error': '請輸入股票代號'}), 400
    try:
        result = analyze(ticker)
        return jsonify(result)
    except ValueError as e:
        return jsonify({'error': str(e)}), 422
    except Exception as e:
        return jsonify({'error': f'分析失敗：{e}'}), 500


if __name__ == '__main__':
    app.run(port=5900, debug=False)
