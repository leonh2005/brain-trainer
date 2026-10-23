// ccr 自訂路由（CUSTOM_ROUTER_PATH）
// 1. claude-3-sonnet / claude-3-haiku 偽裝 → gpt 備用
// 2. cauto（用官方 id claude-3-7-sonnet 觸發）：
//    只在使用者輸入新指令時由 Jev 判難度，其餘請求沿用該 session 目前層級（不在工具迴圈中途切換）
//    0-2 → Haiku、3 → Sonnet、4-5 → Opus（皆走 Abacus，經 abacus-sanitizer 清理 thinking）
// 3. 其餘放行
// 每個請求都把實際路由寫進 route-state/<session>.json，供狀態列顯示與跨重啟沿用

const fs = require("fs");

// 白名單：Jev 只能從這裡挑，選不到清單外的模型
const TIERS = {
  t1: "abacus/claude-haiku-5-5",
  t2: "abacus/claude-sonnet-5-5",
  t3: "abacus/claude-opus-5-5",
};
// Abacus 掛掉或額度不足時退回直連 DeepSeek（不耗 Abacus 點數）
const FALLBACK = "deepseek/deepseek-flash";
// 不直接依賴 ccr 內建 fallback：它在第二個 attempt 沒有重算 content-length，DeepSeek 那次會在連線前 502
const ABACUS_HEALTH = "/Users/steven/.claude-code-router/abacus-health.json";
const DS_HEALTH = "/Users/steven/.claude-code-router/ds-health.json";
const PROBE_INTERVAL_S = 15 * 60;
const ABACUS_PROBE_URL = "http://127.0.0.1:8791/health";
const PIN_FILE = "/Users/steven/.claude-code-router/route-pin.json";
const STATE_DIR = "/Users/steven/.claude-code-router/route-state";
const LOG = "/tmp/jev-router.log";
const SHAPE_LOG = "/tmp/cc-req-shape.jsonl"; // TEMP: 驗證 Claude Code 請求格式後移除

module.exports = async function (request, config, ctx) {
  const model = request?.body?.model || request?.model;
  const sessionId = getSessionId(request);
  const messages = request?.body?.messages || [];
  fs.appendFileSync(SHAPE_LOG, JSON.stringify({ t: new Date().toISOString(), model, sid: sessionId, shape: shape(messages) }) + "\n");

  if (model === "claude-3-sonnet") return remember(sessionId, model, "claude-openai/gpt-4o", "gpt-backup", null);
  if (model === "claude-3-haiku") return remember(sessionId, model, "claude-openai/gpt-4o-mini", "gpt-backup", null);

  if (model === "claude-3-7-sonnet") {
    const pin = pinnedProvider();
    if (pin === "deepseek" || (!pin && (await pickProvider()) === "deepseek")) {
      const route = pin ? "manual-deepseek" : "abacus-down";
      fs.appendFileSync(LOG, `${new Date().toISOString()} ${sessionId} ${route} -> ${FALLBACK}\n`);
      return remember(sessionId, model, FALLBACK, route, undefined);
    }

    const state = readState(sessionId);
    if (state.tier && !isNewInstruction(messages)) {
      return remember(sessionId, model, state.tier, "jev-sticky", undefined);
    }

    try {
      // ccr gateway 進程沒有 TYPESAFE_API_KEY，從 .secrets 補上（SDK 從環境變數讀）
      if (!process.env.TYPESAFE_API_KEY) {
        process.env.TYPESAFE_API_KEY = fs
          .readFileSync("/Users/steven/CCProject/.secrets/typesafe_jev_key.txt", "utf8")
          .trim();
      }
      const { TypeSafeClient } = require("/opt/homebrew/lib/node_modules/@typesafe-ai/sdk");
      const client = new TypeSafeClient();
      const prompt = extractUserPrompt(request);
      const r = await client.systemOne({
        state: { task: prompt },
        questions: {
          complexity: {
            type: "score",
            instructions:
              "判斷這個任務的複雜度。0=極簡單（改錯字、查詢、單檔小修改）、5=極複雜（多檔重構、深度架構、複雜 debug、長程推理）",
            criteria: ["極簡單", "簡單", "中等", "偏難", "困難", "極難"],
          },
        },
      });
      const score = r.answers.complexity.score; // 0–5
      const chosen = score >= 4 ? TIERS.t3 : score >= 3 ? TIERS.t2 : TIERS.t1;
      fs.appendFileSync(LOG, `${new Date().toISOString()} ${sessionId} score=${score} -> ${chosen}\n`);
      return remember(sessionId, model, chosen, "jev", chosen);
    } catch (e) {
      // Jev 判斷失敗 → 這一輪退回直連 DeepSeek，層級不變
      fs.appendFileSync(LOG, `${new Date().toISOString()} ${sessionId} ERROR ${String(e).slice(0, 200)}\n`);
      return remember(sessionId, model, FALLBACK, "jev-error", undefined);
    }
  }

  // 其餘（claude 預設 = Flash）放行
  return remember(sessionId, model, null, "passthrough", undefined);
};

// 手動釘住：route-pin.json 的 provider 為 deepseek 或 abacus 時，略過冷卻判斷，直到移除該檔
function pinnedProvider() {
  try {
    const p = JSON.parse(fs.readFileSync(PIN_FILE, "utf8")).provider;
    return p === "deepseek" || p === "abacus" ? p : undefined;
  } catch (e) {
    return undefined;
  }
}

// 選路：Abacus 優先；Abacus 冷卻中改用 DeepSeek；兩邊都冷卻時回到 Abacus。
// 冷卻期過後放行一個請求試探，成功會由 proxy 清除冷卻標記，失敗會重新標記。
async function pickProvider() {
  const a = coolState(ABACUS_HEALTH);
  if (a === "down") {
    const d = coolState(DS_HEALTH);
    if (d === "down") return "abacus";
    if (d === "due") claimProbe(DS_HEALTH);
    return "deepseek";
  }
  if (a === "due") claimProbe(ABACUS_HEALTH);
  if (!(await probeOk(ABACUS_PROBE_URL))) {
    return coolState(DS_HEALTH) === "down" ? "abacus" : "deepseek";
  }
  return "abacus";
}

function coolState(file) {
  let mark;
  try {
    mark = JSON.parse(fs.readFileSync(file, "utf8"));
  } catch (e) {
    return "ok";
  }
  const age = Date.now() / 1000 - Number(mark.ts);
  return age < PROBE_INTERVAL_S ? "down" : "due";
}

function claimProbe(file) {
  try {
    const tmp = `${file}.${process.pid}.${Math.random().toString(36).slice(2)}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify({ status: "probe", ts: Date.now() / 1000 }));
    fs.renameSync(tmp, file);
  } catch (e) {
    console.error("[route] 寫入探測標記失敗:", e.message);
  }
}

async function probeOk(url) {
  try {
    const r = await fetch(url, { signal: AbortSignal.timeout(800) });
    return r.ok;
  } catch (e) {
    return false;
  }
}

function readState(sessionId) {
  try {
    return JSON.parse(fs.readFileSync(stateFile(sessionId), "utf8"));
  } catch (e) {
    return {};
  }
}

function stateFile(sessionId) {
  return `${STATE_DIR}/${sessionId.replace(/[^\w-]/g, "_")}.json`;
}

// tier === undefined 表示保留原層級
function remember(sessionId, requested, resolved, route, tier) {
  try {
    fs.mkdirSync(STATE_DIR, { recursive: true });
    const file = stateFile(sessionId);
    const keep = tier === undefined ? readState(sessionId).tier ?? null : tier;
    const tmp = `${file}.${process.pid}.${Math.random().toString(36).slice(2)}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify({ requested, resolved, route, tier: keep, updated: new Date().toISOString() }));
    fs.renameSync(tmp, file);
  } catch (e) {
    console.error("[route-state] 寫入失敗:", e.message);
  }
  return resolved;
}

// 新指令 = 最後一則 user 訊息含使用者真正打的文字（不是工具結果、也不是系統提醒）
function isNewInstruction(messages) {
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i]?.role !== "user") continue;
    return hasTypedText(messages[i].content);
  }
  return false;
}

function hasTypedText(content) {
  if (typeof content === "string") return isTypedText(content);
  if (!Array.isArray(content)) return false;
  if (content.some((b) => b?.type === "tool_result")) return false;
  return content.some((b) => b?.type === "text" && isTypedText(b.text));
}

function isTypedText(text) {
  const t = (text || "").trim();
  return t !== "" && !t.startsWith("<system-reminder>");
}

function shape(messages) {
  return messages.slice(-4).map((m) => ({
    role: m.role,
    blocks: Array.isArray(m.content)
      ? m.content.map((b) => ({ type: b?.type, len: (b?.text || "").length, head: (b?.text || "").slice(0, 40) }))
      : [{ type: "string", len: (m.content || "").length, head: String(m.content || "").slice(0, 40) }],
  }));
}

function getSessionId(request) {
  const h = request?.headers || {};
  return h["x-claude-code-session-id"] || h["X-Claude-Code-Session-Id"] || "unknown";
}

function extractUserPrompt(request) {
  try {
    const msgs = request?.body?.messages;
    if (Array.isArray(msgs)) {
      for (let i = msgs.length - 1; i >= 0; i--) {
        if (msgs[i]?.role === "user") {
          const c = msgs[i].content;
          if (typeof c === "string") return c.slice(0, 2000);
          if (Array.isArray(c)) {
            const text = c
              .filter((x) => x?.type === "text")
              .map((x) => x.text)
              .join(" ")
              .slice(0, 2000);
            if (text) return text;
          }
        }
      }
    }
    return typeof request?.body?.system === "string" ? request.body.system.slice(0, 500) : "(empty)";
  } catch (e) {
    return "(empty)";
  }
}
