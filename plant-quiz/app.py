#!/usr/bin/env python3
import os, random, json
from pathlib import Path
from flask import Flask, jsonify, send_file, render_template_string

app = Flask(__name__)
PHOTOS_DIR = "/Users/steven/Downloads/edible_plants_photos"
DESCRIPTIONS_FILE = Path(__file__).parent / "descriptions.json"

def load_descriptions():
    if DESCRIPTIONS_FILE.exists():
        with open(DESCRIPTIONS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}

def get_pool():
    plants = [d for d in os.listdir(PHOTOS_DIR)
              if os.path.isdir(os.path.join(PHOTOS_DIR, d)) and not d.startswith('.')]
    pool = []
    for p in plants:
        d = os.path.join(PHOTOS_DIR, p)
        photos = [f for f in os.listdir(d) if f.lower().endswith(('.jpg','.jpeg','.png'))]
        if photos:
            pool.append((p, photos))
    return pool

@app.route('/api/quiz/photo')
def quiz_photo():
    pool = get_pool()
    random.shuffle(pool)
    selected = pool[:20]
    questions = []
    for correct, photos in selected:
        photo = random.choice(photos)
        wrong = random.sample([p for p,_ in pool if p != correct], 2)
        choices = [correct] + wrong
        random.shuffle(choices)
        questions.append({
            "type": "photo",
            "photo": f"/photo/{correct}/{photo}",
            "answer": correct,
            "choices": choices
        })
    return jsonify(questions)

@app.route('/api/quiz/name')
def quiz_name():
    pool = get_pool()
    random.shuffle(pool)
    selected = pool[:20]
    questions = []
    for correct, photos in selected:
        correct_photo = random.choice(photos)
        wrong_plants = random.sample([p for p,_ in pool if p != correct], 2)
        wrong_photos = []
        for wp in wrong_plants:
            for p, phs in pool:
                if p == wp:
                    wrong_photos.append(random.choice(phs))
                    break
        choices = [
            {"plant": correct,        "photo": f"/photo/{correct}/{correct_photo}"},
            {"plant": wrong_plants[0],"photo": f"/photo/{wrong_plants[0]}/{wrong_photos[0]}"},
            {"plant": wrong_plants[1],"photo": f"/photo/{wrong_plants[1]}/{wrong_photos[1]}"},
        ]
        random.shuffle(choices)
        questions.append({
            "type": "name",
            "question": correct,
            "answer": correct,
            "choices": choices
        })
    return jsonify(questions)

@app.route('/api/memory')
def memory_cards():
    pool = get_pool()
    descriptions = load_descriptions()
    cards = []
    for plant, photos in pool:
        desc = descriptions.get(plant, {})
        cards.append({
            "name": plant,
            "photo": f"/photo/{plant}/{random.choice(photos)}",
            "features": desc.get("features", ""),
            "edible": desc.get("edible", ""),
            "habitat": desc.get("habitat", ""),
            "tips": desc.get("tips", ""),
        })
    random.shuffle(cards)
    return jsonify(cards)

@app.route('/photo/<plant>/<filename>')
def photo(plant, filename):
    path = os.path.join(PHOTOS_DIR, plant, filename)
    return send_file(path)

HTML = """<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>雙北可食植物記憶遊戲</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: "Noto Sans TC", sans-serif; background: #1a2e1a; color: #e8f5e9;
       min-height: 100vh; display: flex; flex-direction: column;
       align-items: center; justify-content: center; padding: 20px; }
h1 { font-size: 1.6rem; margin-bottom: 6px; color: #81c784; text-align: center; }
#sub { color: #a5d6a7; margin-bottom: 24px; font-size: 0.9rem; text-align: center; }
#prog { width:100%; max-width:600px; background:#2e4d2e; border-radius:8px; height:8px; margin-bottom:20px; display:none; }
#pbar { height:8px; background:#66bb6a; border-radius:8px; transition:width .3s; width:0%; }

.mode-wrap { display:flex; gap:16px; flex-wrap:wrap; justify-content:center; }
.mode-btn { padding:24px 32px; border-radius:16px; border:2px solid #3d6b3d; background:#243824;
            color:#e8f5e9; font-size:1.1rem; cursor:pointer; text-align:center; min-width:200px; }
.mode-btn:hover { border-color:#66bb6a; background:#2e5c2e; }
.mode-btn .icon { font-size:2.5rem; display:block; margin-bottom:10px; }
.mode-btn .desc { font-size:0.8rem; color:#a5d6a7; margin-top:6px; }

.mem-card { background:#243824; border-radius:16px; max-width:600px; width:100%; box-shadow:0 8px 32px rgba(0,0,0,.4); overflow:hidden; }
.mem-photo { width:100%; aspect-ratio:4/3; object-fit:cover; display:block; }
.mem-body { padding:20px 24px; }
.mem-name { font-size:1.8rem; font-weight:700; color:#c8e6c9; letter-spacing:3px; margin-bottom:16px; text-align:center; }
.mem-section { margin-bottom:14px; }
.mem-label { font-size:0.72rem; color:#66bb6a; letter-spacing:1.5px; text-transform:uppercase; margin-bottom:4px; font-weight:600; }
.mem-text { font-size:0.95rem; line-height:1.7; color:#e8f5e9; }
.mem-tips { background:#1a3a1a; border-left:3px solid #66bb6a; padding:10px 14px; border-radius:0 8px 8px 0; font-size:0.9rem; line-height:1.6; color:#a5d6a7; }
.mem-nav { display:flex; gap:10px; align-items:center; justify-content:space-between; padding:16px 24px; border-top:1px solid #2e4d2e; }
.mem-counter { color:#a5d6a7; font-size:0.85rem; }
.mem-btn { padding:10px 22px; border-radius:10px; border:none; font-size:1rem; cursor:pointer; font-weight:600; }
.mem-prev { background:#2e4d2e; color:#e8f5e9; }
.mem-next { background:#66bb6a; color:#1a2e1a; }

.card { background:#243824; border-radius:16px; padding:24px; max-width:600px; width:100%;
        box-shadow:0 8px 32px rgba(0,0,0,.4); }
.qnum { color:#81c784; font-size:.85rem; margin-bottom:12px; }
.photo-wrap { width:100%; aspect-ratio:4/3; border-radius:12px; overflow:hidden;
              margin-bottom:20px; background:#1a2e1a; }
.photo-wrap img { width:100%; height:100%; object-fit:cover; }
.choices-text { display:flex; flex-direction:column; gap:10px; }
.choice-text { padding:14px 18px; border-radius:10px; border:2px solid #3d6b3d;
               background:#2e4d2e; color:#e8f5e9; font-size:1.15rem; cursor:pointer; text-align:center; }
.choice-text:hover { border-color:#66bb6a; background:#3a5f3a; }
.plant-name { font-size:2rem; font-weight:700; color:#c8e6c9; text-align:center;
              padding:16px 0 20px; letter-spacing:4px; }
.choices-photo { display:grid; grid-template-columns:repeat(3,1fr); gap:12px; }
.choice-photo { border-radius:12px; overflow:hidden; border:3px solid #3d6b3d;
                cursor:pointer; aspect-ratio:3/4; }
.choice-photo img { width:100%; height:100%; object-fit:cover; display:block; }
.choice-photo:hover { border-color:#66bb6a; transform:scale(1.03); }
.c-correct-text { border-color:#66bb6a !important; background:#2e7d32 !important; color:#fff !important; }
.c-wrong-text   { border-color:#e53935 !important; background:#b71c1c !important; color:#fff !important; }
.c-correct-photo { border-color:#66bb6a !important; box-shadow:0 0 0 3px #66bb6a; }
.c-wrong-photo   { border-color:#e53935 !important; opacity:.45; }
.disabled { cursor:default !important; pointer-events:none; }
.next-btn { margin-top:18px; width:100%; padding:14px; border-radius:10px; border:none;
            background:#66bb6a; color:#1a2e1a; font-size:1.05rem; font-weight:700; cursor:pointer; }
.result { text-align:center; }
.score-big { font-size:3.5rem; font-weight:700; color:#81c784; }
.score-label { font-size:1rem; color:#a5d6a7; margin-bottom:16px; }
.result-list { text-align:left; margin:12px 0; max-height:280px; overflow-y:auto; }
.result-item { display:flex; align-items:center; gap:10px; padding:7px 0;
               border-bottom:1px solid #2e4d2e; font-size:.95rem; }
.r-ok   { color:#81c784; }
.r-fail { color:#ef9a9a; }
.result-btns { display:flex; gap:10px; margin-top:16px; }
.btn-back    { flex:1; padding:13px; border-radius:10px; border:2px solid #3d6b3d;
               background:transparent; color:#e8f5e9; font-size:1rem; cursor:pointer; }
.btn-again   { flex:1; padding:13px; border-radius:10px; border:none;
               background:#66bb6a; color:#1a2e1a; font-size:1rem; font-weight:700; cursor:pointer; }
</style>
</head>
<body>
<h1>🌿 雙北可食植物</h1>
<p id="sub">每輪 20 題，三選一</p>
<div id="prog"><div id="pbar"></div></div>
<div id="root"></div>

<script>
var questions=[], idx=0, score=0, results=[], mode='';

function showMenu() {
  mode = '';
  document.getElementById('prog').style.display = 'none';
  document.getElementById('sub').textContent = '每輪 20 題，三選一';
  var root = document.getElementById('root');
  root.innerHTML = '';
  var wrap = document.createElement('div');
  wrap.className = 'mode-wrap';

  var btnA = document.createElement('button');
  btnA.className = 'mode-btn';
  btnA.innerHTML = '<span class="icon">🖼️</span>看照片選名稱<div class="desc">顯示植物照片<br>從三個名稱中選出正確答案</div>';
  btnA.addEventListener('click', function(){ startMode('photo'); });

  var btnB = document.createElement('button');
  btnB.className = 'mode-btn';
  btnB.innerHTML = '<span class="icon">🔤</span>看名稱選照片<div class="desc">顯示植物名稱<br>從三張照片中選出正確的</div>';
  btnB.addEventListener('click', function(){ startMode('name'); });

  var btnC = document.createElement('button');
  btnC.className = 'mode-btn';
  btnC.innerHTML = '<span class="icon">📖</span>記憶學習模式<div class="desc">照片＋特徵＋食用方式<br>翻卡片背誦每種植物</div>';
  btnC.addEventListener('click', function(){ startMemory(); });

  wrap.appendChild(btnA);
  wrap.appendChild(btnB);
  wrap.appendChild(btnC);
  root.appendChild(wrap);
}

function startMode(m) {
  mode = m;
  document.getElementById('sub').textContent = m==='photo' ? '🖼️ 看照片選名稱' : '🔤 看名稱選照片';
  document.getElementById('root').innerHTML = '<div style="text-align:center;padding:40px;color:#81c784;">載入中...</div>';
  fetch('/api/quiz/' + m)
    .then(function(r){ return r.json(); })
    .then(function(data){
      questions = data; idx = 0; score = 0; results = [];
      document.getElementById('prog').style.display = 'block';
      render();
    });
}

function render() {
  if (idx >= questions.length) { showResult(); return; }
  var q = questions[idx];
  document.getElementById('pbar').style.width = (idx/questions.length*100) + '%';
  if (q.type === 'photo') renderPhoto(q);
  else renderName(q);
}

function renderPhoto(q) {
  var root = document.getElementById('root');
  root.innerHTML = '';
  var card = document.createElement('div');
  card.className = 'card';
  card.innerHTML = '<div class="qnum">第 '+(idx+1)+' / '+questions.length+' 題</div>'
    + '<div class="photo-wrap"><img src="'+q.photo+'" loading="lazy"></div>'
    + '<div class="choices-text"></div>';
  var choicesDiv = card.querySelector('.choices-text');
  q.choices.forEach(function(c) {
    var btn = document.createElement('button');
    btn.className = 'choice-text';
    btn.textContent = c;
    btn.addEventListener('click', function(){ answerPhoto(c, q.answer); });
    choicesDiv.appendChild(btn);
  });
  root.appendChild(card);
}

function renderName(q) {
  var root = document.getElementById('root');
  root.innerHTML = '';
  var card = document.createElement('div');
  card.className = 'card';
  card.innerHTML = '<div class="qnum">第 '+(idx+1)+' / '+questions.length+' 題</div>'
    + '<div class="plant-name">'+q.question+'</div>'
    + '<div class="choices-photo"></div>';
  var grid = card.querySelector('.choices-photo');
  q.choices.forEach(function(c) {
    var div = document.createElement('div');
    div.className = 'choice-photo';
    div.innerHTML = '<img src="'+c.photo+'" loading="lazy">';
    div.addEventListener('click', function(){ answerName(c.plant, q.answer, q.choices); });
    grid.appendChild(div);
  });
  root.appendChild(card);
}

function answerPhoto(chosen, correct) {
  var btns = document.querySelectorAll('.choice-text');
  btns.forEach(function(b) {
    b.classList.add('disabled');
    if (b.textContent === correct) b.classList.add('c-correct-text');
    else if (b.textContent === chosen && chosen !== correct) b.classList.add('c-wrong-text');
  });
  finishQ(chosen === correct, chosen);
}

function answerName(chosen, correct, choices) {
  var divs = document.querySelectorAll('.choice-photo');
  divs.forEach(function(d, i) {
    d.classList.add('disabled');
    if (choices[i].plant === correct) d.classList.add('c-correct-photo');
    else if (choices[i].plant === chosen && chosen !== correct) d.classList.add('c-wrong-photo');
  });
  finishQ(chosen === correct, chosen);
}

function finishQ(ok, chosen) {
  if (ok) score++;
  results.push({ answer: questions[idx].answer, chosen: chosen, ok: ok });
  var btn = document.createElement('button');
  btn.className = 'next-btn';
  btn.textContent = idx < questions.length-1 ? '下一題 →' : '查看結果';
  btn.addEventListener('click', function(){ idx++; render(); });
  document.querySelector('.card').appendChild(btn);
}

function showResult() {
  document.getElementById('pbar').style.width = '100%';
  var pct = Math.round(score/questions.length*100);
  var emoji = pct>=90?'🌟':pct>=70?'🌿':pct>=50?'🌱':'💪';
  var root = document.getElementById('root');
  root.innerHTML = '';
  var card = document.createElement('div');
  card.className = 'card';

  var listHTML = results.map(function(r){
    return '<div class="result-item '+(r.ok?'r-ok':'r-fail')+'">'
      +'<span>'+(r.ok?'✅':'❌')+'</span>'
      +'<span>'+r.answer+'</span>'
      +(!r.ok ? '<span style="color:#888;font-size:.82rem">（你選：'+r.chosen+'）</span>' : '')
      +'</div>';
  }).join('');

  card.innerHTML = '<div class="result">'
    +'<div class="score-big">'+emoji+' '+score+' / '+questions.length+'</div>'
    +'<div class="score-label">答對率 '+pct+'%</div>'
    +'<div class="result-list">'+listHTML+'</div>'
    +'<div class="result-btns">'
    +'<button class="btn-back" id="btnBack">← 換模式</button>'
    +'<button class="btn-again" id="btnAgain">再玩一輪 🔄</button>'
    +'</div></div>';

  root.appendChild(card);
  document.getElementById('btnBack').addEventListener('click', function(){ showMenu(); });
  document.getElementById('btnAgain').addEventListener('click', function(){ startMode(mode); });
}

var memCards = [], memIdx = 0;

function startMemory() {
  document.getElementById('sub').textContent = '📖 記憶學習模式';
  document.getElementById('prog').style.display = 'block';
  document.getElementById('root').innerHTML = '<div style="text-align:center;padding:40px;color:#81c784;">載入植物資料中...</div>';
  fetch('/api/memory')
    .then(function(r){ return r.json(); })
    .then(function(data){
      memCards = data; memIdx = 0;
      renderMemCard();
    });
}

function renderMemCard() {
  if (memCards.length === 0) return;
  var c = memCards[memIdx];
  document.getElementById('pbar').style.width = ((memIdx + 1) / memCards.length * 100) + '%';
  var root = document.getElementById('root');
  root.innerHTML = '';
  var card = document.createElement('div');
  card.className = 'mem-card';
  card.innerHTML =
    '<img class="mem-photo" src="' + c.photo + '" loading="lazy">' +
    '<div class="mem-body">' +
      '<div class="mem-name">' + c.name + '</div>' +
      (c.features ? '<div class="mem-section"><div class="mem-label">外觀特徵</div><div class="mem-text">' + c.features + '</div></div>' : '') +
      (c.edible   ? '<div class="mem-section"><div class="mem-label">食用方式</div><div class="mem-text">' + c.edible   + '</div></div>' : '') +
      (c.habitat  ? '<div class="mem-section"><div class="mem-label">生長環境</div><div class="mem-text">' + c.habitat  + '</div></div>' : '') +
      (c.tips     ? '<div class="mem-section"><div class="mem-label">記憶口訣</div><div class="mem-tips">' + c.tips + '</div></div>' : '') +
    '</div>' +
    '<div class="mem-nav">' +
      '<button class="mem-btn mem-prev" id="memPrev">← 上一張</button>' +
      '<span class="mem-counter">' + (memIdx + 1) + ' / ' + memCards.length + '</span>' +
      '<button class="mem-btn mem-next" id="memNext">' + (memIdx < memCards.length - 1 ? '下一張 →' : '回主選單') + '</button>' +
    '</div>';
  root.appendChild(card);

  document.getElementById('memPrev').addEventListener('click', function(){
    if (memIdx > 0) { memIdx--; renderMemCard(); }
    else showMenu();
  });
  document.getElementById('memNext').addEventListener('click', function(){
    if (memIdx < memCards.length - 1) { memIdx++; renderMemCard(); }
    else showMenu();
  });
}

showMenu();
</script>
</body>
</html>"""

@app.route('/')
def index():
    return render_template_string(HTML)

if __name__ == '__main__':
    app.run(port=7799, debug=False)
