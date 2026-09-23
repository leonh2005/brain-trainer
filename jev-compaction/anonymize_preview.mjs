// 偽名化層的效果展示：印出「原始 → 送出」的實際差異，與殘留檢查。
// 邏輯與 compact_test.mjs 共用 anonymize.mjs，確保兩邊行為一致。
//
// 用法：node anonymize_preview.mjs
import fs from 'node:fs';
import path from 'node:path';
import { Pseudonyms, applyPrivacy, listEntities } from './anonymize.mjs';

const HOME = process.env.HOME;
const TRANSCRIPT = path.join(
  HOME, '.claude/projects/-Users-steven-CCProject/dea502fc-1d26-406d-aee2-6f8881a18099.jsonl'
);
const TARGET_LINE = 5472;
const MIN_CHARS = 200;
const TRUNC = 600;

function collectCandidates(file, startLine, endLine) {
  const cands = [];
  let lineNo = 0;
  for (const line of fs.readFileSync(file, 'utf8').split('\n')) {
    lineNo += 1;
    if (lineNo >= endLine) break;
    if (!line.includes('tool_result')) continue;
    let obj;
    try { obj = JSON.parse(line); } catch { continue; }
    const content = obj?.message?.content;
    if (!Array.isArray(content)) continue;
    for (const blk of content) {
      if (!blk || typeof blk !== 'object' || blk.type !== 'tool_result') continue;
      if (lineNo < startLine) continue;
      const raw = Array.isArray(blk.content)
        ? blk.content.filter((b) => b && typeof b === 'object').map((b) => b.text ?? '').join('')
        : (typeof blk.content === 'string' ? blk.content : '');
      if (raw.length >= MIN_CHARS) cands.push(raw.slice(0, TRUNC));
    }
  }
  return cands;
}

const cands = collectCandidates(TRANSCRIPT, 1, TARGET_LINE);
const p = new Pseudonyms();
const ENTITIES = listEntities(path.join(HOME, 'CCProject'));
const sent = cands.map((t) => applyPrivacy(t, p, true, ENTITIES));

const origChars = cands.reduce((s, t) => s + t.length, 0);
const sentChars = sent.reduce((s, t) => s + t.length, 0);

console.log(`候選 ${cands.length} 筆\n`);
console.log('=== 代號對照表（僅存在記憶體，跑完即丟）===');
for (const [kind, m] of Object.entries(p.maps)) {
  console.log(`  ${kind}: ${m.size} 個不重複項目，出現 ${p.counts[kind]} 次`);
}
console.log(`\n字元數：原始 ${origChars.toLocaleString()} → 送出 ${sentChars.toLocaleString()}`);

const samples = [];
for (let i = 0; i < cands.length && samples.length < 3; i++) {
  if (cands[i] === sent[i]) continue;
  let j = 0;
  while (j < Math.min(cands[i].length, sent[i].length) && cands[i][j] === sent[i][j]) j++;
  samples.push({
    before: cands[i].slice(Math.max(0, j - 60), j + 170).replace(/\n/g, ' '),
    after: sent[i].slice(Math.max(0, j - 60), j + 170).replace(/\n/g, ' '),
  });
}
console.log('\n=== 實際對照（第一處差異的前後文）===');
samples.forEach((s, i) => {
  console.log(`\n--- 樣本 ${i + 1} ---`);
  console.log(`  原始：${s.before}`);
  console.log(`  送出：${s.after}`);
});

// 殘留檢查：這些字串不該出現在送出的內容裡
const leaks = {
  '/Users/steven': (t) => t.includes('/Users/steven'),
  'localhost': (t) => t.includes('localhost'),
  '使用者名 steven': (t) => /\bsteven\b/i.test(t),
  '專案名 command-center': (t) => t.includes('command-center'),
  '專案名 chip-tracker': (t) => t.includes('chip-tracker'),
  '專案名 news-analyzer': (t) => t.includes('news-analyzer'),
  '專案名 telebot': (t) => t.includes('telebot'),
  '專案名 market-dashboard': (t) => t.includes('market-dashboard'),
  '家目錄寫法 ~/': (t) => t.includes('~/'),
};
console.log('\n=== 送出內容的殘留檢查 ===');
for (const [name, test] of Object.entries(leaks)) {
  const n = sent.filter(test).length;
  console.log(`  ${n === 0 ? '✓' : '⚠'} ${name}: ${n} 筆`);
}

// 過度替換檢查：這些字串在原文出現幾次，送出後應該一樣多（被換掉就是誤傷）
const overreach = ['mkdir', 'grep', 'find', 'curl', 'sudo', 'python3', '10.5', '~5', '20~30'];
console.log('\n=== 過度替換檢查（送出後筆數應與原始相同）===');
for (const word of overreach) {
  const before = cands.filter((t) => t.includes(word)).length;
  const after = sent.filter((t) => t.includes(word)).length;
  const ok = before === after;
  console.log(`  ${ok ? '✓' : '⚠'} ${word}: 原始 ${before} 筆 → 送出 ${after} 筆${ok ? '' : `（${before - after} 筆被誤換）`}`);
}
