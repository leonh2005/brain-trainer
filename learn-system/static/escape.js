// 所有插入 DOM 的伺服器資料一律走這裡，避免領域／概念名稱含 HTML 時破壞頁面
function escapeHtml(s) {
  const div = document.createElement('div');
  div.textContent = String(s ?? '');
  return div.innerHTML;
}
