from db import get_db, init_db

def seed():
    init_db()
    with get_db() as conn:
        if conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0] > 0:
            print("Already seeded, skipping.")
            return

        conn.executemany(
            "INSERT INTO categories (name, emoji, color) VALUES (?, ?, ?)",
            [
                ("程式開發", "💻", "#00ff88"),
                ("投資交易", "📈", "#ffaa00"),
                ("身體健康", "🏋️", "#ff4466"),
                ("語言",     "🌐", "#44aaff"),
                ("其他",     "🎯", "#cc88ff"),
            ]
        )

        conn.executemany(
            "INSERT INTO skills (category_id, parent_id, name, description) VALUES (?, ?, ?, ?)",
            [
                (1, None, "Python",     "Python 程式語言"),
                (1, None, "Flask",      "Flask 後端框架"),
                (1, None, "JavaScript", "前端 JS"),
                (2, None, "技術分析",   "K線、均線等技術指標"),
                (2, None, "選股策略",   "多空篩選方法"),
                (3, None, "重訓",       "肌力訓練"),
                (3, None, "有氧",       "跑步、騎車等"),
                (4, None, "英文",       "英語能力"),
                (4, None, "日文",       "日語能力"),
                (5, None, "烹飪",       "料理技術"),
            ]
        )
        print("Seeded successfully.")

if __name__ == "__main__":
    seed()
