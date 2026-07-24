"""兩帳戶模擬配置（定案版，見 specs/2026-07-24-sim-invest-design.md）。"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Target:
    ticker: str
    market: str          # 'TW' | 'US'
    category: str
    target_twd: float
    build_method: str    # 'lump' | 'dca'


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
