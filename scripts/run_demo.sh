#!/usr/bin/env bash
# 一键离线演示：无需任何密钥即可跑通完整流水线
set -euo pipefail
cd "$(dirname "$0")/.."
TOPIC="${1:-人形机器人行业研究}"
python3 main.py "$TOPIC" --mode offline --provider mock
