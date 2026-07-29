"""兩帳戶模擬配置（初始 seed 值，實際本金/標的可透過 API 修改，存於 DB）。"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Target:
    ticker: str
    market: str          # 'TW' | 'US'
    category: str
    target_twd: float
    build_method: str    # 'lump' | 'dca'
    id: int | None = None


@dataclass(frozen=True)
class Plan:
    plan_id: str
    name: str
    capital_twd: float
    dca_months: int
    targets: tuple


_A = (
    Target("00864B", "TW", "短債防禦", 1_000_000, "lump"),
    Target("XLP", "US", "穩健防禦", 900_000, "dca"),
    Target("XLU", "US", "穩健防禦", 900_000, "dca"),
    Target("GLD", "US", "穩健防禦", 900_000, "dca"),
    Target("EFV", "US", "全球進攻", 1_413_333.33, "dca"),
    Target("EWJ", "US", "全球進攻", 1_177_777.78, "dca"),
    Target("VWO", "US", "全球進攻", 942_222.22, "dca"),
    Target("0050", "TW", "全球進攻", 588_888.89, "dca"),
    Target("BE", "US", "AI基建衛星", 412_222.22, "lump"),
    Target("SNDK", "US", "AI基建衛星", 353_333.33, "lump"),
    Target("CORZ", "US", "AI基建衛星", 117_777.78, "lump"),
    Target("IREN", "US", "AI基建衛星", 117_777.78, "lump"),
    Target("CRWV", "US", "AI基建衛星", 176_666.67, "lump"),
)

_B = (
    Target("CRM", "US", "AI Agent", 350_000, "lump"),
    Target("MSFT", "US", "AI Agent", 250_000, "lump"),
    Target("NOW", "US", "AI Agent", 150_000, "lump"),
    Target("AAPL", "US", "AI Agent", 150_000, "lump"),
    Target("PLTR", "US", "AI Agent", 100_000, "lump"),
)

PLANS = {
    "A": Plan("A", "900萬全配置", 9_000_000, 6, _A),
    "B": Plan("B", "100萬 AI Agent 2.0", 1_000_000, 6, _B),
}


def ensure_accounts(conn):
    """若 accounts 表缺少 plan_id 對應的列，用 PLANS 預設值建立。"""
    import store
    from datetime import date
    today = date.today().isoformat()
    for plan_id, plan in PLANS.items():
        if store.get_account(conn, plan_id) is None:
            store.create_account(conn, plan_id, plan.name, plan_id, today, plan.capital_twd)


def seed_defaults(conn):
    """若 plan_targets 表該 plan_id 尚無資料，灌入 PLANS 的預設標的。"""
    import store
    for plan_id, plan in PLANS.items():
        if store.count_targets(conn, plan_id) == 0:
            for t in plan.targets:
                store.add_target(conn, plan_id, t.ticker, t.market, t.category,
                                 t.target_twd, t.build_method)


def load_plan(conn, plan_id):
    """組出目前生效的 Plan：本金讀 accounts 表，標的讀 plan_targets 表。"""
    import store
    base = PLANS[plan_id]
    acct = store.get_account(conn, plan_id)
    capital_twd = acct["capital_twd"] if acct else base.capital_twd
    targets = tuple(
        Target(r["ticker"], r["market"], r["category"], r["target_twd"],
               r["build_method"], id=r["id"])
        for r in store.get_targets(conn, plan_id)
    )
    return Plan(base.plan_id, base.name, capital_twd, base.dca_months, targets)
