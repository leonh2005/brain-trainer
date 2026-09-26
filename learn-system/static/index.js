async function loadDomains() {
  const res = await fetch('/api/domains');
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
