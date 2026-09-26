// 輪詢同時只允許一個請求在途：/api/domains 的成本隨領域數成長（每個領域都要
// 再查掌握度與概念），背景生成又會寫 SQLite，讀取可能因鎖競爭而變慢。少了這道
// 守衛，某次回應一旦超過 5 秒，下一輪不會等待，請求就無上限堆積。
let inflight = false;

async function loadDomains() {
  if (inflight) return;
  inflight = true;
  try {
    const res = await fetch('/api/domains');
    if (!res.ok) return;
    const { domains } = await res.json();
    const wrap = document.getElementById('cards');
    wrap.replaceChildren();
    if (!domains.length) {
      const p = document.createElement('p');
      p.textContent = '還沒有任何領域，點右上角新增。';
      wrap.append(p);
      return;
    }
    for (const d of domains) {
      const card = document.createElement('div');
      card.className = 'card';
      card.onclick = () => location.href = `/domain/${d.id}`;

      const h = document.createElement('h3');
      h.textContent = d.name;
      card.append(h);

      const meta = document.createElement('div');
      meta.className = 'meta';
      meta.textContent = `${d.concept_count} 個概念 · 掌握 ${d.mastery_pct}%`;
      card.append(meta);

      if (d.status !== 'ready') {
        const s = document.createElement('div');
        s.className = 'meta';
        s.textContent = d.status === 'generating' ? '生成中…' : '生成失敗';
        card.append(s);
      }

      const bar = document.createElement('div');
      bar.className = 'bar';
      const fill = document.createElement('i');
      fill.style.width = `${d.mastery_pct}%`;
      bar.append(fill);
      card.append(bar);
      wrap.append(card);
    }
  } catch {
    // 服務重啟中、連不上或回傳非 JSON：這次完全不動畫面，讓清單停在最後一次
    // 成功的狀態（看得到但可能過期，勝過一片空白），下一輪再試。不顯示錯誤
    // 橫幅、不做退避重試——本機單人工具不需要。
  } finally {
    // 這行是自我修復的關鍵：成功、失敗、提早 return 三條路徑都會經過它，
    // 少了它，一次失敗就會讓守衛卡在 true，輪詢永久停擺。
    inflight = false;
  }
}

document.getElementById('new-domain').onclick = () => {
  document.getElementById('new-dialog').showModal();
};

document.getElementById('create-form').onsubmit = async (e) => {
  e.preventDefault();
  const goals = [...document.querySelectorAll('input[name=goal]:checked')].map(i => i.value);
  if (!goals.length) { alert('至少要勾選一個目標'); return; }
  const res = await fetch('/api/domains', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      name: document.getElementById('name').value,
      goals,
      verify_sources: document.getElementById('verify').checked,
    }),
  });
  const body = await res.json();
  if (!res.ok) { alert(body.error); return; }
  location.href = `/domain/${body.id}`;
};

loadDomains();
setInterval(loadDomains, 5000);
