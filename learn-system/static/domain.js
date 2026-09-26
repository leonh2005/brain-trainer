const DOMAIN_ID = Number(document.body.dataset.domainId);
const GOAL_LABELS = { write: '能自己寫出來', read: '能讀懂並改錯', principle: '懂底層原理', exam: '檢定題型' };
const VERDICT_LABELS = { correct: '正確', partial: '部分正確', wrong: '錯誤' };
const SECTION_TITLES = { consensus: '共識（核心思維模型）', dispute: '分歧（爭議點）', frontier: '探索區' };

let state = { domain: null, concepts: [], current: null, question: null };

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

// 說明與出題都要跑 Agent（數秒到十幾秒），期間使用者隨時可能改點別的概念。
// 慢回來的那一筆必須丟掉，否則會蓋掉現在選中的那個概念。
function isCurrent(conceptId) {
  return state.current !== null && state.current.id === conceptId;
}

async function api(path, opts) {
  const res = await fetch(path, opts);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
  return body;
}

async function refresh() {
  const data = await api(`/api/domains/${DOMAIN_ID}`);
  state.domain = data.domain;
  state.concepts = data.concepts;

  document.getElementById('domain-name').textContent = data.domain.name;
  document.getElementById('domain-meta').textContent =
    `目標 ${data.domain.goals.map(g => GOAL_LABELS[g] || g).join('／')} · 掌握 ${data.progress.mastery_pct}%`;

  renderMap();
  renderProgress(data.progress);
  await loadMistakes();
}

function renderMap() {
  const wrap = document.getElementById('map-sections');
  wrap.replaceChildren();
  const empty = document.getElementById('map-empty');
  if (!state.concepts.length) { empty.hidden = false; return; }
  empty.hidden = true;

  for (const section of ['consensus', 'dispute', 'frontier']) {
    const items = state.concepts.filter(c => c.section === section);
    if (!items.length) continue;
    wrap.append(el('div', 'sec-title', SECTION_TITLES[section]));
    for (const c of items) {
      const row = el('div', 'concept');
      if (state.current && state.current.id === c.id) row.classList.add('active');
      row.append(el('span', `dot ${c.status === 'untested' ? '' : c.status}`));
      row.append(el('span', null, c.name));
      row.onclick = () => selectConcept(c.id);
      wrap.append(row);
    }
  }
}

function renderProgress(progress) {
  const { counts } = progress;
  document.getElementById('progress-body').textContent =
    `未練 ${counts.untested} · 練習中 ${counts.practicing} · 已掌握 ${counts.mastered}`;
}

async function loadMistakes() {
  const wrap = document.getElementById('mistakes');
  let mistakes;
  try {
    ({ mistakes } = await api(`/api/domains/${DOMAIN_ID}/mistakes`));
  } catch (e) {
    // 錯題本掛掉不該拖垮整頁，所以自成一個 try，錯誤直接顯示在原地。
    wrap.replaceChildren(el('div', null, `錯題本載入失敗：${e.message}`));
    return;
  }
  wrap.replaceChildren();
  if (!mistakes.length) {
    wrap.append(el('div', null, '還沒有答錯的紀錄。'));
    return;
  }
  for (const m of mistakes) {
    const row = el('div', 'mistake');
    row.append(el('span', `verdict ${m.verdict}`, VERDICT_LABELS[m.verdict] || m.verdict));
    row.append(el('span', null, m.concept_name));
    row.append(el('span', 'dim', new Date(m.created_at).toLocaleString('zh-TW')));
    wrap.append(row);
  }
}

async function selectConcept(id) {
  state.current = state.concepts.find(c => c.id === id);
  state.question = null;
  renderMap();
  document.getElementById('work-empty').hidden = true;
  document.getElementById('work-body').hidden = false;
  document.getElementById('concept-name').textContent = state.current.name;
  document.getElementById('verdict').hidden = true;
  document.getElementById('question-area').hidden = true;

  // 說明第一次要跑 Agent（慢且貴），所以只在使用者點開這個概念時才要，
  // 之後後端走快取。放在 refresh 的輪詢路徑上會每次輪詢都打一次。
  const desc = document.getElementById('concept-desc');
  desc.textContent = '生成說明中…';
  let text;
  try {
    ({ description: text } = await api(`/api/concepts/${id}/explain`, { method: 'POST' }));
  } catch (e) {
    text = `說明生成失敗：${e.message}`;
  }
  if (isCurrent(id)) desc.textContent = text;

  const sel = document.getElementById('goal-type');
  sel.replaceChildren();
  for (const g of state.domain.goals) {
    const o = el('option', null, GOAL_LABELS[g] || g);
    o.value = g;
    sel.append(o);
  }
}

document.getElementById('retry-hint').onclick = () => location.reload();

document.getElementById('ask-question').onclick = async () => {
  const status = document.getElementById('answer-status');
  const conceptId = state.current.id;
  status.textContent = '出題中…';
  try {
    const { question } = await api(`/api/concepts/${conceptId}/questions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ goal_type: document.getElementById('goal-type').value }),
    });
    if (!isCurrent(conceptId)) return;
    state.question = question;
    document.getElementById('question-area').hidden = false;
    document.getElementById('question-prompt').textContent = question.prompt;
    const code = document.getElementById('question-code');
    const snippet = question.payload.code_snippet || question.payload.starter_code;
    code.hidden = !snippet;
    code.textContent = snippet || '';
    document.getElementById('answer').value = question.payload.starter_code || '';
    document.getElementById('verdict').hidden = true;
    status.textContent = '';
  } catch (e) {
    // 422（題目沒過執行驗證）、502（Agent 掛掉）等都在這裡以訊息呈現
    status.textContent = `出題失敗：${e.message}`;
  }
};

document.getElementById('submit-answer').onclick = async () => {
  const status = document.getElementById('answer-status');
  const question = state.question;
  status.textContent = '批改中…';
  let graded;
  try {
    graded = await api(`/api/questions/${question.id}/answer`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ answer: document.getElementById('answer').value }),
    });
  } catch (e) {
    // 408（逾時）、503（執行環境錯誤）、502（Agent 掛掉）都帶自己的說明，
    // 這三種都不算答錯、不落 attempt，直接顯示原文才不會誤導。
    if (state.question === question) status.textContent = `批改失敗：${e.message}`;
    return;
  }
  if (state.question !== question) return;  // 批改期間已換概念，結果不屬於現在的工作區

  const { attempt, concept_status } = graded;
  const box = document.getElementById('verdict');
  box.hidden = false;
  box.className = attempt.verdict;
  box.textContent = `${VERDICT_LABELS[attempt.verdict] || attempt.verdict}\n\n${attempt.feedback}` +
    (attempt.root_cause ? `\n\n錯因：${attempt.root_cause}` : '');
  status.textContent = `概念狀態：${concept_status}`;

  try {
    await refresh();
  } catch (e) {
    // 批改本身成功且結果已顯示，別讓重新載入失敗蓋掉它，訊息要講清楚。
    status.textContent = `批改完成，但重新載入失敗：${e.message}`;
  }
};

document.getElementById('drawer-toggle').onclick = () => {
  document.getElementById('drawer').hidden = false;
};
document.getElementById('drawer-close').onclick = () => {
  document.getElementById('drawer').hidden = true;
};

document.getElementById('chat-form').onsubmit = async (e) => {
  e.preventDefault();
  const input = document.getElementById('chat-input');
  const text = input.value.trim();
  if (!text) return;
  const log = document.getElementById('chat-log');
  log.append(el('div', 'msg me', `你：${text}`));
  input.value = '';
  try {
    const { reply } = await api(`/api/domains/${DOMAIN_ID}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text, concept_id: state.current ? state.current.id : null }),
    });
    log.append(el('div', 'msg', `導師：${reply}`));
  } catch (err) {
    log.append(el('div', 'msg', `錯誤：${err.message}`));
  }
  log.scrollTop = log.scrollHeight;
};

// 輪詢只在首次載入前與地圖生成中進行，生成完成後就停，不再打擾後端。生成中
// 每 3 秒一輪，此時後端正忙著跑 Agent、SQLite 也可能被寫入鎖住，沒有守衛請求
// 會一路堆積；finally 負責放掉守衛，否則一次失敗就讓輪詢永久停擺。
let inflight = false;

async function poll() {
  if (inflight) return;
  inflight = true;
  try {
    await refresh();
  } catch {
    // 服務重啟中或連不上：這次完全不動畫面，停在最後一次成功的狀態，下一輪再試。
  } finally {
    inflight = false;
  }
}

poll();
setInterval(() => { if (!state.domain || state.domain.status !== 'ready') poll(); }, 3000);
