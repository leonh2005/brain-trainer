#!/usr/bin/env python3
"""
兔子照護系統 - Flask Web App
"""

import os
import io
import re
import csv
import json
import sqlite3
import requests
from datetime import datetime, date
from functools import wraps
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, Response, send_from_directory
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'rabbit-care-secret-2026')
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'rabbit.db')
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
LOGIN_PASSWORD = os.getenv('LOGIN_PASSWORD', 'momo2026')
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


def write_login_required(f):
    """GET 公開，POST/DELETE/PUT 需登入"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if request.method != 'GET' and not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


# ── Telegram ────────────────────────────────────────────────

def send_telegram(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage',
            json={'chat_id': TELEGRAM_CHAT_ID, 'text': text, 'parse_mode': 'HTML'},
            timeout=10
        )
    except Exception:
        pass


# ── 資料庫初始化 ──────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS rabbit (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                breed TEXT,
                birthday TEXT,
                gender TEXT,
                habits TEXT,
                photo TEXT,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS daily_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                log_date TEXT NOT NULL,
                weight REAL,
                food TEXT,
                water TEXT,
                poop TEXT,
                health_note TEXT,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS vet_visit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                visit_date TEXT NOT NULL,
                symptoms TEXT,
                diagnosis TEXT,
                treatment TEXT,
                vet_name TEXT,
                note TEXT,
                next_visit TEXT,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS medication (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                dose TEXT,
                frequency TEXT,
                start_date TEXT NOT NULL,
                end_date TEXT,
                note TEXT,
                active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS action_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                log_date TEXT NOT NULL,
                action TEXT NOT NULL,
                confidence REAL NOT NULL,
                timestamp TEXT NOT NULL,
                screenshot TEXT,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS water_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                log_date TEXT NOT NULL,
                log_time TEXT NOT NULL,
                amount_cc INTEGER NOT NULL,
                note TEXT,
                created_at TEXT DEFAULT (datetime('now','localtime'))
            );
        ''')
        # 補欄位（舊資料庫升級）
        try:
            conn.execute('ALTER TABLE rabbit ADD COLUMN photo TEXT')
        except Exception:
            pass
        try:
            conn.execute('ALTER TABLE vet_visit ADD COLUMN next_visit TEXT')
        except Exception:
            pass
        try:
            conn.execute('ALTER TABLE action_log ADD COLUMN screenshot TEXT')
        except Exception:
            pass


# ── 工具函式 ─────────────────────────────────────────────────

def get_rabbit():
    with get_db() as conn:
        return conn.execute('SELECT * FROM rabbit WHERE id=1').fetchone()


def calc_age(birthday_str):
    if not birthday_str:
        return ''
    try:
        bd = datetime.strptime(birthday_str, '%Y-%m-%d').date()
        today = date.today()
        years = today.year - bd.year - ((today.month, today.day) < (bd.month, bd.day))
        months = (today.year - bd.year) * 12 + today.month - bd.month
        if today.day < bd.day:
            months -= 1
        if years >= 1:
            return f'{years} 歲 {months % 12} 個月'
        return f'{months} 個月'
    except Exception:
        return ''


def build_context():
    """組建給 AI 的完整紀錄背景"""
    rabbit = get_rabbit()
    if not rabbit:
        return '目前無兔子資料。'

    age = calc_age(rabbit['birthday'])
    ctx = f"【兔子基本資料】\n名字：{rabbit['name']}\n品種：{rabbit['breed'] or '不明'}\n生日：{rabbit['birthday'] or '不明'}（{age}）\n性別：{rabbit['gender'] or '不明'}\n習慣：{rabbit['habits'] or '無記錄'}\n\n"

    with get_db() as conn:
        logs = conn.execute(
            'SELECT * FROM daily_log ORDER BY log_date DESC LIMIT 14'
        ).fetchall()
        visits = conn.execute(
            'SELECT * FROM vet_visit ORDER BY visit_date DESC LIMIT 5'
        ).fetchall()
        meds = conn.execute(
            "SELECT * FROM medication WHERE active=1 ORDER BY start_date DESC"
        ).fetchall()

    if logs:
        ctx += '【近 14 天日誌】\n'
        for log in logs:
            ctx += f"- {log['log_date']}：體重 {log['weight'] or '-'}kg，食物 {log['food'] or '-'}，水 {log['water'] or '-'}，排便 {log['poop'] or '-'}，觀察 {log['health_note'] or '-'}\n"
        ctx += '\n'

    if visits:
        ctx += '【近期就醫紀錄】\n'
        for v in visits:
            ctx += f"- {v['visit_date']}：{v['symptoms'] or '-'} → 診斷：{v['diagnosis'] or '-'}，治療：{v['treatment'] or '-'}\n"
        ctx += '\n'

    if meds:
        ctx += '【目前用藥】\n'
        for m in meds:
            ctx += f"- {m['name']}：{m['dose'] or '-'}，頻率：{m['frequency'] or '-'}，{m['start_date']}～{m['end_date'] or '持續中'}\n"

    return ctx


def get_reminders():
    """取得提醒事項列表"""
    reminders = []
    today = date.today()

    with get_db() as conn:
        # 即將到期的藥物（3天內）
        meds = conn.execute(
            "SELECT * FROM medication WHERE active=1 AND end_date IS NOT NULL AND end_date != ''"
        ).fetchall()
        for m in meds:
            try:
                end = datetime.strptime(m['end_date'], '%Y-%m-%d').date()
                days_left = (end - today).days
                if days_left < 0:
                    reminders.append({'type': 'danger', 'icon': '💊', 'text': f"{m['name']} 已於 {m['end_date']} 到期，請確認是否需要繼續用藥"})
                elif days_left <= 3:
                    reminders.append({'type': 'warning', 'icon': '💊', 'text': f"{m['name']} 還有 {days_left} 天到期（{m['end_date']}）"})
            except Exception:
                pass

        # 下次就醫提醒
        next_visit = conn.execute(
            "SELECT next_visit FROM vet_visit WHERE next_visit IS NOT NULL AND next_visit != '' ORDER BY next_visit ASC LIMIT 1"
        ).fetchone()
        if next_visit:
            try:
                nv = datetime.strptime(next_visit['next_visit'], '%Y-%m-%d').date()
                days_left = (nv - today).days
                if days_left < 0:
                    reminders.append({'type': 'warning', 'icon': '🏥', 'text': f"預約回診日 {next_visit['next_visit']} 已過，請確認是否已就醫')"})
                elif days_left <= 7:
                    reminders.append({'type': 'info', 'icon': '🏥', 'text': f"預約回診：{next_visit['next_visit']}（{days_left} 天後）"})
            except Exception:
                pass

        # 超過 3 天沒記日誌
        last_log = conn.execute(
            'SELECT log_date FROM daily_log ORDER BY log_date DESC LIMIT 1'
        ).fetchone()
        if last_log:
            try:
                last = datetime.strptime(last_log['log_date'], '%Y-%m-%d').date()
                gap = (today - last).days
                if gap >= 3:
                    reminders.append({'type': 'warning', 'icon': '📋', 'text': f"已有 {gap} 天未記錄日誌，上次：{last_log['log_date']}"})
            except Exception:
                pass

    return reminders


# ── 路由：登入 ──────────────────────────────────────────────

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        if request.form.get('password') == LOGIN_PASSWORD:
            session['logged_in'] = True
            return redirect(url_for('index'))
        error = '密碼錯誤，請再試一次'
    return render_template('login.html', error=error)


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ── 路由：首頁 ──────────────────────────────────────────────

@app.route('/')
def index():
    rabbit = get_rabbit()
    age = calc_age(rabbit['birthday']) if rabbit else ''
    with get_db() as conn:
        recent_logs = conn.execute(
            'SELECT * FROM daily_log ORDER BY log_date DESC LIMIT 7'
        ).fetchall()
        recent_water = conn.execute(
            '''SELECT log_date, SUM(amount_cc) as total_cc
               FROM water_log
               GROUP BY log_date
               ORDER BY log_date DESC LIMIT 7'''
        ).fetchall()
        weight_data = conn.execute(
            'SELECT log_date, weight FROM daily_log WHERE weight IS NOT NULL ORDER BY log_date ASC LIMIT 30'
        ).fetchall()
        active_meds = conn.execute(
            "SELECT * FROM medication WHERE active=1 ORDER BY start_date DESC LIMIT 5"
        ).fetchall()
        today_actions_raw = conn.execute(
            'SELECT id, action, timestamp, screenshot FROM action_log WHERE log_date=? ORDER BY timestamp DESC',
            (date.today().isoformat(),)
        ).fetchall()
    weight_labels = [r['log_date'] for r in weight_data]
    weight_values = [r['weight'] for r in weight_data]
    reminders = get_reminders()
    action_labels = {'eating': '🍽 吃飯', 'drinking': '💧 喝水', 'stretching': '🐇 伸懶腰', 'sleeping': '😴 睡覺'}
    today_action_list = [
        {
            'id': r['id'],
            'label': action_labels.get(r['action'], r['action']),
            'time': r['timestamp'][11:16],
            'screenshot': r['screenshot']
        }
        for r in today_actions_raw
    ]
    today_action_summary = {}
    for row in today_actions_raw:
        a = row['action']
        if a not in today_action_summary:
            today_action_summary[a] = {'label': action_labels.get(a, a), 'count': 0}
        today_action_summary[a]['count'] += 1
    return render_template('index.html',
        rabbit=rabbit, age=age,
        recent_logs=recent_logs,
        recent_water=recent_water,
        active_meds=active_meds,
        weight_labels=json.dumps(weight_labels),
        weight_values=json.dumps(weight_values),
        today=date.today().isoformat(),
        reminders=reminders,
        today_action_summary=today_action_summary,
        today_action_list=today_action_list,
    )


# ── 路由：兔子資料 ───────────────────────────────────────────

@app.route('/rabbit', methods=['GET', 'POST'])
@write_login_required
def rabbit_profile():
    if request.method == 'POST':
        data = request.form
        photo_filename = None

        # 處理照片上傳
        if 'photo' in request.files:
            file = request.files['photo']
            if file and file.filename:
                ext = file.filename.rsplit('.', 1)[-1].lower()
                if ext in ('jpg', 'jpeg', 'png', 'gif', 'webp'):
                    photo_filename = f'rabbit_{date.today().isoformat()}.{ext}'
                    file.save(os.path.join(UPLOAD_FOLDER, photo_filename))

        with get_db() as conn:
            existing = conn.execute('SELECT id, photo FROM rabbit WHERE id=1').fetchone()
            if existing:
                if not photo_filename:
                    photo_filename = existing['photo']
                conn.execute('''
                    UPDATE rabbit SET name=?,breed=?,birthday=?,gender=?,habits=?,photo=?,updated_at=?
                    WHERE id=1
                ''', (data['name'], data['breed'], data['birthday'],
                      data['gender'], data['habits'], photo_filename,
                      datetime.now().strftime('%Y-%m-%d %H:%M')))
            else:
                conn.execute('''
                    INSERT INTO rabbit (id,name,breed,birthday,gender,habits,photo,updated_at)
                    VALUES (1,?,?,?,?,?,?,?)
                ''', (data['name'], data['breed'], data['birthday'],
                      data['gender'], data['habits'], photo_filename,
                      datetime.now().strftime('%Y-%m-%d %H:%M')))
        return redirect(url_for('index'))
    rabbit = get_rabbit()
    return render_template('rabbit_profile.html', rabbit=rabbit)


@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)


# ── 路由：日誌 ──────────────────────────────────────────────

@app.route('/log', methods=['GET', 'POST'])
@write_login_required
def daily_log():
    if request.method == 'POST':
        data = request.form
        with get_db() as conn:
            conn.execute('''
                INSERT INTO daily_log (log_date, weight, food, water, poop, health_note)
                VALUES (?,?,?,?,?,?)
            ''', (
                data['log_date'],
                float(data['weight']) if data.get('weight') else None,
                data.get('food'), data.get('water'),
                data.get('poop'), data.get('health_note')
            ))
        return redirect(url_for('index'))
    today = date.today().isoformat()
    with get_db() as conn:
        logs = conn.execute(
            'SELECT * FROM daily_log ORDER BY log_date DESC LIMIT 30'
        ).fetchall()
    return render_template('daily_log.html', today=today, logs=logs)


@app.route('/log/<int:log_id>/edit', methods=['POST'])
@login_required
def edit_log(log_id):
    data = request.form
    with get_db() as conn:
        conn.execute('''
            UPDATE daily_log SET log_date=?,weight=?,food=?,water=?,poop=?,health_note=?
            WHERE id=?
        ''', (
            data['log_date'],
            float(data['weight']) if data.get('weight') else None,
            data.get('food'), data.get('water'),
            data.get('poop'), data.get('health_note'),
            log_id
        ))
    return redirect(url_for('daily_log'))


@app.route('/log/<int:log_id>/delete', methods=['POST'])
@login_required
def delete_log(log_id):
    with get_db() as conn:
        conn.execute('DELETE FROM daily_log WHERE id=?', (log_id,))
    return redirect(url_for('daily_log'))


# ── 路由：就醫紀錄 ───────────────────────────────────────────

@app.route('/vet', methods=['GET', 'POST'])
@write_login_required
def vet_visit():
    if request.method == 'POST':
        data = request.form
        with get_db() as conn:
            conn.execute('''
                INSERT INTO vet_visit (visit_date, symptoms, diagnosis, treatment, vet_name, note, next_visit)
                VALUES (?,?,?,?,?,?,?)
            ''', (
                data['visit_date'], data.get('symptoms'), data.get('diagnosis'),
                data.get('treatment'), data.get('vet_name'), data.get('note'),
                data.get('next_visit') or None
            ))
        return redirect(url_for('vet_visit'))
    with get_db() as conn:
        visits = conn.execute(
            'SELECT * FROM vet_visit ORDER BY visit_date DESC'
        ).fetchall()
    return render_template('vet_visit.html', today=date.today().isoformat(), visits=visits)


@app.route('/vet/<int:visit_id>/edit', methods=['POST'])
@login_required
def edit_vet(visit_id):
    data = request.form
    with get_db() as conn:
        conn.execute('''
            UPDATE vet_visit SET visit_date=?,symptoms=?,diagnosis=?,treatment=?,vet_name=?,note=?,next_visit=?
            WHERE id=?
        ''', (
            data['visit_date'], data.get('symptoms'), data.get('diagnosis'),
            data.get('treatment'), data.get('vet_name'), data.get('note'),
            data.get('next_visit') or None,
            visit_id
        ))
    return redirect(url_for('vet_visit'))


@app.route('/vet/<int:visit_id>/delete', methods=['POST'])
@login_required
def delete_vet(visit_id):
    with get_db() as conn:
        conn.execute('DELETE FROM vet_visit WHERE id=?', (visit_id,))
    return redirect(url_for('vet_visit'))


# ── 路由：藥物紀錄 ───────────────────────────────────────────

@app.route('/med', methods=['GET', 'POST'])
@write_login_required
def medication():
    if request.method == 'POST':
        data = request.form
        with get_db() as conn:
            conn.execute('''
                INSERT INTO medication (name, dose, frequency, start_date, end_date, note, active)
                VALUES (?,?,?,?,?,?,1)
            ''', (
                data['name'], data.get('dose'), data.get('frequency'),
                data['start_date'], data.get('end_date') or None,
                data.get('note')
            ))
        return redirect(url_for('medication'))
    with get_db() as conn:
        meds = conn.execute(
            'SELECT * FROM medication ORDER BY active DESC, start_date DESC'
        ).fetchall()
    return render_template('medication.html', today=date.today().isoformat(), meds=meds)


@app.route('/med/<int:med_id>/edit', methods=['POST'])
@login_required
def edit_med(med_id):
    data = request.form
    with get_db() as conn:
        conn.execute('''
            UPDATE medication SET name=?,dose=?,frequency=?,start_date=?,end_date=?,note=?,active=?
            WHERE id=?
        ''', (
            data['name'], data.get('dose'), data.get('frequency'),
            data['start_date'], data.get('end_date') or None,
            data.get('note'), int(data.get('active', 1)),
            med_id
        ))
    return redirect(url_for('medication'))


@app.route('/med/<int:med_id>/stop', methods=['POST'])
@login_required
def stop_med(med_id):
    with get_db() as conn:
        conn.execute('UPDATE medication SET active=0 WHERE id=?', (med_id,))
    return redirect(url_for('medication'))


@app.route('/med/<int:med_id>/delete', methods=['POST'])
@login_required
def delete_med(med_id):
    with get_db() as conn:
        conn.execute('DELETE FROM medication WHERE id=?', (med_id,))
    return redirect(url_for('medication'))


# ── 路由：CSV 匯出 ───────────────────────────────────────────

@app.route('/export/logs')
def export_logs():
    with get_db() as conn:
        logs = conn.execute('SELECT * FROM daily_log ORDER BY log_date DESC').fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['日期', '體重(kg)', '食物', '飲水', '排便', '健康觀察', '建立時間'])
    for log in logs:
        writer.writerow([
            log['log_date'], log['weight'] or '',
            log['food'] or '', log['water'] or '',
            log['poop'] or '', log['health_note'] or '',
            log['created_at']
        ])
    output.seek(0)
    return Response(
        '\ufeff' + output.getvalue(),
        mimetype='text/csv; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename=rabbit_logs_{date.today().isoformat()}.csv'}
    )


@app.route('/export/vet')
def export_vet():
    with get_db() as conn:
        visits = conn.execute('SELECT * FROM vet_visit ORDER BY visit_date DESC').fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['日期', '症狀', '診斷', '治療', '獸醫', '備注', '下次回診', '建立時間'])
    for v in visits:
        writer.writerow([
            v['visit_date'], v['symptoms'] or '', v['diagnosis'] or '',
            v['treatment'] or '', v['vet_name'] or '', v['note'] or '',
            v['next_visit'] or '', v['created_at']
        ])
    output.seek(0)
    return Response(
        '\ufeff' + output.getvalue(),
        mimetype='text/csv; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename=rabbit_vet_{date.today().isoformat()}.csv'}
    )


@app.route('/export/med')
def export_med():
    with get_db() as conn:
        meds = conn.execute('SELECT * FROM medication ORDER BY start_date DESC').fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['藥名', '劑量', '頻率', '開始日期', '結束日期', '備注', '狀態', '建立時間'])
    for m in meds:
        writer.writerow([
            m['name'], m['dose'] or '', m['frequency'] or '',
            m['start_date'], m['end_date'] or '', m['note'] or '',
            '用藥中' if m['active'] else '已停藥',
            m['created_at']
        ])
    output.seek(0)
    return Response(
        '\ufeff' + output.getvalue(),
        mimetype='text/csv; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename=rabbit_medications_{date.today().isoformat()}.csv'}
    )


# ── 路由：AI 諮詢 ────────────────────────────────────────────

@app.route('/ai')
def ai_chat():
    return render_template('ai_chat.html')


@app.route('/api/chat', methods=['POST'])
@login_required
def api_chat():
    question = request.json.get('question', '').strip()
    if not question:
        return jsonify({'error': '請輸入問題'}), 400

    context = build_context()
    system_prompt = (
        "你是一位專業的兔子照護顧問，熟悉兔子的飲食、健康、行為與疾病。"
        "請根據以下兔子的完整紀錄，以繁體中文回答飼主的問題。"
        "回答要具體實用，如有需要請提醒就醫。\n\n"
        f"{context}"
    )

    try:
        response = client.chat.completions.create(
            model='gpt-4o',
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': question}
            ],
            max_tokens=1000,
        )
        answer = response.choices[0].message.content.strip()
        return jsonify({'answer': answer})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── 每日 Telegram 摘要 ───────────────────────────────────────

def send_daily_summary():
    """每天早上 8 點推送昨天的健康摘要到 Telegram"""
    rabbit = get_rabbit()
    if not rabbit:
        return

    yesterday = (date.today() - __import__('datetime').timedelta(days=1)).isoformat()
    with get_db() as conn:
        log = conn.execute(
            'SELECT * FROM daily_log WHERE log_date=?', (yesterday,)
        ).fetchone()
        meds = conn.execute(
            "SELECT * FROM medication WHERE active=1"
        ).fetchall()

    lines = [f"🐰 <b>{rabbit['name']} 的健康摘要</b>（{yesterday}）\n"]

    if log:
        lines.append(f"📋 <b>昨日日誌</b>")
        if log['weight']:
            lines.append(f"  體重：{log['weight']} kg")
        if log['food']:
            lines.append(f"  食物：{log['food']}")
        if log['poop']:
            lines.append(f"  排便：{log['poop']}")
        if log['health_note']:
            lines.append(f"  觀察：{log['health_note']}")
    else:
        lines.append("⚠️ 昨天未記錄日誌")

    if meds:
        lines.append(f"\n💊 <b>目前用藥（{len(meds)} 項）</b>")
        for m in meds:
            end_str = f"至 {m['end_date']}" if m['end_date'] else '持續中'
            lines.append(f"  • {m['name']} {m['dose'] or ''} {end_str}")

    reminders = get_reminders()
    if reminders:
        lines.append(f"\n🔔 <b>提醒事項</b>")
        for r in reminders:
            lines.append(f"  {r['icon']} {r['text']}")

    send_telegram('\n'.join(lines))


# ── 動作辨識 API ──────────────────────────────────────────────

@app.route('/api/log-action', methods=['POST'])
def api_log_action():
    data = request.get_json()
    action = data.get('action', '')
    confidence = float(data.get('confidence', 0))
    timestamp = data.get('timestamp', '')
    screenshot = data.get('screenshot')

    if action == 'other' or not action:
        return jsonify({'status': 'ignored'})

    log_date = timestamp[:10] if timestamp else date.today().isoformat()

    with get_db() as conn:
        conn.execute(
            'INSERT INTO action_log (log_date, action, confidence, timestamp, screenshot) VALUES (?,?,?,?,?)',
            (log_date, action, confidence, timestamp, screenshot)
        )
    return jsonify({'status': 'ok'})


@app.route('/api/action-log/<int:log_id>', methods=['DELETE'])
@login_required
def api_delete_action(log_id):
    with get_db() as conn:
        conn.execute('DELETE FROM action_log WHERE id=?', (log_id,))
    return jsonify({'status': 'ok'})


@app.route('/api/today-actions')
def api_today_actions():
    target_date = request.args.get('date', date.today().isoformat())
    with get_db() as conn:
        rows = conn.execute(
            'SELECT action, confidence, timestamp FROM action_log WHERE log_date=? ORDER BY timestamp',
            (target_date,)
        ).fetchall()
    return jsonify({'actions': [dict(r) for r in rows]})


@app.route('/actions')
def action_history():
    target_date = request.args.get('date', date.today().isoformat())
    action_labels = {
        'eating': '🍽 吃飯', 'drinking': '💧 喝水',
        'stretching': '🐇 伸懶腰', 'sleeping': '😴 睡覺', 'resting': '🐰 休息'
    }
    with get_db() as conn:
        rows = conn.execute(
            'SELECT id, action, confidence, timestamp, screenshot FROM action_log WHERE log_date=? ORDER BY timestamp DESC',
            (target_date,)
        ).fetchall()
        dates = conn.execute(
            'SELECT DISTINCT log_date FROM action_log ORDER BY log_date DESC LIMIT 30'
        ).fetchall()
    action_list = [
        {
            'id': r['id'],
            'action': r['action'],
            'label': action_labels.get(r['action'], r['action']),
            'confidence': r['confidence'],
            'time': r['timestamp'][11:16] if r['timestamp'] else '',
            'screenshot': r['screenshot'],
        }
        for r in rows
    ]
    summary = {}
    for entry in action_list:
        a = entry['action']
        if a not in summary:
            summary[a] = {'label': action_labels.get(a, a), 'count': 0}
        summary[a]['count'] += 1
    available_dates = [r['log_date'] for r in dates]
    return render_template('action_history.html',
        target_date=target_date,
        action_list=action_list,
        summary=summary,
        available_dates=available_dates,
    )


# ── 路由：喝水紀錄 ───────────────────────────────────────────

def _parse_cc(text):
    """從文字中提取第一個數字作為 cc 數，例如 '25cc'、'早上喝15cc' → int。"""
    if not text:
        return None
    m = re.search(r'(\d+)', str(text))
    return int(m.group(1)) if m else None


@app.route('/water')
def water_log():
    from datetime import timedelta
    today = date.today().isoformat()
    with get_db() as conn:
        # water_log 今日紀錄
        today_records = conn.execute(
            'SELECT * FROM water_log WHERE log_date=? ORDER BY log_time ASC',
            (today,)
        ).fetchall()
        wl_total = conn.execute(
            'SELECT COALESCE(SUM(amount_cc),0) as total FROM water_log WHERE log_date=?',
            (today,)
        ).fetchone()['total']

        # daily_log 今日水量（解析 cc）
        dl_today = conn.execute(
            'SELECT water FROM daily_log WHERE log_date=? AND water IS NOT NULL',
            (today,)
        ).fetchall()
        dl_today_cc = sum((_parse_cc(r['water']) or 0) for r in dl_today)
        today_total = wl_total + dl_today_cc

        # 今日來自日誌的紀錄（供前端顯示唯讀列）
        dl_today_rows = [
            {'water': r['water'], 'cc': _parse_cc(r['water'])}
            for r in dl_today if _parse_cc(r['water'])
        ]

        # water_log 近 7 天
        history = conn.execute(
            '''SELECT log_date, SUM(amount_cc) as total
               FROM water_log
               WHERE log_date >= date('now','localtime','-6 days')
               GROUP BY log_date
               ORDER BY log_date ASC''',
        ).fetchall()
        hist_map = {r['log_date']: r['total'] for r in history}

        # daily_log 近 7 天（疊加）
        dl_hist = conn.execute(
            '''SELECT log_date, water FROM daily_log
               WHERE log_date >= date('now','localtime','-6 days')
               AND water IS NOT NULL''',
        ).fetchall()
        for r in dl_hist:
            cc = _parse_cc(r['water'])
            if cc:
                hist_map[r['log_date']] = hist_map.get(r['log_date'], 0) + cc

    hist_labels, hist_values = [], []
    for i in range(6, -1, -1):
        d = (date.today() - timedelta(days=i)).isoformat()
        hist_labels.append(d[5:])   # MM-DD
        hist_values.append(hist_map.get(d, 0))
    return render_template('water.html',
        today=today,
        today_records=today_records,
        dl_today_rows=dl_today_rows,
        today_total=today_total,
        hist_labels=json.dumps(hist_labels),
        hist_values=json.dumps(hist_values),
    )


@app.route('/api/water', methods=['POST'])
@login_required
def api_add_water():
    data = request.get_json()
    amount = int(data.get('amount_cc', 0))
    note = data.get('note', '').strip()
    if amount <= 0:
        return jsonify({'error': '請輸入正確 cc 數'}), 400
    now = datetime.now()
    with get_db() as conn:
        conn.execute(
            'INSERT INTO water_log (log_date, log_time, amount_cc, note) VALUES (?,?,?,?)',
            (now.strftime('%Y-%m-%d'), now.strftime('%H:%M'), amount, note or None)
        )
        total = conn.execute(
            'SELECT COALESCE(SUM(amount_cc),0) as t FROM water_log WHERE log_date=?',
            (now.strftime('%Y-%m-%d'),)
        ).fetchone()['t']
    return jsonify({'ok': True, 'today_total': total,
                    'record': {'log_time': now.strftime('%H:%M'), 'amount_cc': amount, 'note': note}})


@app.route('/api/water/<int:record_id>', methods=['DELETE'])
@login_required
def api_delete_water(record_id):
    with get_db() as conn:
        conn.execute('DELETE FROM water_log WHERE id=?', (record_id,))
        total = conn.execute(
            'SELECT COALESCE(SUM(amount_cc),0) as t FROM water_log WHERE log_date=?',
            (date.today().isoformat(),)
        ).fetchone()['t']
    return jsonify({'ok': True, 'today_total': total})


if __name__ == '__main__':
    init_db()
    # 每天 08:00 推送 Telegram 健康摘要
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        scheduler = BackgroundScheduler()
        scheduler.add_job(send_daily_summary, 'cron', hour=8, minute=0)
        scheduler.start()
        print('[scheduler] 每日摘要排程已啟動（08:00）')
    except Exception as e:
        print(f'[scheduler] 啟動失敗：{e}')
    port = int(os.getenv('PORT', 5200))
    print(f"兔子照護系統啟動：http://localhost:{port}")
    app.run(host='0.0.0.0', port=port, debug=False)
