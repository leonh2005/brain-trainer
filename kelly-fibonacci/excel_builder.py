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
