#!/usr/bin/env python3
"""
安全的靜態檔案伺服器 —— 給 com.steven.portfolio-monitor 用。

為什麼不用 `python -m http.server --directory ~/CCProject`：
  那會把整棵 CCProject（含 .secrets/ 裡的 18 支 API key）透過 HTTP 開在
  127.0.0.1 上，任何本機程序都能讀走。2026-10-27 體檢時實測確認可讀。

本程式行為與 http.server 相同，但**拒絕任何含隱藏路徑成分的請求**
（.secrets/、.git/、.env、__pycache__/ 等一律 403）。

用法：safe_static_server.py <port> <directory>
"""
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


class SafeHandler(SimpleHTTPRequestHandler):
    """繼承標準 handler，只在送出檔案前擋掉隱藏路徑。"""

    def _is_hidden(self) -> bool:
        path = self.path.split("?", 1)[0].split("#", 1)[0]
        for part in path.split("/"):
            if not part:
                continue
            if part.startswith("."):
                return True
            if part in ("__pycache__", "node_modules", "venv"):
                return True
        return False

    def send_head(self):
        if self._is_hidden():
            self.send_error(403, "Forbidden: hidden path")
            return None
        return super().send_head()


def main() -> None:
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    port = int(sys.argv[1])
    directory = sys.argv[2]
    handler = partial(SafeHandler, directory=directory)
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    print(f"safe static server on 127.0.0.1:{port} -> {directory}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
