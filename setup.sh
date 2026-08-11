#!/bin/bash
# dd_bot 一键部署脚本
# 在同事的 Mac 上运行此脚本来完成初始设置

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DAEMON="$SCRIPT_DIR/dd_bot_daemon.py"
WATCHDOG="$SCRIPT_DIR/dd_bot_watchdog.py"
DWS_BIN="$HOME/.qoderwork/bin/ext/dws-core-darwin-arm64"

echo "===== dd_bot 部署脚本 ====="
echo ""

# 1. 检查 Python
if ! command -v python3 &>/dev/null; then
    echo "[错误] 未找到 python3，请先安装 Python 3.9+"
    exit 1
fi
echo "[OK] Python: $(python3 --version)"

# 2. 检查 DWS 二进制
if [ ! -f "$DWS_BIN" ]; then
    echo "[错误] 未找到 DWS 二进制: $DWS_BIN"
    echo "       请先安装 QoderWork (https://qoder.com) 并在其中登录钉钉"
    exit 1
fi
echo "[OK] DWS: $DWS_BIN"

# 3. 检查 qmind 二进制
if [ ! -f "$SCRIPT_DIR/bin/qmind" ]; then
    echo "[错误] 未找到 qmind 二进制: $SCRIPT_DIR/bin/qmind"
    exit 1
fi
chmod +x "$SCRIPT_DIR/bin/qmind"
echo "[OK] QMind: $SCRIPT_DIR/bin/qmind"

# 4. 测试 DWS 连通性
echo ""
echo "正在测试钉钉连接..."
RESULT=$("$DWS_BIN" chat message list --group test --limit 1 --format json 2>&1 || true)
if echo "$RESULT" | grep -q '"success"'; then
    echo "[OK] 钉钉连接正常"
elif echo "$RESULT" | grep -q '"errorCode"'; then
    echo "[警告] 钉钉 API 返回错误，可能需要先在 QoderWork 中登录钉钉"
    echo "       打开 QoderWork → 连接钉钉 → 登录"
else
    echo "[警告] 无法测试钉钉连接（可能需要在 QoderWork 中登录）"
fi

# 5. 设置 watchdog 定时任务（每 1 分钟检查一次）
echo ""
CRON_CMD="* * * * * cd $SCRIPT_DIR && python3 $WATCHDOG >> $SCRIPT_DIR/.dd_bot_watchdog.log 2>&1"
EXISTING_CRON=$(crontab -l 2>/dev/null || true)

if echo "$EXISTING_CRON" | grep -q "dd_bot_watchdog"; then
    echo "[跳过] watchdog cron 已存在"
else
    (echo "$EXISTING_CRON"; echo "$CRON_CMD") | crontab -
    echo "[OK] 已添加 watchdog cron 任务（每分钟检查一次）"
fi

# 6. 启动 daemon
echo ""
if [ -f "$SCRIPT_DIR/.dd_bot.pid" ]; then
    OLD_PID=$(cat "$SCRIPT_DIR/.dd_bot.pid")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "[跳过] daemon 已在运行 (PID=$OLD_PID)"
    else
        rm -f "$SCRIPT_DIR/.dd_bot.pid" "$SCRIPT_DIR/.dd_bot.pid.lock"
        echo "正在启动 daemon..."
        cd "$SCRIPT_DIR" && python3 "$DAEMON" > "$SCRIPT_DIR/.dd_bot_stdout.log" 2>&1 &
        echo "[OK] daemon 已启动 (PID=$!)"
    fi
else
    echo "正在启动 daemon..."
    cd "$SCRIPT_DIR" && python3 "$DAEMON" > "$SCRIPT_DIR/.dd_bot_stdout.log" 2>&1 &
    echo "[OK] daemon 已启动 (PID=$!)"
fi

echo ""
echo "===== 部署完成 ====="
echo "日志: tail -f $SCRIPT_DIR/.dd_bot_stdout.log"
echo "停止: kill \$(cat $SCRIPT_DIR/.dd_bot.pid)"
