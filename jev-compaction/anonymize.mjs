// 隱私處理層：送出給第三方 API 前，先做憑證遮蔽（redact）與識別資訊偽名化（anonymize）。
//
// 為什麼偽名化不需要還原：Jev 只回傳機率、不生成文字。
// 我們只需要它判斷「這筆舊輸出還相不相關」，不需要它看懂實際的檔名或主機名。
// 對照表只存在記憶體中，用途僅是維持同一次執行內代號一致，跑完即丟。
import fs from 'node:fs';

// 通用目錄名不可代號化：替換掉會破壞語意（Jev 反而看不懂「scripts」是什麼）。
const GENERIC_DIRS = new Set([
  'scripts', 'logs', 'log', 'data', 'reports', 'docs', 'templates', 'static', 'assets',
  'node_modules', '__pycache__', 'temp', 'tmp', 'backup', 'backups', 'config', 'conf',
  'test', 'tests', 'dist', 'build', 'public', 'images', 'img', 'media', 'cache',
  'output', 'outputs', 'input', 'src', 'lib', 'bin', 'files', 'downloads', 'archive',
]);

// shell 指令與常見工具名。專案目錄若恰好叫這些名字（本機真的有一個叫 mkdir 的），
// 全局替換會把每一行 Bash 輸出都污染掉——這比漏掉專案名更糟，因為會實質擾亂判斷。
const SHELL_COMMANDS = new Set([
  'mkdir', 'rmdir', 'grep', 'find', 'curl', 'wget', 'cat', 'ls', 'rm', 'cp', 'mv',
  'echo', 'sed', 'awk', 'python', 'python3', 'node', 'npm', 'npx', 'git', 'docker',
  'ssh', 'scp', 'rsync', 'tar', 'zip', 'unzip', 'kill', 'killall', 'ps', 'top', 'df',
  'du', 'chmod', 'chown', 'jq', 'sqlite', 'sqlite3', 'pip', 'pip3', 'brew', 'launchctl',
  'systemctl', 'crontab', 'tail', 'head', 'sort', 'uniq', 'wc', 'xargs', 'which',
  'env', 'export', 'source', 'bash', 'sh', 'zsh', 'make', 'gcc', 'java', 'go', 'cargo',
  'code', 'open', 'pbcopy', 'pbpaste', 'defaults', 'osascript', 'say', 'afplay',
  'screencapture', 'ffmpeg', 'convert', 'nginx', 'redis', 'mysql', 'psql', 'mongo',
  'main', 'test', 'app', 'run', 'start', 'stop', 'status', 'build', 'init', 'update',
]);

// 專案名單：路徑以外的純文字提及（例如「telebot 的卡片」）也需要代號化。
export function listEntities(root) {
  try {
    return fs.readdirSync(root, { withFileTypes: true })
      .filter((d) => d.isDirectory() && !d.name.startsWith('.')
        && !GENERIC_DIRS.has(d.name.toLowerCase())
        && !SHELL_COMMANDS.has(d.name.toLowerCase()))
      .map((d) => d.name);
  } catch {
    return [];
  }
}

// 憑證類遮蔽。順序重要：較具體的樣式要排在較通用的前面。
export const REDACTIONS = [
  [/-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----/g, '[REDACTED_PRIVATE_KEY]'],
  [/sk-ant-[A-Za-z0-9_-]{16,}/g, '[REDACTED_ANTHROPIC_KEY]'],
  [/sk-[A-Za-z0-9_-]{16,}/g, '[REDACTED_OPENAI_KEY]'],
  [/apikey_[A-Za-z0-9_]{16,}/g, '[REDACTED_TYPESAFE_KEY]'],
  [/AKIA[0-9A-Z]{16}/g, '[REDACTED_AWS_KEY]'],
  [/gh[pousr]_[A-Za-z0-9]{20,}/g, '[REDACTED_GITHUB_TOKEN]'],
  [/glpat-[A-Za-z0-9_-]{20,}/g, '[REDACTED_GITLAB_TOKEN]'],
  [/xox[baprs]-[A-Za-z0-9-]{10,}/g, '[REDACTED_SLACK_TOKEN]'],
  [/AIza[0-9A-Za-z_-]{35}/g, '[REDACTED_GOOGLE_KEY]'],
  [/\b\d{8,10}:[A-Za-z0-9_-]{35}\b/g, '[REDACTED_TELEGRAM_TOKEN]'],
  [/eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}/g, '[REDACTED_JWT]'],
  [/\b[A-Z][12]\d{8}\b/g, '[REDACTED_TW_ID]'],
  [/\b[\w.+-]+@[\w-]+\.[a-z]{2,}\b/gi, '[REDACTED_EMAIL]'],
  [/(Bearer\s+)[A-Za-z0-9._-]{10,}/g, '$1[REDACTED]'],
  [
    /((?:"|')?(?:password|passwd|pwd|api[_-]?key|token|secret)(?:"|')?\s*[:=]\s*)(?:"[^"]{4,}"|'[^']{4,}'|[A-Za-z0-9_\-./+]{12,})/gi,
    '$1[REDACTED]',
  ],
];

export function redact(text) {
  let out = text;
  for (const [re, rep] of REDACTIONS) out = out.replace(re, rep);
  return out;
}

export class Pseudonyms {
  constructor() { this.maps = {}; this.counts = {}; }
  code(kind, value) {
    if (!this.maps[kind]) this.maps[kind] = new Map();
    const m = this.maps[kind];
    if (!m.has(value)) m.set(value, `<${kind.toUpperCase()}_${m.size}>`);
    this.counts[kind] = (this.counts[kind] ?? 0) + 1;
    return m.get(value);
  }
}

// 只保留「看起來是檔案」的最後一層（有副檔名者）。
// 目錄名往往就是專案名（command-center、chip-tracker），本身就是識別資訊，必須一併代號化。
function safeTail(relativePath) {
  const segs = relativePath.split('/').filter(Boolean);
  const last = segs.at(-1) ?? '';
  return /\.[A-Za-z0-9]{1,8}$/.test(last) ? `${last}` : '';
}

// 家目錄下路徑：~/x 與 /Users/steven/x 指向同一物，必須對到同一個代號，
// 否則 Jev 會以為是兩個不同檔案，破壞相關性判斷。
function renderHomePath(raw, p) {
  const relative = raw.replace(/^~/, '').replace(/^\/Users\/steven/, '');
  if (!relative || relative === '/') return p.code('path', '/home');
  const trailingSlash = raw.endsWith('/') ? '/' : '';
  const code = p.code('path', relative);
  const tail = safeTail(relative);
  return tail ? `${code}/${tail}${trailingSlash}` : `${code}${trailingSlash}`;
}

export function anonymize(text, p, entities = []) {
  let out = text;
  // 家目錄路徑。負向 lookahead 避免把 /Users/stevenfoo 誤認成 /Users/steven + "foo"。
  out = out.replace(/\/Users\/steven(?![\w])(?:\/[^\s"'`)\]},;:|]*)?/g, (m) => renderHomePath(m, p));
  // ~ 只在單獨出現或 ~/ 開頭時匹配。lookahead 排除 ~5、20~30、~~刪節線~~ 這類非路徑寫法。
  out = out.replace(/~(?![\d~])(?:\/[^\s"'`)\]},;:|]*)?/g, (m) => renderHomePath(m, p));
  // 其他絕對路徑根：外接硬碟名（/Volumes/1TOWC）本身也是識別資訊。
  out = out.replace(/\/(?:Volumes\/[^\/\s"'`)\]},;:|]+|opt|tmp|var|etc|srv|mnt)(?:\/[^\s"'`)\]},;:|]*)?/g,
    (m) => p.code('syspath', m));
  // 內部服務位址
  out = out.replace(/\b(?:localhost|127\.0\.0\.1|0\.0\.0\.0)(?::\d{2,5})?/g, (m) => p.code('host', m));
  // 私有網段：要求至少 2 段後綴，避免把版本號（10.5）或價位（10.99）當成 IP。
  out = out.replace(/\b(?:10|192\.168|172\.(?:1[6-9]|2\d|3[01]))(?:\.\d{1,3}){2,3}(?::\d{2,5})?\b/g,
    (m) => p.code('ip', m));
  // 專案／服務名：放在路徑處理之後，此時路徑內的專案名已被吃掉，
  // 這裡處理的是純文字提及（如「telebot 的卡片」）。長的先換，避免部分匹配。
  for (const e of [...entities].sort((a, b) => b.length - a.length)) {
    if (e.length >= 3) out = out.split(e).join(p.code('project', e));
  }
  // 使用者名稱
  out = out.replace(/\bsteven\b/gi, () => p.code('user', 'steven'));
  return out;
}

// 實際的隱私處理入口。anon=false 時只做憑證遮蔽。
export function applyPrivacy(text, p, anon, entities = []) {
  const r = redact(text);
  return anon ? anonymize(r, p, entities) : r;
}
