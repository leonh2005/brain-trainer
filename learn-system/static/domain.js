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

// 概念出處是模型產生的網址，一律當成不可信輸入。只有真的解析得出 http/https
// 的才升級成連結：`javascript:` 會在點擊時執行腳本，`data:` 能載入任意內容，
// 兩者都不能進 href。解析不出來的原樣以文字顯示，仍看得到模型給了什麼。
function httpUrlOrNull(value) {
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    return null;
  }
  return parsed.protocol === 'http:' || parsed.protocol === 'https:' ? parsed.href : null;
}

function renderSources(urls) {
  const box = document.getElementById('concept-sources');
  box.replaceChildren();
  if (!urls || !urls.length) { box.hidden = true; return; }
  box.hidden = false;
  box.append(el('span', 'dim', '出處：'));
  for (const raw of urls) {
    const safe = httpUrlOrNull(raw);
    if (!safe) { box.append(el('span', 'source', String(raw))); continue; }
    // 顯示用正規化後的值：原始字串可能與實際指向不同（例如夾了控制字元或
    // 前後空白），顯示與目標一致才不會騙人。
    const a = el('a', 'source', safe);
    a.href = safe;
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    box.append(a);
  }
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

  document.getElementById('load-error').hidden = true;
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
  // 首次載入失敗時 state.domain 還是 null，一律當成「生成中」處理，真正的
  // 錯誤由 #load-error 顯示。
  const status = state.domain ? state.domain.status : 'generating';
  const hasConcepts = state.concepts.length > 0;
  document.getElementById('map-empty').hidden = status !== 'generating' || hasConcepts;
  document.getElementById('map-failed').hidden = status !== 'failed';
  if (!hasConcepts) return;

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
  renderSources(state.current.source_urls);

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

document.getElementById('regenerate').onclick = async () => {
  const button = document.getElementById('regenerate');
  const status = document.getElementById('regenerate-status');
  // failed 的領域不一定沒有練習紀錄：生成跑到一半失敗會留下已建立的概念，而
  // 那些概念在 failed 狀態下照樣畫得出來、點得開、答得了，重新生成會把它們
  // 連同作答一起清掉。破壞性動作一律先確認（與刪除領域同一條規則）。
  if (!confirm('重新生成會清掉現有概念與其作答紀錄，確定要繼續嗎？')) return;
  button.disabled = true;
  status.textContent = '重新生成中…';
  try {
    await api(`/api/domains/${DOMAIN_ID}/regenerate`, { method: 'POST' });
  } catch (e) {
    status.textContent = `重新生成失敗：${e.message}`;
    button.disabled = false;
    return;
  }
  status.textContent = '';
  button.disabled = false;
  // 後端已把狀態寫回 generating，但輪詢條件是「只在 generating 時輪詢」，
  // 本機快取還停在 failed——不先改掉就永遠不會再輪詢。
  if (state.domain) state.domain.status = 'generating';
  poll();
};

document.getElementById('delete-domain').onclick = async () => {
  // 領域名是伺服器資料。confirm 收的是字串、不解析 HTML，故直接內插即可；
  // 仍先判空，重新載入失敗時 state.domain 會是 null，不能讓它擲出例外。
  const name = state.domain ? state.domain.name : '';
  if (!confirm(`確定要刪除「${name}」？這個動作無法復原。`)) return;
  const button = document.getElementById('delete-domain');
  button.disabled = true;
  try {
    await api(`/api/domains/${DOMAIN_ID}`, { method: 'DELETE' });
  } catch (e) {
    button.disabled = false;
    document.getElementById('domain-meta').textContent = `刪除失敗：${e.message}`;
    return;
  }
  // 刪掉之後這個頁面的每一個請求都會 404，只能回首頁
  location.href = '/';
};

document.getElementById('ask-question').onclick = async () => {
  const status = document.getElementById('answer-status');
  const button = document.getElementById('ask-question');
  const conceptId = state.current.id;
  // 出題與批改都要跑 Agent（數秒到十幾秒），按鈕不關掉時第二次點擊會再送一個
  // POST。exam／read／principle 題的後果不只白花錢：兩次批改落兩筆 attempt，
  // 而掌握度只看最近 5 筆、錯題本也會重複兩列。disabled 期間瀏覽器不派送 click，
  // finally 負責放回來（成功、失敗、提早 return 三條路徑都會經過）。
  button.disabled = true;
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
  } finally {
    button.disabled = false;
  }
};

document.getElementById('submit-answer').onclick = async () => {
  const status = document.getElementById('answer-status');
  const button = document.getElementById('submit-answer');
  const question = state.question;
  button.disabled = true;
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
  } finally {
    button.disabled = false;
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
let loaded = false;

async function poll() {
  if (inflight) return;
  inflight = true;
  try {
    await refresh();
    loaded = true;
  } catch (e) {
    // 服務重啟中或連不上：已載入過就完全不動畫面，停在最後一次成功的狀態，
    // 下一輪再試。**首次載入就失敗不能吞掉**（領域被刪或後端掛掉），否則整頁
    // 空白卻沒有任何訊息，看起來像壞掉。
    if (!loaded) {
      const box = document.getElementById('load-error');
      box.textContent = `載入失敗：${e.message}`;
      box.hidden = false;
    }
  } finally {
    inflight = false;
  }
}

poll();
// 只在生成中輪詢：failed 是終態（要靠「重新生成」離開），ready 也沒有東西會再變，
// 繼續輪詢等於每 3 秒白打一次後端。
setInterval(() => { if (!state.domain || state.domain.status === 'generating') poll(); }, 3000);
