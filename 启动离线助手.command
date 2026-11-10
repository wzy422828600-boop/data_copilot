#!/bin/zsh
set -e
project_dir="${0:A:h}"
cd -- "$project_dir"
if [[ ! -x .venv/bin/python ]]; then
  print "未找到项目的 Python 环境，请打开 README 查看安装步骤。"
  read "?按回车退出"
  exit 1
fi
# Blank the key for this child process only; do not edit .env.
export ANTHROPIC_API_KEY=''
export PYTHONDONTWRITEBYTECODE=1
exec .venv/bin/python scripts/launch_offline.py
