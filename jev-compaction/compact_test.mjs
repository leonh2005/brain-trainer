// 模擬 LiteLLM 的 Jev context 壓縮：
// 對一段真實對話中「已累積的舊 tool 輸出」，問 Jev 是否還與最新的使用者問題相關。
// 低於門檻者即為可壓縮對象。
//
// 用法：
//   node compact_test.mjs             # baseline：模擬時刻之前的舊輸出
//   node compact_test.mjs --anchor    # anchor：同一任務執行中的輸出（真陽性對照組）
//
// 注意：這會把 transcript 內容送到 TypeSafe 雲端。redact() 已盡量遮蔽憑證，
// 但接進正式管線前仍須由使用者確認隱私政策。
import fs from 'node:fs';
import path from 'node:path';
import { Pseudonyms, applyPrivacy, listEntities } from './anonymize.mjs';

const HOME = process.env.HOME;
const TRANSCRIPT = path.join(
  HOME, '.claude/projects/-Users-steven-CCProject/dea502fc-1d26-406d-aee2-6f8881a18099.jsonl'
);
const KEY_PATH = path.join(HOME, 'CCProject/.secrets/typesafe_jev_key.txt');

// --anchor：改測「同一條任務線實際執行時產生的 tool 輸出」（真陽性對照組）
const MODE = process.argv.includes('--anchor') ? 'anchor' : 'baseline';
const ANON = process.argv.includes('--anon');
const OUT_PATH = path.join(HOME, `CCProject/jev-compaction/result-${MODE}${ANON ? '-anon' : ''}.json`);

const TARGET_LINE = 5472;   // 模擬時刻：第 120 個使用者提問
const ANCHOR_END = 5904;    // 錨點模式：到第 121 個提問為止（同為 DCF 任務）
const TASK = '使用者在問：http://localhost:5950/svc/5800/ 只輸入代碼無法帶出中文股名，'
  + '想辦法要可以互相帶出（輸入代碼要顯示中文股名）。這是 control-center 上的 DCF 查詢頁。';

const BATCH_SIZE = 25;      // 每批送幾個候選（parallel questions）
const MIN_CHARS = 200;      // LiteLLM 的候選門檻
const TRUNC = 600;          // 送給 Jev 的每筆字元上限
const THRESHOLD = 0.2;      // LiteLLM 的預設相關性門檻
const BATCH_TIMEOUT_MS = 60_000;  // 單批上限，避免 Jev 無回應時整支懸置

function withTimeout(promise, ms) {
  return Promise.race([
    promise,
    new Promise((_, reject) => setTimeout(() => reject(new Error(`逾時 ${ms}ms`)), ms)),
  ]);
}

// 隱私處理（憑證遮蔽 + 偽名化）抽在 anonymize.mjs，供其他腳本共用。
const pseudonyms = new Pseudonyms();
// 專案名單：處理純文字中對專案的提及（不只路徑形式）。
const ENTITIES = listEntities(path.join(HOME, 'CCProject'));
// current_task 本身也含識別資訊（localhost:5950、control-center），必須一併處理，
// 且必須與 tool_outputs 共用同一組代號，否則 Jev 看到的任務與內容對不起來。
const SAFE_TASK = applyPrivacy(TASK, pseudonyms, ANON, ENTITIES);

// 定位 npx 快取中的 SDK（不硬編 hash，npx 重裝後仍可用）
function resolveSdk() {
  const local = path.join(HOME, 'CCProject/jev-compaction/node_modules/@typesafe-ai/sdk/dist/index.mjs');
  if (fs.existsSync(local)) return local;
  const npxRoot = path.join(HOME, '.npm/_npx');
  if (fs.existsSync(npxRoot)) {
    for (const entry of fs.readdirSync(npxRoot)) {
      const p = path.join(npxRoot, entry, 'node_modules/@typesafe-ai/sdk/dist/index.mjs');
      if (fs.existsSync(p)) return p;
    }
  }
  throw new Error('找不到 @typesafe-ai/sdk。請在 jev-compaction 執行：npm i @typesafe-ai/sdk');
}

// 取出指定行區間的 tool 輸出（tool_result），過濾掉太短者
function collectCandidates(file, startLine, endLine) {
  const toolNames = new Map();
  const cands = [];
  let lineNo = 0;
  for (const line of fs.readFileSync(file, 'utf8').split('\n')) {
    lineNo += 1;
    if (lineNo >= endLine) break;
    if (!line.includes('tool_use') && !line.includes('tool_result')) continue;
    let obj;
    try { obj = JSON.parse(line); } catch { continue; }
    const content = obj?.message?.content;
    if (!Array.isArray(content)) continue;
    for (const blk of content) {
      if (!blk || typeof blk !== 'object') continue;
      if (blk.type === 'tool_use') toolNames.set(blk.id, blk.name);
      else if (blk.type === 'tool_result') {
        if (lineNo < startLine) continue;
        const raw = Array.isArray(blk.content)
          ? blk.content.filter((b) => b && typeof b === 'object').map((b) => b.text ?? '').join('')
          : (typeof blk.content === 'string' ? blk.content : '');
        if (raw.length < MIN_CHARS) continue;
        cands.push({
          id: blk.tool_use_id,
          tool: toolNames.get(blk.tool_use_id) ?? '?',
          chars: raw.length,
          text: applyPrivacy(raw.slice(0, TRUNC), pseudonyms, ANON, ENTITIES),
        });
      }
    }
  }
  return cands;
}

async function scoreBatch(client, noul, batch, offset) {
  const outputs = batch.map((c, i) => `【${offset + i}】(${c.tool}, ${c.chars} 字元)\n${c.text}`).join('\n\n');
  const questions = {};
  batch.forEach((_, i) => {
    questions[`out${offset + i}`] = noul(
      `state.tool_outputs 裡編號【${offset + i}】的工具輸出，是否仍是回答 state.current_task 所必要的資訊？`,
      { true: '仍是回答該問題所需的資訊', false: '與回答該問題無關，可以移除' }
    );
  });
  const res = await client.systemOne({
    state: { current_task: SAFE_TASK, tool_outputs: outputs },
    questions,
    model: 'jev-latest',
  });
  if (!res?.answers || typeof res.answers !== 'object') {
    throw new Error('回傳格式不符：缺少 answers');
  }
  const missing = Object.keys(questions).filter((k) => !(k in res.answers));
  if (missing.length) {
    throw new Error(`回傳缺少 ${missing.length} 個答案（例如 ${missing[0]}）`);
  }
  return { answers: res.answers, usage: res.usage };
}

const apiKey = fs.readFileSync(KEY_PATH, 'utf8').trim();
const sdk = await import(resolveSdk());
const client = new sdk.TypeSafeClient({ apiKey });

const [startLine, endLine] = MODE === 'anchor' ? [TARGET_LINE, ANCHOR_END] : [1, TARGET_LINE];
const cands = collectCandidates(TRANSCRIPT, startLine, endLine);
const totalChars = cands.reduce((s, c) => s + c.chars, 0);
console.log(`模式：${MODE}（第 ${startLine}–${endLine} 行，endLine 不含）`);
console.log(`候選 tool 輸出：${cands.length} 筆，共 ${totalChars.toLocaleString()} 字元`);
console.log(`模擬任務：${TASK.slice(0, 60)}...\n`);

if (cands.length === 0) {
  console.error('沒有候選，結束。');
  process.exit(1);
}

const scored = [];
let totalUsage = { input_tokens: 0, output_tokens: 0 };
let failedBatches = 0;

for (let offset = 0; offset < cands.length; offset += BATCH_SIZE) {
  const batch = cands.slice(offset, offset + BATCH_SIZE);
  const done = Math.min(offset + BATCH_SIZE, cands.length);
  try {
    const { answers, usage } = await withTimeout(
      scoreBatch(client, sdk.noul, batch, offset), BATCH_TIMEOUT_MS
    );
    totalUsage.input_tokens += usage?.input_tokens ?? 0;
    totalUsage.output_tokens += usage?.output_tokens ?? 0;
    batch.forEach((c, i) => {
      const p = answers[`out${offset + i}`]?.noul ?? null;
      scored.push({ ...c, relevance: p, kept: p === null ? true : p >= THRESHOLD });
    });
  } catch (e) {
    failedBatches += 1;
    console.error(`\n  批次 ${offset}–${done} 失敗（保留不壓縮）：${e.message}`);
    for (const c of batch) scored.push({ ...c, relevance: null, kept: true });
  }
  process.stdout.write(`  已評分 ${done}/${cands.length}\r`);
}

console.log('\n');
const dropped = scored.filter((s) => !s.kept);
const kept = scored.filter((s) => s.kept);
const nulls = scored.filter((s) => s.relevance === null).length;
const droppedChars = dropped.reduce((s, c) => s + c.chars, 0);

console.log('=== 結果 ===');
console.log(`  保留 ${kept.length} 筆 (${kept.reduce((s, c) => s + c.chars, 0).toLocaleString()} 字元)`);
console.log(`  判定可壓縮 ${dropped.length} 筆 (${droppedChars.toLocaleString()} 字元)`);
console.log(`  省下比例：${(100 * droppedChars / totalChars).toFixed(1)}% 的字元（約 ${Math.round(droppedChars / 4).toLocaleString()} tokens）`);
console.log(`  Jev 用量：input ${totalUsage.input_tokens.toLocaleString()} / output ${totalUsage.output_tokens.toLocaleString()} tokens`);
console.log(`  評估成本 ≈ $${(totalUsage.input_tokens * 0.042 / 1e6).toFixed(5)}`);
if (failedBatches || nulls) console.log(`  ⚠ 失敗批次 ${failedBatches}，未取得分數而被保留 ${nulls} 筆`);
const totalBatches = Math.ceil(cands.length / BATCH_SIZE);
if (failedBatches === totalBatches) {
  console.error('  ✗ 全部批次都失敗，結果不具參考價值。');
  process.exitCode = 1;
}

console.log('\n=== 機率分布 ===');
const bins = [0, 0.1, 0.2, 0.4, 0.6, 0.8, 0.95, 1.01];
for (let i = 0; i < bins.length - 1; i++) {
  const n = scored.filter((s) => s.relevance !== null && s.relevance >= bins[i] && s.relevance < bins[i + 1]).length;
  console.log(`  ${bins[i].toFixed(2)}–${bins[i + 1].toFixed(2)}: ${'█'.repeat(Math.ceil(n / 2))} ${n}`);
}

console.log('\n=== 被判定可壓縮的最大 10 筆 ===');
for (const c of [...dropped].sort((a, b) => b.chars - a.chars).slice(0, 10)) {
  console.log(`  ${c.chars.toLocaleString().padStart(7)} 字元  p=${(c.relevance ?? -1).toFixed(3)}  ${c.tool.padEnd(30)} ${c.text.slice(0, 45).replace(/\n/g, ' ')}`);
}

console.log('\n=== 被保留的最大 10 筆（確認沒砍錯）===');
for (const c of [...kept].sort((a, b) => b.chars - a.chars).slice(0, 10)) {
  console.log(`  ${c.chars.toLocaleString().padStart(7)} 字元  p=${(c.relevance ?? -1).toFixed(3)}  ${c.tool.padEnd(30)} ${c.text.slice(0, 45).replace(/\n/g, ' ')}`);
}

fs.writeFileSync(OUT_PATH, JSON.stringify({
  mode: MODE, task: TASK, target_line: TARGET_LINE, threshold: THRESHOLD, batch_size: BATCH_SIZE,
  total: { count: cands.length, chars: totalChars },
  dropped: { count: dropped.length, chars: droppedChars },
  failed_batches: failedBatches,
  usage: totalUsage,
  results: scored.map(({ id, tool, chars, relevance, kept }) => ({ id, tool, chars, relevance, kept })),
}, null, 2));
console.log(`\n詳細結果已寫入 ${OUT_PATH}`);
