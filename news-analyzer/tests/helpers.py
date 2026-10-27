"""測試共用 helper。"""
import json

from storage import get_conn


def set_analysis(article_id, score, summary, tags, db_path):
    """寫入分析結果（score/summary/tags）。

    生產路徑由 analyze_pending.py 的 Jev 流程寫入（不產 summary/tags），
    這裡是給測試用的，方便造出有摘要有標籤的資料。"""
    with get_conn(db_path) as conn:
        conn.execute(
            "UPDATE articles SET score=?, summary=?, tags=? WHERE id=?",
            (score, summary, json.dumps(tags, ensure_ascii=False), article_id),
        )
        conn.commit()
