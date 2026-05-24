import os
import sqlite3
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename

app = Flask(__name__)
DB = os.path.join(os.path.dirname(__file__), 'plants.db')
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), 'uploads')
os.makedirs(UPLOAD_DIR, exist_ok=True)

def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS plants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            category TEXT DEFAULT '',
            edible_parts TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            lat REAL NOT NULL,
            lng REAL NOT NULL,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )''')
        conn.execute('''CREATE TABLE IF NOT EXISTS photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plant_id INTEGER NOT NULL,
            filename TEXT NOT NULL,
            FOREIGN KEY (plant_id) REFERENCES plants(id)
        )''')

def plant_to_dict(row, photos):
    return {
        'id': row['id'],
        'name': row['name'],
        'category': row['category'],
        'edible_parts': row['edible_parts'],
        'notes': row['notes'],
        'lat': row['lat'],
        'lng': row['lng'],
        'created_at': row['created_at'],
        'photo_urls': [f'/uploads/{p}' for p in photos],
    }

@app.route('/api/plants', methods=['GET'])
def list_plants():
    with get_db() as conn:
        plants = conn.execute('SELECT * FROM plants ORDER BY created_at DESC').fetchall()
        result = []
        for p in plants:
            photos = [r['filename'] for r in conn.execute(
                'SELECT filename FROM photos WHERE plant_id=?', (p['id'],)
            ).fetchall()]
            result.append(plant_to_dict(p, photos))
    return jsonify(result)

@app.route('/api/plants', methods=['POST'])
def create_plant():
    data = request.json
    with get_db() as conn:
        cur = conn.execute(
            'INSERT INTO plants (name, category, edible_parts, notes, lat, lng) VALUES (?,?,?,?,?,?)',
            (data['name'], data.get('category', ''), data.get('edible_parts', ''),
             data.get('notes', ''), data['lat'], data['lng'])
        )
        plant = conn.execute('SELECT * FROM plants WHERE id=?', (cur.lastrowid,)).fetchone()
    return jsonify(plant_to_dict(plant, [])), 201

@app.route('/api/plants/<int:pid>', methods=['GET'])
def get_plant(pid):
    with get_db() as conn:
        plant = conn.execute('SELECT * FROM plants WHERE id=?', (pid,)).fetchone()
        if not plant:
            return jsonify({'error': 'not found'}), 404
        photos = [r['filename'] for r in conn.execute(
            'SELECT filename FROM photos WHERE plant_id=?', (pid,)
        ).fetchall()]
    return jsonify(plant_to_dict(plant, photos))

@app.route('/api/plants/<int:pid>', methods=['DELETE'])
def delete_plant(pid):
    with get_db() as conn:
        conn.execute('DELETE FROM photos WHERE plant_id=?', (pid,))
        conn.execute('DELETE FROM plants WHERE id=?', (pid,))
    return jsonify({'ok': True})

@app.route('/api/plants/<int:pid>/photos', methods=['POST'])
def upload_photo(pid):
    file = request.files.get('photo')
    if not file:
        return jsonify({'error': 'no file'}), 400
    filename = f'{pid}_{datetime.now().strftime("%Y%m%d%H%M%S")}_{secure_filename(file.filename)}'
    file.save(os.path.join(UPLOAD_DIR, filename))
    with get_db() as conn:
        conn.execute('INSERT INTO photos (plant_id, filename) VALUES (?,?)', (pid, filename))
    return jsonify({'filename': filename})

@app.route('/uploads/<filename>')
def serve_file(filename):
    return send_from_directory(UPLOAD_DIR, filename)

@app.route('/api/health')
def health():
    return jsonify({'status': 'ok'})

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5800, debug=False)
