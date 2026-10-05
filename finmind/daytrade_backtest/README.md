# daytrade_backtest — 當沖候選策略回測

2026-10 為調查「當沖為何持續虧損」而寫，結論見記憶 `lesson_daytrade_chase_high_no_edge`。

## 資料來源

- **候選名單**：`logs/daytrade.log`（2026-03-30 起，daytrade_alert.py 每日推播訊息含漲幅/振幅/量）
  → 是**無前視偏誤**的真實候選，優於用收盤漲幅榜（scanner）重建
- **個股/大盤分K**：shioaji-gateway(5455) `/intraday?code=&date=`，節流 0.3s（gateway 密集請求會 deadlock，勿中途殺）

## 執行順序

```bash
cd ~/CCProject/finmind/daytrade_backtest
python3 dt_parse.py    # 解析 log → cache/dt_features.json
python3 dt_long.py     # 抓分K → cache/dt_long_cache.json，做出場時間回測
python3 dt_cond2.py    # 選股條件回測（振幅/漲幅/位階/股價/RVOL/大盤/放空）
python3 dt_final.py    # 最佳組合按月穩定度 + 停損 + 總表
```

## 已知限制

- `cache/` 是執行期產物，已 gitignore（**CCProject 是公開 repo，勿 commit 股票資料**）。首次執行會重建 cache，需 gateway 在線、約數分鐘。
- 大盤分K（IX0001）只回溯到 2026-06 左右，因此「大盤同向濾網」只能驗證 6~10 月。
- RVOL 無法評估：log 的「量」與「近5均量」單位不一致（差 1000 倍）。
