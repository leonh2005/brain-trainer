#!/bin/bash
# 用法: ./forecast.sh [股票代號] [天數]
# 範例: ./forecast.sh 2330 10
DIR="$(cd "$(dirname "$0")" && pwd)"
"$DIR/.venv/bin/python" "$DIR/stock_forecast.py" "$@"
