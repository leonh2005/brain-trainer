// ccr → 本 proxy(8790) → routellm.abacus.ai
// 切換模型時，前面輪次的 thinking 區塊可能來自別家（例如 DeepSeek），Abacus 會以 invalid signature 拒絕。
// 這裡在送往 Abacus 前，移除最後一則真正使用者指令「之前」的 assistant thinking 區塊。
// 目前這一輪（含工具迴圈）的內容原樣保留。
const http = require("http");
const https = require("https");

const PORT = 8790;
const HOST = "127.0.0.1";
const UPSTREAM = "routellm.abacus.ai";
const THINKING_TYPES = new Set(["thinking", "redacted_thinking"]);
// DeepSeek 經 ccr 回傳的簽章是 UUID；Anthropic 的簽章是長 base64，兩者可區分
const FOREIGN_SIGNATURE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

function isForeignThinking(b) {
  return THINKING_TYPES.has(b?.type) && FOREIGN_SIGNATURE.test(b.signature || "");
}

function isTypedInstruction(content) {
  if (typeof content === "string") return isTypedText(content);
  if (!Array.isArray(content)) return false;
  if (content.some((b) => b?.type === "tool_result")) return false;
  return content.some((b) => b?.type === "text" && isTypedText(b.text));
}

function isTypedText(text) {
  const t = (text || "").trim();
  return t !== "" && !t.startsWith("<system-reminder>");
}

function sanitize(body) {
  const msgs = body?.messages;
  if (!Array.isArray(msgs)) return { body, removed: 0 };

  let boundary = -1;
  for (let i = msgs.length - 1; i >= 0; i--) {
    if (msgs[i]?.role === "user" && isTypedInstruction(msgs[i].content)) {
      boundary = i;
      break;
    }
  }
  if (boundary < 0) return { body, removed: 0 };

  let removed = 0;
  body.messages = msgs.map((m, i) => {
    if (m?.role !== "assistant" || !Array.isArray(m.content)) return m;
    const drop = (b) => (i < boundary ? THINKING_TYPES.has(b?.type) : isForeignThinking(b));
    const kept = m.content.filter((b) => !drop(b));
    removed += m.content.length - kept.length;
    if (kept.length === m.content.length) return m;
    return { ...m, content: kept.length ? kept : [{ type: "text", text: "." }] };
  });
  return { body, removed };
}

const server = http.createServer((req, res) => {
  const chunks = [];
  req.on("data", (c) => chunks.push(c));
  req.on("end", () => {
    let raw = Buffer.concat(chunks);

    if (req.method === "POST" && req.url.startsWith("/v1/messages")) {
      try {
        const { body, removed } = sanitize(JSON.parse(raw.toString("utf8")));
        if (removed) console.log(`${new Date().toISOString()} removed_thinking=${removed}`);
        raw = Buffer.from(JSON.stringify(body));
      } catch (e) {
        console.error(`${new Date().toISOString()} sanitize_failed ${e.message}, forwarding raw`);
      }
    }

    const headers = { ...req.headers, host: UPSTREAM, "content-length": raw.length };
    delete headers["transfer-encoding"];

    const up = https.request(
      { hostname: UPSTREAM, port: 443, path: req.url, method: req.method, headers },
      (ur) => {
        res.writeHead(ur.statusCode, ur.headers);
        ur.pipe(res);
      },
    );
    up.on("error", (e) => {
      console.error(`${new Date().toISOString()} upstream_error ${e.message}`);
      res.writeHead(502, { "content-type": "application/json" });
      res.end(JSON.stringify({ type: "error", error: { type: "api_error", message: `abacus-sanitizer upstream: ${e.message}` } }));
    });
    up.end(raw);
  });
});

server.listen(PORT, HOST, () => console.log(`abacus-sanitizer listening on ${HOST}:${PORT}`));
