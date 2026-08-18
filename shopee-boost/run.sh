#!/bin/bash
cd "$(dirname "$0")"
/Users/steven/CCProject/telebot/venv/bin/python3 boost.py >> logs/boost.log 2>&1
