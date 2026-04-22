# 凱利×費波那契×週期 倉位計算器 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立 Flask 網頁計算器，整合凱利公式、費波那契回檔位與四大經濟週期，輸出最佳倉位建議並可下載含蒙地卡羅模擬的 Excel 模型。

**Architecture:** 模組化 Flask 應用，計算邏輯拆為獨立模組（kelly / fibonacci / cycles / montecarlo），Excel 由 excel_builder 單獨負責。前端單頁表單透過 AJAX `/calculate` 取得即時結果，`/download` 端點動態產出 .xlsx 回傳。

**Tech Stack:** Python 3, Flask, numpy, xlsxwriter, pytest

---

## 檔案結構

```
/Users/steven/CCProject/kelly-fibonacci/
├── app.py                    # Flask routes: GET /, POST /calculate, POST /download
├── kelly.py                  # raw_kelly(), adjusted_kelly(), position_sizes()
├── fibonacci.py              # FIB_LEVELS 常數, get_fib_params()
├── cycles.py                 # CYCLE_WEIGHTS 常數, resonance_score()
├── montecarlo.py             # run_simulation(), simulation_stats()
├── excel_builder.py          # build_excel() → bytes
├── templates/
│   └── index.html            # 單頁表單 + 結果 + 使用說明
├── requirements.txt
└── tests/
    ├── test_kelly.py
    ├── test_fibonacci.py
    ├── test_cycles.py
    └── test_montecarlo.py
```

---

### Task 1: 建立專案目錄與依賴

**Files:**
- Create: `/Users/steven/CCProject/kelly-fibonacci/requirements.txt`

- [ ] **Step 1: 建立目錄與 venv**

```bash
mkdir -p /Users/steven/CCProject/kelly-fibonacci/templates
mkdir -p /Users/steven/CCProject/kelly-fibonacci/tests
cd /Users/steven/CCProject/kelly-fibonacci
python3 -m venv venv
source venv/bin/activate
```

- [ ] **Step 2: 建立 requirements.txt**

```
flask==3.0.3
numpy==1.26.4
xlsxwriter==3.2.0
pytest==8.2.0
```

- [ ] **Step 3: 安裝依賴**

```bash
cd /Users/steven/CCProject/kelly-fibonacci
source venv/bin/activate
pip install -r requirements.txt
```

Expected: Successfully installed flask, numpy, xlsxwriter, pytest

- [ ] **Step 4: Commit**

```bash
cd /Users/steven/CCProject/kelly-fibonacci
git add requirements.txt
cd /Users/steven/CCProject && git add kelly-fibonacci/requirements.txt kelly-fibonacci/
git commit -m "feat: init kelly-fibonacci project structure"
```

---

### Task 2: fibonacci.py — 回檔位映射

**Files:**
- Create: `kelly-fibonacci/fibonacci.py`
- Create: `kelly-fibonacci/tests/test_fibonacci.py`

- [ ] **Step 1: 寫失敗測試**

建立 `kelly-fibonacci/tests/test_fibonacci.py`：

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from fibonacci import get_fib_params, FIB_LEVELS

def test_fib_levels_keys():
    assert set(FIB_LEVELS.keys()) == {23.6, 38.2, 50.0, 61.8, 78.6}

def test_get_fib_params_38():
    params = get_fib_params(38.2)
    assert params['entry_multiplier'] == 0.8
    assert params['reward_ratio'] == 2.0

def test_get_fib_params_61():
    params = get_fib_params(61.8)
    assert params['entry_multiplier'] == 1.0
    assert params['reward_ratio'] == 3.0

def test_deeper_retracement_higher_multiplier():
    m_38 = get_fib_params(38.2)['entry_multiplier']
    m_61 = get_fib_params(61.8)['entry_multiplier']
    assert m_61 > m_38
```

- [ ] **Step 2: 執行確認失敗**

```bash
cd /Users/steven/CCProject/kelly-fibonacci
source venv/bin/activate && python -m pytest tests/test_fibonacci.py -v
```

Expected: ModuleNotFoundError: No module named 'fibonacci'

- [ ] **Step 3: 實作 fibonacci.py**

建立 `kelly-fibonacci/fibonacci.py`：

```python
FIB_LEVELS = {
    23.6: {'entry_multiplier': 0.6, 'reward_ratio': 1.5},
    38.2: {'entry_multiplier': 0.8, 'reward_ratio': 2.0},
    50.0: {'entry_multiplier': 0.9, 'reward_ratio': 2.5},
    61.8: {'entry_multiplier': 1.0, 'reward_ratio': 3.0},
    78.6: {'entry_multiplier': 1.1, 'reward_ratio': 4.0},
}

def get_fib_params(level: float) -> dict:
    return FIB_LEVELS[level]
```

- [ ] **Step 4: 執行確認通過**

```bash
cd /Users/steven/CCProject/kelly-fibonacci
source venv/bin/activate && python -m pytest tests/test_fibonacci.py -v
```

Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
cd /Users/steven/CCProject
git add kelly-fibonacci/fibonacci.py kelly-fibonacci/tests/test_fibonacci.py
git commit -m "feat: fibonacci retracement level mapping"
```

---

### Task 3: cycles.py — 四週期共振分數

**Files:**
- Create: `kelly-fibonacci/cycles.py`
- Create: `kelly-fibonacci/tests/test_cycles.py`

- [ ] **Step 1: 寫失敗測試**

建立 `kelly-fibonacci/tests/test_cycles.py`：

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from cycles import resonance_score, CYCLE_WEIGHTS

def test_weights_sum_to_one():
    assert abs(sum(CYCLE_WEIGHTS.values()) - 1.0) < 1e-9

def test_all_neutral_gives_one():
    score, multiplier = resonance_score(0, 0, 0, 0)
    assert score == 0.0
    assert multiplier == 1.0

def test_all_max_expansion():
    score, multiplier = resonance_score(2, 2, 2, 2)
    assert abs(score - 2.0) < 1e-9
    assert abs(multiplier - 1.5) < 1e-9

def test_all_max_contraction():
    score, multiplier = resonance_score(-2, -2, -2, -2)
    assert abs(score - (-2.0)) < 1e-9
    assert abs(multiplier - 0.5) < 1e-9

def test_weighted_mix():
    # kitchin=2(w=0.15), juglar=0(w=0.25), kuznets=0(w=0.30), kondratiev=0(w=0.30)
    score, multiplier = resonance_score(2, 0, 0, 0)
    assert abs(score - 0.30) < 1e-9
    # multiplier = 1.0 + (0.30/2.0)*0.5 = 1.075
    assert abs(multiplier - 1.075) < 1e-9
```

- [ ] **Step 2: 執行確認失敗**

```bash
cd /Users/steven/CCProject/kelly-fibonacci
source venv/bin/activate && python -m pytest tests/test_cycles.py -v
```

Expected: ModuleNotFoundError: No module named 'cycles'

- [ ] **Step 3: 實作 cycles.py**

建立 `kelly-fibonacci/cycles.py`：

```python
CYCLE_WEIGHTS = {
    'kitchin': 0.15,
    'juglar': 0.25,
    'kuznets': 0.30,
    'kondratiev': 0.30,
}

def resonance_score(kitchin: float, juglar: float, kuznets: float, kondratiev: float) -> tuple[float, float]:
    """
    Returns (weighted_score, position_multiplier).
    Inputs range -2 to +2. Multiplier maps [-2,+2] → [0.5, 1.5].
    """
    score = (
        kitchin * CYCLE_WEIGHTS['kitchin'] +
        juglar * CYCLE_WEIGHTS['juglar'] +
        kuznets * CYCLE_WEIGHTS['kuznets'] +
        kondratiev * CYCLE_WEIGHTS['kondratiev']
    )
    multiplier = 1.0 + (score / 2.0) * 0.5
    return round(score, 4), round(multiplier, 4)
```

- [ ] **Step 4: 執行確認通過**

```bash
cd /Users/steven/CCProject/kelly-fibonacci
source venv/bin/activate && python -m pytest tests/test_cycles.py -v
```

Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
cd /Users/steven/CCProject
git add kelly-fibonacci/cycles.py kelly-fibonacci/tests/test_cycles.py
git commit -m "feat: four-cycle resonance score calculation"
```

---

### Task 4: kelly.py — 凱利公式核心

**Files:**
- Create: `kelly-fibonacci/kelly.py`
- Create: `kelly-fibonacci/tests/test_kelly.py`

- [ ] **Step 1: 寫失敗測試**

建立 `kelly-fibonacci/tests/test_kelly.py`：

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from kelly import raw_kelly, adjusted_kelly, position_sizes

def test_raw_kelly_classic():
    # 勝率 0.6, 賠率 2.0 → (0.6*2 - 0.4)/2 = 0.4
    assert abs(raw_kelly(0.6, 2.0) - 0.4) < 1e-9

def test_raw_kelly_no_edge():
    # 勝率 0.5, 賠率 1.0 → (0.5*1 - 0.5)/1 = 0
    assert raw_kelly(0.5, 1.0) == 0.0

def test_adjusted_kelly_neutral():
    # cycle_multiplier=1.0, fib_multiplier=1.0 → same as raw
    assert abs(adjusted_kelly(0.6, 2.0, 1.0, 1.0) - 0.4) < 1e-9

def test_adjusted_kelly_with_multipliers():
    # 0.4 * 1.2 * 0.8 = 0.384
    assert abs(adjusted_kelly(0.6, 2.0, 1.2, 0.8) - 0.384) < 1e-9

def test_position_sizes():
    sizes = position_sizes(capital=100000, kelly_pct=0.25)
    assert sizes['full_kelly'] == 25000.0
    assert sizes['half_kelly'] == 12500.0
```

- [ ] **Step 2: 執行確認失敗**

```bash
cd /Users/steven/CCProject/kelly-fibonacci
source venv/bin/activate && python -m pytest tests/test_kelly.py -v
```

Expected: ModuleNotFoundError: No module named 'kelly'

- [ ] **Step 3: 實作 kelly.py**

建立 `kelly-fibonacci/kelly.py`：

```python
def raw_kelly(win_rate: float, odds: float) -> float:
    """Kelly fraction: (win_rate * odds - lose_rate) / odds"""
    lose_rate = 1.0 - win_rate
    return (win_rate * odds - lose_rate) / odds

def adjusted_kelly(win_rate: float, odds: float, cycle_multiplier: float, fib_multiplier: float) -> float:
    """Apply cycle resonance and fibonacci entry multipliers to raw Kelly."""
    return raw_kelly(win_rate, odds) * cycle_multiplier * fib_multiplier

def position_sizes(capital: float, kelly_pct: float) -> dict:
    """Return full and half Kelly position sizes in currency units."""
    full = round(capital * kelly_pct, 2)
    return {'full_kelly': full, 'half_kelly': round(full * 0.5, 2)}
```

- [ ] **Step 4: 執行確認通過**

```bash
cd /Users/steven/CCProject/kelly-fibonacci
source venv/bin/activate && python -m pytest tests/test_kelly.py -v
```

Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
cd /Users/steven/CCProject
git add kelly-fibonacci/kelly.py kelly-fibonacci/tests/test_kelly.py
git commit -m "feat: Kelly formula core with cycle and fibonacci adjustments"
```

---

### Task 5: montecarlo.py — 蒙地卡羅模擬

**Files:**
- Create: `kelly-fibonacci/montecarlo.py`
- Create: `kelly-fibonacci/tests/test_montecarlo.py`

- [ ] **Step 1: 寫失敗測試**

建立 `kelly-fibonacci/tests/test_montecarlo.py`：

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import numpy as np
from montecarlo import run_simulation, simulation_stats

def test_simulation_shape():
    curves = run_simulation(0.55, 0.2, 100000, n_trades=50, n_simulations=100, seed=42)
    assert curves.shape == (100, 51)  # 100 paths, 51 columns (initial + 50 trades)

def test_simulation_initial_capital():
    curves = run_simulation(0.55, 0.2, 100000, n_trades=50, n_simulations=100, seed=42)
    assert np.all(curves[:, 0] == 100000)

def test_stats_keys():
    curves = run_simulation(0.55, 0.2, 100000, n_trades=50, n_simulations=200, seed=42)
    stats = simulation_stats(curves, initial_capital=100000)
    for key in ['p5', 'p50', 'p95', 'max_drawdown_mean', 'max_drawdown_std',
                'max_drawdown_worst', 'ruin_rate']:
        assert key in stats

def test_ruin_rate_type():
    curves = run_simulation(0.55, 0.2, 100000, n_trades=50, n_simulations=100, seed=42)
    stats = simulation_stats(curves, initial_capital=100000)
    assert 0.0 <= stats['ruin_rate'] <= 1.0
```

- [ ] **Step 2: 執行確認失敗**

```bash
cd /Users/steven/CCProject/kelly-fibonacci
source venv/bin/activate && python -m pytest tests/test_montecarlo.py -v
```

Expected: ModuleNotFoundError: No module named 'montecarlo'

- [ ] **Step 3: 實作 montecarlo.py**

建立 `kelly-fibonacci/montecarlo.py`：

```python
import numpy as np

def run_simulation(
    win_rate: float,
    kelly_fraction: float,
    initial_capital: float,
    n_trades: int = 200,
    n_simulations: int = 1000,
    seed: int = None
) -> np.ndarray:
    """
    Returns equity curves of shape (n_simulations, n_trades + 1).
    Column 0 is initial_capital; each subsequent column is capital after that trade.
    Each win multiplies capital by (1 + kelly_fraction); each loss by (1 - kelly_fraction).
    """
    rng = np.random.default_rng(seed)
    wins = rng.random((n_simulations, n_trades)) < win_rate
    factors = np.where(wins, 1.0 + kelly_fraction, 1.0 - kelly_fraction)
    cumulative = np.cumprod(factors, axis=1)
    equity = initial_capital * cumulative
    initial_col = np.full((n_simulations, 1), initial_capital)
    return np.hstack([initial_col, equity])

def simulation_stats(curves: np.ndarray, initial_capital: float) -> dict:
    """Compute summary statistics from equity curves."""
    final_values = curves[:, -1]
    p5 = float(np.percentile(final_values, 5))
    p50 = float(np.percentile(final_values, 50))
    p95 = float(np.percentile(final_values, 95))

    # Max drawdown per path: peak-to-trough
    running_max = np.maximum.accumulate(curves, axis=1)
    drawdowns = (running_max - curves) / running_max
    max_drawdowns = np.max(drawdowns, axis=1)

    ruin_threshold = initial_capital * 0.5
    ruin_rate = float(np.mean(final_values < ruin_threshold))

    return {
        'p5': round(p5, 2),
        'p50': round(p50, 2),
        'p95': round(p95, 2),
        'max_drawdown_mean': round(float(np.mean(max_drawdowns)) * 100, 2),
        'max_drawdown_std': round(float(np.std(max_drawdowns)) * 100, 2),
        'max_drawdown_worst': round(float(np.max(max_drawdowns)) * 100, 2),
        'ruin_rate': round(ruin_rate * 100, 2),
    }
```

- [ ] **Step 4: 執行確認通過**

```bash
cd /Users/steven/CCProject/kelly-fibonacci
source venv/bin/activate && python -m pytest tests/test_montecarlo.py -v
```

Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
cd /Users/steven/CCProject
git add kelly-fibonacci/montecarlo.py kelly-fibonacci/tests/test_montecarlo.py
git commit -m "feat: Monte Carlo simulation with equity curves and drawdown stats"
```

---

### Task 6: excel_builder.py — Excel 輸出（4 工作表）

**Files:**
- Create: `kelly-fibonacci/excel_builder.py`

- [ ] **Step 1: 實作 excel_builder.py**

建立 `kelly-fibonacci/excel_builder.py`：

```python
import io
import numpy as np
import xlsxwriter

def build_excel(params: dict, results: dict, curves: np.ndarray) -> bytes:
    """
    params: dict with keys capital, win_rate, odds, fib_level, kitchin, juglar, kuznets, kondratiev
    results: dict with keys raw_kelly, cycle_score, cycle_multiplier, fib_multiplier,
                            adj_kelly, full_kelly_amt, half_kelly_amt, stats
    curves: np.ndarray shape (1000, n_trades+1)
    Returns bytes of .xlsx file.
    """
    output = io.BytesIO()
    wb = xlsxwriter.Workbook(output, {'in_memory': True})

    _sheet_summary(wb, params, results)
    _sheet_montecarlo(wb, curves, params['capital'])
    _sheet_drawdown(wb, curves, params['capital'], results['stats'])
    _sheet_cycles(wb, params, results)

    wb.close()
    return output.getvalue()


def _sheet_summary(wb, params, results):
    ws = wb.add_worksheet('總覽')
    bold = wb.add_format({'bold': True})
    money = wb.add_format({'num_format': '#,##0'})
    pct = wb.add_format({'num_format': '0.00%'})

    rows = [
        ('=== 輸入參數 ===', ''),
        ('初始資金', params['capital']),
        ('勝率', params['win_rate'] / 100),
        ('賠率（風險報酬比）', params['odds']),
        ('費波那契回檔位', f"{params['fib_level']}%"),
        ('基欽週期位置', params['kitchin']),
        ('朱格拉週期位置', params['juglar']),
        ('庫茲涅茨週期位置', params['kuznets']),
        ('康波週期位置', params['kondratiev']),
        ('', ''),
        ('=== 計算結果 ===', ''),
        ('原始凱利%', results['raw_kelly'] / 100),
        ('週期共振分數', results['cycle_score']),
        ('週期共振乘數', results['cycle_multiplier']),
        ('費波那契入場乘數', results['fib_multiplier']),
        ('調整後凱利%', results['adj_kelly'] / 100),
        ('建議全凱利倉位（$）', results['full_kelly_amt']),
        ('建議半凱利倉位（$）', results['half_kelly_amt']),
        ('', ''),
        ('=== 蒙地卡羅統計（1000次）===', ''),
        ('最終資金 5th percentile', results['stats']['p5']),
        ('最終資金中位數', results['stats']['p50']),
        ('最終資金 95th percentile', results['stats']['p95']),
        ('平均最大回撤', f"{results['stats']['max_drawdown_mean']}%"),
        ('最大回撤標準差', f"{results['stats']['max_drawdown_std']}%"),
        ('最大回撤最差值', f"{results['stats']['max_drawdown_worst']}%"),
        ('破產率（跌破初始50%）', f"{results['stats']['ruin_rate']}%"),
    ]

    for i, (label, value) in enumerate(rows):
        if label.startswith('==='):
            ws.write(i, 0, label, bold)
        else:
            ws.write(i, 0, label)
            if isinstance(value, float) and label.endswith('%') is False and '倉位' in label:
                ws.write(i, 1, value, money)
            else:
                ws.write(i, 1, value)

    ws.set_column(0, 0, 32)
    ws.set_column(1, 1, 18)


def _sheet_montecarlo(wb, curves, initial_capital):
    ws = wb.add_worksheet('蒙地卡羅曲線')
    n_sim, n_cols = curves.shape

    # Sample 100 paths for display (every 10th)
    sample_idx = list(range(0, n_sim, n_sim // 100))[:100]
    sample = curves[sample_idx, :]

    # Write header
    ws.write(0, 0, '交易次數')
    for j, idx in enumerate(sample_idx):
        ws.write(0, j + 1, f'路徑{idx+1}')
    ws.write(0, len(sample_idx) + 1, '5th %ile')
    ws.write(0, len(sample_idx) + 2, '中位數')
    ws.write(0, len(sample_idx) + 3, '95th %ile')

    p5 = np.percentile(curves, 5, axis=0)
    p50 = np.percentile(curves, 50, axis=0)
    p95 = np.percentile(curves, 95, axis=0)

    for i in range(n_cols):
        ws.write(i + 1, 0, i)
        for j, path in enumerate(sample):
            ws.write(i + 1, j + 1, round(float(path[i]), 2))
        ws.write(i + 1, len(sample_idx) + 1, round(float(p5[i]), 2))
        ws.write(i + 1, len(sample_idx) + 2, round(float(p50[i]), 2))
        ws.write(i + 1, len(sample_idx) + 3, round(float(p95[i]), 2))

    # Chart: percentile lines only
    chart = wb.add_chart({'type': 'line'})
    for col_offset, name, color in [
        (len(sample_idx) + 1, '5th %ile', '#FF6B6B'),
        (len(sample_idx) + 2, '中位數', '#4ECDC4'),
        (len(sample_idx) + 3, '95th %ile', '#45B7D1'),
    ]:
        chart.add_series({
            'name': name,
            'categories': [ws.name, 1, 0, n_cols, 0],
            'values': [ws.name, 1, col_offset, n_cols, col_offset],
            'line': {'color': color, 'width': 2},
        })
    chart.set_title({'name': '蒙地卡羅資金曲線（分位線）'})
    chart.set_x_axis({'name': '交易次數'})
    chart.set_y_axis({'name': '資金 ($)'})
    chart.set_size({'width': 600, 'height': 350})
    ws.insert_chart('A' + str(n_cols + 4), chart)


def _sheet_drawdown(wb, curves, initial_capital, stats):
    ws = wb.add_worksheet('回撤分析')
    bold = wb.add_format({'bold': True})

    running_max = np.maximum.accumulate(curves, axis=1)
    drawdowns = (running_max - curves) / running_max
    max_drawdowns = np.max(drawdowns, axis=1) * 100

    # Histogram: 10 bins from 0% to 100%
    bins = np.linspace(0, 100, 11)
    counts, edges = np.histogram(max_drawdowns, bins=bins)

    ws.write(0, 0, '最大回撤區間', bold)
    ws.write(0, 1, '路徑數量', bold)
    for i, (count, edge) in enumerate(zip(counts, edges[:-1])):
        ws.write(i + 1, 0, f'{edge:.0f}%–{edges[i+1]:.0f}%')
        ws.write(i + 1, 1, int(count))

    chart = wb.add_chart({'type': 'column'})
    chart.add_series({
        'name': '路徑數量',
        'categories': [ws.name, 1, 0, len(counts), 0],
        'values': [ws.name, 1, 1, len(counts), 1],
        'fill': {'color': '#FF6B6B'},
    })
    chart.set_title({'name': '最大回撤分佈（1000 次模擬）'})
    chart.set_x_axis({'name': '最大回撤區間'})
    chart.set_y_axis({'name': '路徑數量'})
    chart.set_size({'width': 500, 'height': 300})
    ws.insert_chart('D2', chart)

    # Stats table
    stat_rows = [
        ('平均最大回撤', f"{stats['max_drawdown_mean']}%"),
        ('回撤標準差', f"{stats['max_drawdown_std']}%"),
        ('最差回撤', f"{stats['max_drawdown_worst']}%"),
        ('破產率', f"{stats['ruin_rate']}%"),
    ]
    base = len(counts) + 3
    ws.write(base, 0, '統計摘要', bold)
    for i, (label, value) in enumerate(stat_rows):
        ws.write(base + 1 + i, 0, label)
        ws.write(base + 1 + i, 1, value)

    ws.set_column(0, 0, 20)
    ws.set_column(1, 1, 12)


def _sheet_cycles(wb, params, results):
    ws = wb.add_worksheet('週期共振')
    bold = wb.add_format({'bold': True})

    cycle_data = [
        ('基欽週期（3-4年）', params['kitchin'], 0.15),
        ('朱格拉週期（7-11年）', params['juglar'], 0.25),
        ('庫茲涅茨週期（15-25年）', params['kuznets'], 0.30),
        ('康波週期（50-60年）', params['kondratiev'], 0.30),
    ]

    ws.write(0, 0, '週期', bold)
    ws.write(0, 1, '當前位置（-2~+2）', bold)
    ws.write(0, 2, '權重', bold)
    ws.write(0, 3, '加權貢獻', bold)

    for i, (name, val, weight) in enumerate(cycle_data):
        ws.write(i + 1, 0, name)
        ws.write(i + 1, 1, val)
        ws.write(i + 1, 2, f'{weight*100:.0f}%')
        ws.write(i + 1, 3, round(val * weight, 4))

    ws.write(6, 0, '共振分數（加權和）', bold)
    ws.write(6, 3, results['cycle_score'])
    ws.write(7, 0, '週期乘數', bold)
    ws.write(7, 3, results['cycle_multiplier'])

    ws.write(9, 0, '凱利%比較', bold)
    ws.write(10, 0, '原始凱利%')
    ws.write(10, 1, f"{results['raw_kelly']:.2f}%")
    ws.write(11, 0, '調整後凱利%')
    ws.write(11, 1, f"{results['adj_kelly']:.2f}%")

    ws.set_column(0, 0, 26)
    ws.set_column(1, 3, 16)
```

- [ ] **Step 2: 快速煙霧測試**

```bash
cd /Users/steven/CCProject/kelly-fibonacci
source venv/bin/activate
python3 -c "
from excel_builder import build_excel
import numpy as np
params = {'capital': 100000, 'win_rate': 55, 'odds': 2.0, 'fib_level': 61.8,
          'kitchin': 1, 'juglar': 1, 'kuznets': 0, 'kondratiev': 1}
results = {'raw_kelly': 25.0, 'cycle_score': 0.7, 'cycle_multiplier': 1.175,
           'fib_multiplier': 1.0, 'adj_kelly': 29.375,
           'full_kelly_amt': 29375, 'half_kelly_amt': 14687.5,
           'stats': {'p5': 80000, 'p50': 130000, 'p95': 250000,
                     'max_drawdown_mean': 35.2, 'max_drawdown_std': 10.1,
                     'max_drawdown_worst': 78.5, 'ruin_rate': 2.3}}
from montecarlo import run_simulation
curves = run_simulation(0.55, 0.2, 100000, n_trades=200, n_simulations=1000, seed=42)
data = build_excel(params, results, curves)
print(f'Excel bytes: {len(data)}')
"
```

Expected: Excel bytes: 数字（大於 50000）

- [ ] **Step 3: Commit**

```bash
cd /Users/steven/CCProject
git add kelly-fibonacci/excel_builder.py
git commit -m "feat: Excel builder with 4 sheets and embedded charts"
```

---

### Task 7: app.py — Flask 路由

**Files:**
- Create: `kelly-fibonacci/app.py`

- [ ] **Step 1: 實作 app.py**

建立 `kelly-fibonacci/app.py`：

```python
from flask import Flask, render_template, request, jsonify, send_file
import io
from kelly import raw_kelly, adjusted_kelly, position_sizes
from fibonacci import get_fib_params
from cycles import resonance_score
from montecarlo import run_simulation, simulation_stats
from excel_builder import build_excel

app = Flask(__name__)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/calculate', methods=['POST'])
def calculate():
    data = request.get_json()
    capital = float(data['capital'])
    win_rate = float(data['win_rate']) / 100.0
    fib_level = float(data['fib_level'])
    kitchin = float(data['kitchin'])
    juglar = float(data['juglar'])
    kuznets = float(data['kuznets'])
    kondratiev = float(data['kondratiev'])

    fib_params = get_fib_params(fib_level)
    odds = fib_params['reward_ratio']
    fib_multiplier = fib_params['entry_multiplier']

    cycle_score, cycle_multiplier = resonance_score(kitchin, juglar, kuznets, kondratiev)

    raw_k = raw_kelly(win_rate, odds)
    adj_k = adjusted_kelly(win_rate, odds, cycle_multiplier, fib_multiplier)
    sizes = position_sizes(capital, adj_k)

    curves = run_simulation(win_rate, adj_k, capital, n_trades=200, n_simulations=1000)
    stats = simulation_stats(curves, capital)

    return jsonify({
        'raw_kelly': round(raw_k * 100, 2),
        'odds': odds,
        'fib_multiplier': fib_multiplier,
        'cycle_score': cycle_score,
        'cycle_multiplier': cycle_multiplier,
        'adj_kelly': round(adj_k * 100, 2),
        'full_kelly_amt': sizes['full_kelly'],
        'half_kelly_amt': sizes['half_kelly'],
        'stats': stats,
    })

@app.route('/download', methods=['POST'])
def download():
    data = request.get_json()
    capital = float(data['capital'])
    win_rate = float(data['win_rate']) / 100.0
    fib_level = float(data['fib_level'])
    kitchin = float(data['kitchin'])
    juglar = float(data['juglar'])
    kuznets = float(data['kuznets'])
    kondratiev = float(data['kondratiev'])

    fib_params = get_fib_params(fib_level)
    odds = fib_params['reward_ratio']
    fib_multiplier = fib_params['entry_multiplier']

    cycle_score, cycle_multiplier = resonance_score(kitchin, juglar, kuznets, kondratiev)
    raw_k = raw_kelly(win_rate, odds)
    adj_k = adjusted_kelly(win_rate, odds, cycle_multiplier, fib_multiplier)
    sizes = position_sizes(capital, adj_k)

    curves = run_simulation(win_rate, adj_k, capital, n_trades=200, n_simulations=1000)
    stats = simulation_stats(curves, capital)

    params = {
        'capital': capital, 'win_rate': win_rate * 100, 'odds': odds,
        'fib_level': fib_level, 'kitchin': kitchin, 'juglar': juglar,
        'kuznets': kuznets, 'kondratiev': kondratiev,
    }
    results = {
        'raw_kelly': round(raw_k * 100, 2),
        'cycle_score': cycle_score,
        'cycle_multiplier': cycle_multiplier,
        'fib_multiplier': fib_multiplier,
        'adj_kelly': round(adj_k * 100, 2),
        'full_kelly_amt': sizes['full_kelly'],
        'half_kelly_amt': sizes['half_kelly'],
        'stats': stats,
    }

    xlsx_bytes = build_excel(params, results, curves)
    return send_file(
        io.BytesIO(xlsx_bytes),
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name='kelly_fibonacci_model.xlsx',
    )

if __name__ == '__main__':
    app.run(port=5400, debug=True)
```

- [ ] **Step 2: 煙霧測試路由**

```bash
cd /Users/steven/CCProject/kelly-fibonacci
source venv/bin/activate
python3 -c "
from app import app
client = app.test_client()
resp = client.post('/calculate', json={
    'capital': 100000, 'win_rate': 55, 'fib_level': 61.8,
    'kitchin': 1, 'juglar': 1, 'kuznets': 0, 'kondratiev': 1
})
import json
data = json.loads(resp.data)
print('adj_kelly:', data['adj_kelly'])
print('full_kelly_amt:', data['full_kelly_amt'])
assert resp.status_code == 200
print('OK')
"
```

Expected: 印出 adj_kelly 和 full_kelly_amt 數值，最後 OK

- [ ] **Step 3: Commit**

```bash
cd /Users/steven/CCProject
git add kelly-fibonacci/app.py
git commit -m "feat: Flask routes /calculate and /download"
```

---

### Task 8: index.html — 前端介面

**Files:**
- Create: `kelly-fibonacci/templates/index.html`

- [ ] **Step 1: 實作 index.html**

建立 `kelly-fibonacci/templates/index.html`：

```html
<!DOCTYPE html>
<html lang="zh-TW">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>凱利×費波那契×週期 倉位計算器</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: 'Segoe UI', sans-serif; background: #0f1117; color: #e2e8f0; min-height: 100vh; }
    header { background: linear-gradient(135deg, #1a1f35, #2d3561); padding: 24px 32px; border-bottom: 1px solid #2d3748; }
    header h1 { font-size: 1.6rem; font-weight: 700; color: #63b3ed; }
    header p { color: #a0aec0; font-size: 0.9rem; margin-top: 4px; }
    .container { max-width: 1100px; margin: 0 auto; padding: 32px 24px; }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }
    .card { background: #1a202c; border-radius: 12px; padding: 24px; border: 1px solid #2d3748; }
    .card h2 { font-size: 1rem; font-weight: 600; color: #90cdf4; margin-bottom: 16px; text-transform: uppercase; letter-spacing: 0.05em; }
    .form-group { margin-bottom: 16px; }
    label { display: block; font-size: 0.85rem; color: #a0aec0; margin-bottom: 6px; }
    input[type=number], select {
      width: 100%; padding: 10px 12px; background: #2d3748; border: 1px solid #4a5568;
      border-radius: 8px; color: #e2e8f0; font-size: 0.95rem;
    }
    input[type=range] { width: 100%; accent-color: #63b3ed; }
    .range-row { display: flex; align-items: center; gap: 12px; }
    .range-value { min-width: 32px; text-align: right; font-weight: 600; color: #63b3ed; font-size: 0.95rem; }
    .btn-calc {
      width: 100%; padding: 14px; background: linear-gradient(135deg, #3182ce, #2b6cb0);
      border: none; border-radius: 8px; color: white; font-size: 1rem; font-weight: 600;
      cursor: pointer; margin-top: 8px; transition: opacity 0.2s;
    }
    .btn-calc:hover { opacity: 0.85; }
    .result-row { display: flex; justify-content: space-between; align-items: center; padding: 10px 0; border-bottom: 1px solid #2d3748; }
    .result-row:last-child { border-bottom: none; }
    .result-label { color: #a0aec0; font-size: 0.9rem; }
    .result-value { font-weight: 700; font-size: 1rem; color: #68d391; }
    .result-value.highlight { color: #f6ad55; font-size: 1.15rem; }
    .result-value.big { color: #63b3ed; font-size: 1.2rem; }
    .btn-download {
      width: 100%; padding: 12px; background: linear-gradient(135deg, #276749, #2f855a);
      border: none; border-radius: 8px; color: white; font-size: 0.95rem; font-weight: 600;
      cursor: pointer; margin-top: 16px; transition: opacity 0.2s;
    }
    .btn-download:hover { opacity: 0.85; }
    .btn-download:disabled { opacity: 0.4; cursor: not-allowed; }
    .stats-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 8px; }
    .stat-box { background: #2d3748; border-radius: 8px; padding: 10px 14px; }
    .stat-box .label { font-size: 0.75rem; color: #718096; }
    .stat-box .val { font-size: 1rem; font-weight: 700; color: #e2e8f0; margin-top: 2px; }
    details { margin-top: 32px; background: #1a202c; border-radius: 12px; border: 1px solid #2d3748; }
    summary { padding: 18px 24px; cursor: pointer; font-weight: 600; color: #90cdf4; font-size: 1rem; user-select: none; }
    .help-content { padding: 0 24px 24px; }
    .help-section { margin-bottom: 20px; }
    .help-section h3 { color: #f6ad55; font-size: 0.9rem; font-weight: 600; margin-bottom: 10px; text-transform: uppercase; letter-spacing: 0.04em; }
    .help-item { margin-bottom: 10px; }
    .help-item strong { color: #63b3ed; }
    .help-item p { color: #a0aec0; font-size: 0.88rem; line-height: 1.6; margin-top: 2px; }
    .tag { display: inline-block; background: #2d3748; border-radius: 4px; padding: 1px 6px; font-size: 0.75rem; color: #90cdf4; margin-right: 4px; }
    #loading { display: none; text-align: center; color: #a0aec0; padding: 20px; }
  </style>
</head>
<body>
  <header>
    <h1>凱利 × 費波那契 × 四大週期　倉位計算器</h1>
    <p>整合凱利公式、費波那契回檔位與基欽/朱格拉/庫茲涅茨/康波四大經濟週期，計算最佳倉位並生成蒙地卡羅風險模型</p>
  </header>

  <div class="container">
    <div class="grid">
      <!-- Left: Input -->
      <div>
        <div class="card">
          <h2>基本參數</h2>
          <div class="form-group">
            <label>初始資金 ($)</label>
            <input type="number" id="capital" value="200000" min="1000">
          </div>
          <div class="form-group">
            <label>勝率 (%)</label>
            <div class="range-row">
              <input type="range" id="win_rate" min="1" max="99" value="55" oninput="document.getElementById('win_rate_val').textContent=this.value+'%'">
              <span class="range-value" id="win_rate_val">55%</span>
            </div>
          </div>
          <div class="form-group">
            <label>費波那契回檔位入場點</label>
            <select id="fib_level">
              <option value="23.6">23.6%（淺回檔，較保守）</option>
              <option value="38.2">38.2%（常見支撐）</option>
              <option value="50.0">50.0%（黃金中點）</option>
              <option value="61.8" selected>61.8%（黃金比例，強支撐）</option>
              <option value="78.6">78.6%（深度回檔，高報酬）</option>
            </select>
          </div>
        </div>

        <div class="card" style="margin-top:16px">
          <h2>四大經濟週期位置</h2>
          <p style="font-size:0.8rem;color:#718096;margin-bottom:16px">-2 = 深度收縮　0 = 中性　+2 = 強勁擴張</p>

          <div class="form-group">
            <label>基欽週期（3-4年　庫存循環）</label>
            <div class="range-row">
              <input type="range" id="kitchin" min="-2" max="2" step="0.5" value="0" oninput="document.getElementById('kitchin_val').textContent=this.value">
              <span class="range-value" id="kitchin_val">0</span>
            </div>
          </div>
          <div class="form-group">
            <label>朱格拉週期（7-11年　固定資產投資）</label>
            <div class="range-row">
              <input type="range" id="juglar" min="-2" max="2" step="0.5" value="0" oninput="document.getElementById('juglar_val').textContent=this.value">
              <span class="range-value" id="juglar_val">0</span>
            </div>
          </div>
          <div class="form-group">
            <label>庫茲涅茨週期（15-25年　房地產人口）</label>
            <div class="range-row">
              <input type="range" id="kuznets" min="-2" max="2" step="0.5" value="0" oninput="document.getElementById('kuznets_val').textContent=this.value">
              <span class="range-value" id="kuznets_val">0</span>
            </div>
          </div>
          <div class="form-group">
            <label>康波週期（50-60年　技術革命）</label>
            <div class="range-row">
              <input type="range" id="kondratiev" min="-2" max="2" step="0.5" value="0" oninput="document.getElementById('kondratiev_val').textContent=this.value">
              <span class="range-value" id="kondratiev_val">0</span>
            </div>
          </div>

          <button class="btn-calc" onclick="calculate()">計算最佳倉位</button>
        </div>
      </div>

      <!-- Right: Results -->
      <div>
        <div class="card" id="results-card">
          <h2>計算結果</h2>
          <div id="loading">計算中...</div>
          <div id="results-body">
            <p style="color:#718096;font-size:0.9rem">填入左側參數後點擊「計算最佳倉位」</p>
          </div>
        </div>
      </div>
    </div>

    <!-- Help Section -->
    <details>
      <summary>📖 使用說明與參數解釋</summary>
      <div class="help-content">
        <div class="help-section">
          <h3>基本參數</h3>
          <div class="help-item">
            <strong>初始資金</strong>
            <p>這筆交易投入的總資本。凱利公式會計算應動用其中幾%。</p>
          </div>
          <div class="help-item">
            <strong>勝率</strong>
            <p>根據歷史回測或主觀判斷，這個策略預期獲利的機率。55% 代表十次交易約五到六次賺錢。</p>
          </div>
          <div class="help-item">
            <strong>費波那契回檔位入場點</strong>
            <p>你計劃在哪個回檔位置入場。越深的回檔位（61.8%、78.6%）代表更強的歷史支撐，系統自動帶入較高的入場乘數與更佳的風險報酬比。<br>
            <span class="tag">23.6%</span>保守；<span class="tag">38.2%</span>常見；<span class="tag">61.8%</span>黃金比例；<span class="tag">78.6%</span>激進</p>
          </div>
        </div>

        <div class="help-section">
          <h3>四大經濟週期（-2 到 +2）</h3>
          <div class="help-item">
            <strong>基欽週期（3-4 年）</strong>
            <p>庫存循環。+2 代表企業正積極補庫存（擴張）；-2 代表去庫存（收縮）。通常看企業庫存月數、PMI 庫存分項。</p>
          </div>
          <div class="help-item">
            <strong>朱格拉週期（7-11 年）</strong>
            <p>固定資產投資循環（廠房設備）。+2 代表資本支出高峰期；-2 代表投資低谷。可參考工業設備訂單、企業資本支出數據。</p>
          </div>
          <div class="help-item">
            <strong>庫茲涅茨週期（15-25 年）</strong>
            <p>房地產與人口結構週期。+2 代表建設高峰與人口紅利；-2 代表房市低迷與人口老化壓力。</p>
          </div>
          <div class="help-item">
            <strong>康波週期（50-60 年）</strong>
            <p>技術革命浪潮（蒸汽機→電力→IT→AI）。+2 代表新技術大規模擴散與生產力爆發；-2 代表舊技術衰退與創新低谷。目前 AI 革命可能處於早期擴散階段（+1 至 +2）。</p>
          </div>
        </div>

        <div class="help-section">
          <h3>輸出結果解讀</h3>
          <div class="help-item">
            <strong>原始凱利%</strong>
            <p>純以勝率與費波那契帶入的賠率計算的數學最佳倉位比例，不含週期調整。</p>
          </div>
          <div class="help-item">
            <strong>週期共振乘數</strong>
            <p>四個週期加權後的市場環境係數。0.5 代表全面收縮應大幅縮倉；1.5 代表全面擴張可適度加倉；1.0 為中性。</p>
          </div>
          <div class="help-item">
            <strong>調整後凱利%</strong>
            <p>= 原始凱利% × 週期乘數 × 費波那契入場乘數。這是最終建議的倉位占資本比例。</p>
          </div>
          <div class="help-item">
            <strong>全凱利 vs 半凱利</strong>
            <p><strong>全凱利</strong>：數學最佳化，長期期望值最大，但短期波動劇烈。<br>
            <strong>半凱利</strong>：實務上多數專業交易者採用，犧牲約 25% 期望值但波動降低約 50%，心理上更容易執行。</p>
          </div>
          <div class="help-item">
            <strong>蒙地卡羅統計（1000 次模擬）</strong>
            <p>模擬 1000 種不同的隨機交易路徑（各 200 筆）。<br>
            <span class="tag">中位數結果</span>50% 的情境最終資金高於此值。<br>
            <span class="tag">破產率</span>資金跌破初始 50% 的路徑比例，越低越安全。</p>
          </div>
        </div>
      </div>
    </details>
  </div>

  <script>
    let lastParams = null;

    async function calculate() {
      const params = {
        capital: document.getElementById('capital').value,
        win_rate: document.getElementById('win_rate').value,
        fib_level: document.getElementById('fib_level').value,
        kitchin: document.getElementById('kitchin').value,
        juglar: document.getElementById('juglar').value,
        kuznets: document.getElementById('kuznets').value,
        kondratiev: document.getElementById('kondratiev').value,
      };
      lastParams = params;

      document.getElementById('loading').style.display = 'block';
      document.getElementById('results-body').innerHTML = '';

      const resp = await fetch('/calculate', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(params),
      });
      const d = await resp.json();
      document.getElementById('loading').style.display = 'none';

      const fmt = n => '$' + Number(n).toLocaleString('en-US', {minimumFractionDigits: 0});

      document.getElementById('results-body').innerHTML = `
        <div class="result-row">
          <span class="result-label">原始凱利%</span>
          <span class="result-value">${d.raw_kelly}%</span>
        </div>
        <div class="result-row">
          <span class="result-label">費波那契入場乘數 <small style="color:#718096">（回檔位 ${params.fib_level}%，賠率 ${d.odds}:1）</small></span>
          <span class="result-value">${d.fib_multiplier}×</span>
        </div>
        <div class="result-row">
          <span class="result-label">週期共振分數 → 乘數</span>
          <span class="result-value">${d.cycle_score} → ${d.cycle_multiplier}×</span>
        </div>
        <div class="result-row">
          <span class="result-label">調整後凱利%</span>
          <span class="result-value highlight">${d.adj_kelly}%</span>
        </div>
        <div class="result-row">
          <span class="result-label">建議全凱利倉位</span>
          <span class="result-value big">${fmt(d.full_kelly_amt)}</span>
        </div>
        <div class="result-row">
          <span class="result-label">建議半凱利倉位（推薦）</span>
          <span class="result-value big">${fmt(d.half_kelly_amt)}</span>
        </div>

        <div style="margin-top:16px">
          <p style="font-size:0.8rem;color:#718096;margin-bottom:10px">蒙地卡羅模擬（1000次，各 200 筆交易）</p>
          <div class="stats-grid">
            <div class="stat-box"><div class="label">最終資金中位數</div><div class="val">${fmt(d.stats.p50)}</div></div>
            <div class="stat-box"><div class="label">最終資金 5th %ile</div><div class="val">${fmt(d.stats.p5)}</div></div>
            <div class="stat-box"><div class="label">最終資金 95th %ile</div><div class="val">${fmt(d.stats.p95)}</div></div>
            <div class="stat-box"><div class="label">破產率（跌破50%）</div><div class="val" style="color:${d.stats.ruin_rate > 10 ? '#fc8181' : '#68d391'}">${d.stats.ruin_rate}%</div></div>
            <div class="stat-box"><div class="label">平均最大回撤</div><div class="val">${d.stats.max_drawdown_mean}%</div></div>
            <div class="stat-box"><div class="label">最差回撤</div><div class="val" style="color:#fc8181">${d.stats.max_drawdown_worst}%</div></div>
          </div>
        </div>

        <button class="btn-download" onclick="downloadExcel()">⬇ 下載 Excel 完整模型（含蒙地卡羅圖表）</button>
      `;
    }

    async function downloadExcel() {
      if (!lastParams) return;
      const btn = document.querySelector('.btn-download');
      btn.disabled = true;
      btn.textContent = '產出中...';
      const resp = await fetch('/download', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(lastParams),
      });
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'kelly_fibonacci_model.xlsx';
      a.click();
      URL.revokeObjectURL(url);
      btn.disabled = false;
      btn.textContent = '⬇ 下載 Excel 完整模型（含蒙地卡羅圖表）';
    }
  </script>
</body>
</html>
```

- [ ] **Step 2: Commit**

```bash
cd /Users/steven/CCProject
git add kelly-fibonacci/templates/index.html
git commit -m "feat: single-page UI with results display and help section"
```

---

### Task 9: 端對端驗證與啟動

**Files:**
- 無新增

- [ ] **Step 1: 執行所有單元測試**

```bash
cd /Users/steven/CCProject/kelly-fibonacci
source venv/bin/activate && python -m pytest tests/ -v
```

Expected: 全部通過（18+ passed）

- [ ] **Step 2: 啟動服務**

```bash
cd /Users/steven/CCProject/kelly-fibonacci
source venv/bin/activate && python app.py
```

Expected: `* Running on http://127.0.0.1:5400`

- [ ] **Step 3: curl 健康測試**

新開 terminal：

```bash
curl -s -X POST http://localhost:5400/calculate \
  -H "Content-Type: application/json" \
  -d '{"capital":200000,"win_rate":55,"fib_level":61.8,"kitchin":1,"juglar":1,"kuznets":0,"kondratiev":1}' \
  | python3 -m json.tool
```

Expected: JSON 含 `adj_kelly`、`full_kelly_amt`、`stats` 欄位

- [ ] **Step 4: 測試下載端點**

```bash
curl -s -X POST http://localhost:5400/download \
  -H "Content-Type: application/json" \
  -d '{"capital":200000,"win_rate":55,"fib_level":61.8,"kitchin":1,"juglar":1,"kuznets":0,"kondratiev":1}' \
  -o /tmp/test_model.xlsx
ls -lh /tmp/test_model.xlsx
```

Expected: 檔案大於 50KB

- [ ] **Step 5: Final commit**

```bash
cd /Users/steven/CCProject
git add kelly-fibonacci/
git commit -m "feat: kelly-fibonacci calculator complete - Flask + Monte Carlo + Excel"
```
