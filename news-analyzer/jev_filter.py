#!/usr/bin/env python3
"""用 Jev 判斷新聞的相關性（是否該進入情緒分析管線）。

為什麼用 Jev：它只回傳帶機率的判斷、不生成文字，因此又快又便宜
（實測 2000 筆 81 秒、約 gpt-4o-mini 的 1/8 成本），且不受人工範例的偏差影響
（盲測顯示它在與人工標記分歧的案例中約 83% 是對的）。

純標準庫實作，不引入 requests。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

API_BASE = "https://api.typesafe.ai"
ENDPOINT = "/v1/systemone"
DEFAULT_MODEL = "jev-latest"
DEFAULT_KEY_PATH = Path.home() / "CCProject/.secrets/typesafe_jev_key.txt"

BATCH_SIZE = 25

TASK = "判斷財經新聞標題是否與投資決策相關（決定要不要進入情緒分析管線）"

# 依人工標記偏好調校過的定義（實測：與人工標記一致率 9/10，且盲測優於人工標記）
CLASSES = {
    "relevant": (
        "會影響投資決策的市場資訊。屬於此類：大盤與指數走勢、總體經濟數據（CPI/就業/GDP）"
        "與央行利率政策、重要個股或整體產業的實質動態（財報數字、財測調整、併購、法人買賣超、"
        "重大訂單合約、股價大幅波動）、原物料與能源價格、加密貨幣市場、市場分析與投資策略，"
        "以及會牽動市場的國際政經大事——元首會談與經貿談判、關稅與貿易政策、"
        "地緣衝突對供應鏈／能源／原物料的影響、國際制裁與科技管制。"
        "例：「Snowflake 財測上修股價大漲 23%」、「川習會談及貿易與 AI 議題」屬此類。"
    ),
    "irrelevant": (
        "與投資決策無直接關聯的新聞，下列各類均屬此類："
        "(1) 例行公司公告，只有行政事項、無實質營運或財務資訊者"
        "（如「董事會預計召開日期」、「職工代表公告」、「減資變更登記」、「員工認股權憑證」）"
        "(2) 與市場無關的純政治、外交、國際軍事新聞——若涉及貿易、關稅、供應鏈、能源、"
        "科技管制等市場議題則屬 relevant，純粹的內政或外交禮儀往來才歸此類 "
        "(3) 社會案件、司法糾紛、意外災害、天然災害 "
        "(4) 生活消費、產品推薦、展覽活動、品牌廣告 (5) 娛樂體育、名人訃聞與個人生活 "
        "(6) 個人理財與稅務規劃，例如退休金提撥上限、遺產規劃、退休時點決策（除非直接涉及證券市場走勢）"
        "(7) 企業新聞稿式的單一公司宣傳消息，例如獲獎、認證、非市場性的合作宣布 "
        "(8) 純科學、醫療、健康報導。"
        "例：「威力彩頭獎槓龜」、「木質生活展登場」屬此類。"
    ),
}

SENTIMENT_TASK = "判斷財經新聞對市場的方向是利多、利空或無明確方向"

# 三分類，對應前端統計門檻（score>=7 算多方、<=4 算空方）
SENTIMENT_CLASSES = {
    "bullish": (
        "會推升股價或市場情緒的利多消息。例如：財報優於預期、財測上修、"
        "重大訂單或併購、法人買超、政策利多、原物料或加密貨幣上漲、"
        "產業景氣轉強、大盤走揚。"
    ),
    "bearish": (
        "會壓低股價或市場情緒的利空消息。例如：財報不如預期、財測下修、"
        "裁員或營運危機、法人賣超、政策打壓、違約或財務疑慮、"
        "原物料或加密貨幣下跌、產業景氣轉弱、大盤走弱。"
    ),
    "neutral": (
        "與市場有關但沒有明確多空方向者。例如：股價持平或小幅震盪、"
        "營運數據符合預期、中性的事件報導、多空因素並陳、"
        "與方向無關的市場制度或規則說明。"
    ),
}

# 分類 → 前端使用的 1-10 情緒分數（5 為中性）
SENTIMENT_SCORE = {"bullish": 10, "bearish": 1, "neutral": 5}


class JevFilterError(RuntimeError):
    """Jev 呼叫失敗。呼叫端應據此跳過本批，不要讓整條管線中斷。"""


def load_api_key(key_path: Path | str | None = None) -> str:
    if os.environ.get("TYPESAFE_API_KEY"):
        return os.environ["TYPESAFE_API_KEY"]
    path = Path(key_path or DEFAULT_KEY_PATH)
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as e:
        raise JevFilterError(f"找不到 TypeSafe API key：{path}") from e


def _post(body: dict, api_key: str, timeout: float) -> dict:
    req = urllib.request.Request(
        API_BASE + ENDPOINT,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise JevFilterError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}") from e
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        raise JevFilterError(f"{type(e).__name__}: {e}") from e


def _classify(titles: list[str], classes: dict, task: str, api_key: str,
              timeout: float, model: str) -> list[tuple[str, dict]]:
    """送一批標題給 Jev 分類，回傳 [(選中的類別, 機率分布), ...]，順序與輸入一致。"""
    if not titles:
        return []

    outputs = "\n\n".join(f"【{i}】{t}" for i, t in enumerate(titles))
    questions = {
        f"out{i}": {
            "type": "choice",
            "instructions": f"state.news 裡編號【{i}】的新聞標題，該歸為哪一類？",
            "criteria": classes,
        }
        for i in range(len(titles))
    }
    data = _post({"state": {"task": task, "news": outputs},
                  "questions": questions, "model": model}, api_key, timeout)

    answers = data.get("answers")
    if not isinstance(answers, dict):
        raise JevFilterError(f"回應缺少 answers：{json.dumps(data)[:200]}")

    out: list[tuple[str, dict]] = []
    for i in range(len(titles)):
        ans = answers.get(f"out{i}")
        if not isinstance(ans, dict):
            raise JevFilterError(f"回應缺少 out{i}")
        # choice 的答案可能是字串（選中的 key）或機率分布
        pick = ans.get("choice")
        probs = ans.get("probabilities") or ans.get("probs") or {}
        if not isinstance(pick, str):
            pick = max(probs, key=probs.get) if probs else None
        if pick is None:
            raise JevFilterError(f"out{i} 無法解析分類")
        out.append((pick, probs))
    return out


def classify(titles: list[str], api_key: str, timeout: float = 30.0,
             model: str = DEFAULT_MODEL) -> list[dict]:
    """判斷相關性。回傳 [{relevant: bool, cls: str, probs: {...}}, ...]

    順序與輸入一致。整批失敗時丟 JevFilterError（由呼叫端決定怎麼退）。
    """
    return [{"cls": pick, "relevant": pick == "relevant", "probs": probs}
            for pick, probs in _classify(titles, CLASSES, TASK, api_key, timeout, model)]


def classify_sentiment(titles: list[str], api_key: str, timeout: float = 30.0,
                       model: str = DEFAULT_MODEL) -> list[dict]:
    """判斷多空方向。回傳 [{sentiment: str, probs: {...}}, ...]

    只適用於已判定為相關的新聞；不相關的新聞不該送進來。
    """
    return [{"sentiment": pick, "probs": probs}
            for pick, probs in _classify(titles, SENTIMENT_CLASSES, SENTIMENT_TASK,
                                         api_key, timeout, model)]


def _classify_all(classify_fn, titles: list[str], api_key: str | None,
                  batch_size: int, timeout: float) -> list[dict]:
    key = api_key or load_api_key()
    results: list[dict] = []
    for i in range(0, len(titles), batch_size):
        results.extend(classify_fn(titles[i:i + batch_size], key, timeout=timeout))
    return results


def classify_all(titles: list[str], api_key: str | None = None,
                 batch_size: int = BATCH_SIZE, timeout: float = 30.0) -> list[dict]:
    """分批判斷相關性。"""
    return _classify_all(classify, titles, api_key, batch_size, timeout)


def classify_sentiment_all(titles: list[str], api_key: str | None = None,
                           batch_size: int = BATCH_SIZE, timeout: float = 30.0) -> list[dict]:
    """分批判斷多空方向。只該用在已判定相關的新聞上。"""
    return _classify_all(classify_sentiment, titles, api_key, batch_size, timeout)
