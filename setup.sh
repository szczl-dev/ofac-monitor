#!/bin/bash
# ============================================
# OFAC 制裁名单监控系统 - 一键安装脚本
# ============================================

set -e

echo "============================================"
echo "  OFAC 制裁名单监控系统 - 安装向导"
echo "============================================"
echo ""

cd "$(dirname "$0")"
PROJECT_DIR="$(pwd)"

# 检查 Python
echo "[1/4] 检查 Python 环境..."
PYTHON=""
for py in python3 python; do
    if command -v $py &> /dev/null; then
        PYTHON=$py
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo "❌ 未找到 Python，请先安装 Python 3.8+"
    echo "   macOS: brew install python3"
    echo "   或访问: https://www.python.org/downloads/"
    exit 1
fi

PY_VERSION=$($PYTHON --version 2>&1)
echo "   ✅ 找到 $PY_VERSION"

# 安装依赖
echo ""
echo "[2/4] 安装 Python 依赖..."
$PYTHON -m pip install -r requirements.txt --quiet
echo "   ✅ 依赖安装完成"

# 创建目录
echo ""
echo "[3/4] 创建数据目录..."
mkdir -p data/current data/archive logs
echo "   ✅ 目录已创建"

# 检查 .env 配置
echo ""
echo "[4/4] 检查配置..."
if [ ! -f .env ]; then
    cp .env.example .env
    echo "   ⚠️  已创建 .env 文件，请编辑填入飞书 Webhook URL"
    echo "       编辑命令: nano .env"
else
    echo "   ✅ .env 配置文件已存在"
fi

echo ""
echo "============================================"
echo "  ✅ 安装完成！"
echo "============================================"
echo ""
echo "  下一步操作:"
echo ""
echo "  1. 确保 .env 中配置了正确的飞书 Webhook URL"
echo "     nano .env"
echo ""
echo "  2. 发送测试消息验证配置:"
echo "     $PYTHON -m src.main test"
echo ""
echo "  3. 运行一次监控检查:"
echo "     $PYTHON -m src.main scrape"
echo ""
echo "  4. 设置每日定时任务:"
echo "     crontab -e"
echo "     添加: 0 9 * * * $PROJECT_DIR/run.sh"
echo "     (每天早上9点自动执行)"
echo ""
