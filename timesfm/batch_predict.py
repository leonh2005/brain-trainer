"""批次預測隔日收盤價，給 telebot/screener.py 以 subprocess 呼叫。
stdin : {"<代號>": [歷史收盤價由舊到新, ...], ...}
stdout: {"<代號>": 預測收盤價, ...}
"""
import contextlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

MIN_HISTORY = 6


def main():
    series = json.load(sys.stdin)
    codes = [c for c, closes in series.items() if len(closes) >= MIN_HISTORY]
    if not codes:
        print("{}")
        return

    from stock_forecast import load_model

    import timesfm

    with contextlib.redirect_stdout(sys.stderr):
        model = load_model()
        model.compile(timesfm.ForecastConfig(
            max_context=512,
            max_horizon=1,
            normalize_inputs=True,
            use_continuous_quantile_head=True,
            force_flip_invariance=True,
            infer_is_positive=True,
            fix_quantile_crossing=True,
        ))
        point, _ = model.forecast(horizon=1, inputs=[series[c] for c in codes])

    print(json.dumps({c: float(point[i][0]) for i, c in enumerate(codes)}))


if __name__ == "__main__":
    main()
