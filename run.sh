#!/bin/bash
# ============================================
# OFAC 制裁名单监控 - 运行脚本
# 用于手动执行或 cron 定时任务调用
# ============================================

set -e

# 切换到脚本所在目录
cd "$(dirname "$0")"

# 项目根目录
PROJECT_DIR="$(pwd)"

# Python 路径（优先使用 python3）
PYTHON="${PYTHON:-python3}"

# 日志文件
LOG_FILE="$PROJECT_DIR/logs/cron_$(date +%Y%m%d).log"

echo "============================================" | tee -a "$LOG_FILE"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] OFAC Monitor - Starting" | tee -a "$LOG_FILE"
echo "============================================" | tee -a "$LOG_FILE"

# 执行监控
cd "$PROJECT_DIR"
$PYTHON -m src.main scrape 2>&1 | tee -a "$LOG_FILE"

EXIT_CODE=${PIPESTATUS[0]}

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Exit code: $EXIT_CODE" | tee -a "$LOG_FILE"

exit $EXIT_CODE
