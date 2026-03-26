"""
Playwright browser manager — controls real AI chat websites.
No API keys needed; copies session from user's real Firefox profile.
"""
import threading
import time
import os

# Persistent profile dir — login sessions are saved here permanently
SESSION_DIR = os.path.expanduser("~/.ai-arena-ff-session")
os.makedirs(SESSION_DIR, exist_ok=True)

SERVICES = {
    "claude":  {"name": "Claude",   "color": "#7C3AED", "chat_url": "https://claude.ai/new",           "home_url": "https://claude.ai"},
    "chatgpt": {"name": "ChatGPT",  "color": "#10A37F", "chat_url": "https://chatgpt.com",             "home_url": "https://chatgpt.com"},
    "gemini":  {"name": "Gemini",   "color": "#4285F4", "chat_url": "https://gemini.google.com/app",   "home_url": "https://gemini.google.com"},
    "grok":    {"name": "Grok",     "color": "#1A1A1A", "chat_url": "https://grok.com",                "home_url": "https://grok.com"},
    "copilot": {"name": "Copilot",  "color": "#0078D4", "chat_url": "https://copilot.microsoft.com",   "home_url": "https://copilot.microsoft.com"},
}

# ── JavaScript: get latest assistant response text ──────────────────────────
RESPONSE_JS = {
    "claude": """() => {
        const sel = [
            '.font-claude-message',
            '[data-testid="assistant-message"]',
            '.font-claude-message-prose',
        ];
        for (const s of sel) {
            const els = document.querySelectorAll(s);
            if (els.length) return els[els.length - 1].innerText.trim();
        }
        // Generic fallback: last .prose block
        const prose = document.querySelectorAll('.prose');
        return prose.length ? prose[prose.length - 1].innerText.trim() : '';
    }""",

    "chatgpt": """() => {
        const msgs = document.querySelectorAll('[data-message-author-role="assistant"]');
        if (!msgs.length) return '';
        const last = msgs[msgs.length - 1];
        const md = last.querySelector('.markdown');
        return (md || last).innerText.trim();
    }""",

    "gemini": """() => {
        const byTag = document.querySelectorAll('model-response');
        if (byTag.length) return byTag[byTag.length - 1].innerText.trim();
        const byCls = document.querySelectorAll(
            '.response-container, .response-content-chunk, [class*="response"]'
        );
        return byCls.length ? byCls[byCls.length - 1].innerText.trim() : '';
    }""",

    "grok": """() => {
        const sels = [
            '[class*="AssistantMessage"]',
            '[data-message-role="assistant"]',
            '[class*="message-bubble"]:not([class*="user"])',
        ];
        for (const s of sels) {
            const els = document.querySelectorAll(s);
            if (els.length) return els[els.length - 1].innerText.trim();
        }
        return '';
    }""",

    "copilot": """() => {
        const sels = [
            '[data-content="ai-message"]',
            '[class*="AIMessage"]',
            'cib-message[slot="default"]',
        ];
        for (const s of sels) {
            const els = document.querySelectorAll(s);
            if (els.length) return els[els.length - 1].innerText.trim();
        }
        return '';
    }""",
}

# ── Input selectors (first match wins) ──────────────────────────────────────
INPUT_SELECTORS = {
    "claude":  ['div[contenteditable="true"]', '.ProseMirror'],
    "chatgpt": ['#prompt-textarea', 'textarea'],
    "gemini":  ['rich-textarea div[contenteditable="true"]', 'div[contenteditable="true"]'],
    "grok":    ['textarea', 'div[contenteditable="true"]'],
    "copilot": ['textarea', 'cib-text-input textarea', 'div[contenteditable="true"]'],
}


def _is_contenteditable(selector: str) -> bool:
    return 'contenteditable' in selector


class BrowserManager:
    def __init__(self):
        self._pw = None
        self._context = None
        self._pages: dict = {}
        self._lock = threading.Lock()
        self._started = False

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def start(self):
        if self._started:
            return
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()

        self._context = self._pw.firefox.launch_persistent_context(
            SESSION_DIR,
            headless=False,
            executable_path="/Applications/Firefox.app/Contents/MacOS/firefox",
            firefox_user_prefs={
                "browser.startup.page": 0,
                "browser.startup.homepage": "about:blank",
            },
        )
        self._started = True

    def stop(self):
        try:
            if self._context:
                self._context.close()
            if self._pw:
                self._pw.stop()
        except Exception:
            pass

    # ── Status ─────────────────────────────────────────────────────────────

    def get_status(self) -> dict:
        result = {}
        for sid in SERVICES:
            page = self._pages.get(sid)
            if not page:
                result[sid] = {"opened": False, "logged_in": False}
                continue
            try:
                logged_in = self._check_login(sid, page)
                result[sid] = {"opened": True, "logged_in": logged_in}
            except Exception:
                result[sid] = {"opened": True, "logged_in": False}
        return result

    def _check_login(self, sid: str, page) -> bool:
        """Check current URL to determine if user is logged in."""
        try:
            url = page.url or ""
        except Exception:
            return False

        # Login = we're on the service's main domain and NOT on a login/auth page
        NOT_LOGGED = ["login", "/auth", "signin", "sign-in",
                      "accounts.google", "login.microsoft", "about:blank", ""]
        checks = {
            "claude":  lambda u: "claude.ai" in u,
            "chatgpt": lambda u: "chatgpt.com" in u,
            "gemini":  lambda u: "gemini.google.com" in u,
            "grok":    lambda u: "grok.com" in u,
            "copilot": lambda u: "copilot.microsoft.com" in u,
        }
        fn = checks.get(sid, lambda u: False)
        if not fn(url):
            return False
        return not any(kw in url for kw in NOT_LOGGED)

    # ── Open service tab ───────────────────────────────────────────────────

    def open_service(self, sid: str):
        if sid not in SERVICES or not self._started:
            return
        with self._lock:
            if sid not in self._pages:
                page = self._context.new_page()
                self._pages[sid] = page
            else:
                page = self._pages[sid]
        page.bring_to_front()
        # Navigate to home (login page) if we haven't loaded anything yet
        if page.url in ("", "about:blank"):
            page.goto(SERVICES[sid]["home_url"])

    # ── Send message ───────────────────────────────────────────────────────

    def send_message(self, sid: str, message: str, out_queue, new_chat: bool = False):
        t = threading.Thread(
            target=self._worker,
            args=(sid, message, out_queue, new_chat),
            daemon=True,
        )
        t.start()

    def _worker(self, sid, message, out_queue, new_chat):
        try:
            page = self._pages.get(sid)
            if not page:
                out_queue.put({"type": "error", "content": f"請先點「開啟」並登入 {SERVICES[sid]['name']}"})
                return

            if not self._check_login(sid, page):
                out_queue.put({"type": "error", "content": f"{SERVICES[sid]['name']} 尚未登入，請在瀏覽器中登入"})
                return

            if new_chat:
                page.goto(SERVICES[sid]["chat_url"])
                page.wait_for_load_state("domcontentloaded", timeout=15000)
                time.sleep(2)

            self._type_and_send(sid, page, message)
            self._poll(sid, page, out_queue)

        except Exception as e:
            out_queue.put({"type": "error", "content": str(e)})

    def _type_and_send(self, sid, page, message):
        selectors = INPUT_SELECTORS.get(sid, ["textarea"])
        el = None

        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    el = loc
                    break
            except Exception:
                continue

        if not el:
            raise RuntimeError(f"找不到 {SERVICES[sid]['name']} 的輸入框，頁面可能尚未載入")

        el.click()
        time.sleep(0.3)

        # Clear any existing content
        page.keyboard.press("Control+a")
        time.sleep(0.1)

        # Insert text
        if _is_contenteditable(selectors[0]):
            # For contenteditable / ProseMirror editors
            page.keyboard.type(message)
        else:
            el.fill(message)

        time.sleep(0.2)

        # Submit: try Enter (most services), fallback to clicking send button
        page.keyboard.press("Enter")

    def _poll(self, sid, page, out_queue):
        """Poll DOM for response text and stream deltas to out_queue."""
        last_text = ""
        last_change = time.time()
        response_started = False
        start = time.time()
        MAX_WAIT = 120

        time.sleep(1.5)  # Give the page time to start responding

        while time.time() - start < MAX_WAIT:
            try:
                script = RESPONSE_JS.get(sid, "() => ''")
                text = page.evaluate(script) or ""

                if text and text != last_text:
                    if len(text) > len(last_text):
                        delta = text[len(last_text):]
                        out_queue.put({"type": "chunk", "content": delta})
                    else:
                        # Text was truncated / replaced (unlikely but handle it)
                        out_queue.put({"type": "reset", "content": text})
                    last_text = text
                    last_change = time.time()
                    response_started = True

                # Declare done if text has been stable for 2.5 seconds
                if response_started and (time.time() - last_change) > 2.5:
                    out_queue.put({"type": "done"})
                    return

            except Exception:
                pass

            time.sleep(0.4)

        if last_text:
            out_queue.put({"type": "done"})
        else:
            out_queue.put({"type": "error", "content": "等待回應逾時（120 秒）"})

    # ── New conversation ───────────────────────────────────────────────────

    def new_chat(self, sid: str):
        page = self._pages.get(sid)
        if page:
            page.goto(SERVICES[sid]["chat_url"])


manager = BrowserManager()
