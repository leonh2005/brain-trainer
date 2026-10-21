const fs = require("fs");
const path = require("path");
const os = require("os");

const STATE_DIR = path.join(os.homedir(), ".claude-code-router", "route-state");

const LABELS = {
  "abacus/claude-haiku-5-5": "Haiku 5.5 (Abacus)",
  "abacus/claude-sonnet-5-5": "Sonnet 5.5 (Abacus)",
  "abacus/claude-opus-5-5": "Opus 5.5 (Abacus)",
  "deepseek/deepseek-flash": "DeepSeek Flash (直連)",
  "deepseek/deepseek-flash[1m]": "DeepSeek Flash (直連)",
  "deepseek/deepseek-v4-pro": "DeepSeek V4 Pro (直連)",
  "deepseek-flash": "DeepSeek Flash (直連)",
  "deepseek-flash[1m]": "DeepSeek Flash (直連)",
  "deepseek-v4-pro": "DeepSeek V4 Pro (直連)",
  "claude-openai/gpt-4o": "GPT-4o (OpenAI)",
  "claude-openai/gpt-4o-mini": "GPT-4o mini (OpenAI)",
};

const ROUTES = {
  "jev": "Jev 判斷",
  "jev-sticky": "Jev 沿用",
  "jev-error": "Jev 失敗→DeepSeek",
  "gpt-backup": "gpt 備用",
  "passthrough": "直通",
};

function readStdin() {
  return new Promise((resolve) => {
    let data = "";
    process.stdin.on("data", (c) => (data += c));
    process.stdin.on("end", () => resolve(data));
  });
}

(async () => {
  let sessionId = "";
  try {
    sessionId = JSON.parse(await readStdin()).session_id || "";
  } catch (e) {
    sessionId = "";
  }

  const safe = sessionId.replace(/[^\w-]/g, "_");
  const file = path.join(STATE_DIR, `${safe}.json`);

  let state;
  try {
    state = JSON.parse(fs.readFileSync(file, "utf8"));
  } catch (e) {
    console.log("路由 ▸ 尚未記錄");
    return;
  }

  const key = state.resolved || state.requested || "?";
  const label = LABELS[key] || key;
  const route = ROUTES[state.route] || state.route;
  const time = state.updated ? new Date(state.updated).toLocaleTimeString("zh-TW", { hour12: false }) : "";
  console.log(`路由 ▸ ${label} · ${route} · ${time}`);
})();
