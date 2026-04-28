from flask import Flask, request, Response, render_template, jsonify
from browser import manager, SERVICES
import json
import queue
import atexit

app = Flask(__name__)


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/services')
def get_services():
    return jsonify(SERVICES)


@app.route('/api/status')
def get_status():
    try:
        return jsonify(manager.get_status())
    except Exception:
        return jsonify({sid: {"opened": False, "logged_in": False} for sid in SERVICES})


@app.route('/api/open/<sid>', methods=['POST'])
def open_service(sid):
    manager.open_service(sid)
    return jsonify({'ok': True})


@app.route('/api/new-chat/<sid>', methods=['POST'])
def new_chat(sid):
    manager.new_chat(sid)
    return jsonify({'ok': True})


@app.route('/api/ask', methods=['POST'])
def ask():
    data = request.json or {}
    message = data.get('message', '').strip()
    sid = data.get('service', '')
    new_chat_flag = data.get('new_chat', False)

    if not message or sid not in SERVICES:
        return jsonify({'error': 'invalid'}), 400

    q = queue.Queue()
    manager.send_message(sid, message, q, new_chat=new_chat_flag)

    def generate():
        while True:
            try:
                item = q.get(timeout=130)
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
                if item['type'] in ('done', 'error'):
                    break
            except queue.Empty:
                yield f"data: {json.dumps({'type': 'error', 'content': '逾時'})}\n\n"
                break

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


if __name__ == '__main__':
    atexit.register(manager.stop)
    manager.start()
    print()
    print("  ⚡ AI Arena 已啟動 → http://localhost:5050")
    print("  📋 請在彈出的瀏覽器中登入各 AI 服務，然後回到網頁開始使用")
    print()
    app.run(host='0.0.0.0', debug=False, port=5050, threaded=True)
