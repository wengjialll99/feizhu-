#!/bin/bash
# 大荔枝 watchdog - 检测所有群的守护进程是否存活，不存活则重启
# 由 QoderWork 定时任务每 5 分钟调用一次

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DAEMON="$SCRIPT_DIR/dd_bot_daemon.py"
LOG_FILE="$SCRIPT_DIR/.dd_bot_watchdog.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"
}

# ===== 群配置列表（新增群在这里加） =====
# 格式: TAG|GROUP_ID
GROUPS=(
    "renzouchaliang|cidWuXsSf6NK/IyES4I61CAGw=="
    "chuangxin|cidLiCKX7xtseTamYRHphUBRA=="
)

# 检查进程是否存活
is_alive() {
    local pid_file="$1"
    if [ -f "$pid_file" ]; then
        local pid
        pid=$(cat "$pid_file" 2>/dev/null)
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            return 0
        fi
    fi
    return 1
}

for entry in "${GROUPS[@]}"; do
    IFS='|' read -r tag group_id <<< "$entry"
    PID_FILE="$SCRIPT_DIR/.dd_bot_${tag}.pid"

    if is_alive "$PID_FILE"; then
        continue
    fi

    # 进程不存在，启动
    log "[$tag] 进程未运行，正在重启..."
    cd "$SCRIPT_DIR"
    nohup env DD_GROUP_ID="$group_id" DD_GROUP_TAG="$tag" /usr/bin/python3 "$DAEMON" \
        >> "$SCRIPT_DIR/.dd_bot_${tag}_stdout.log" 2>&1 &
    NEW_PID=$!
    sleep 2

    if kill -0 "$NEW_PID" 2>/dev/null; then
        log "[$tag] 重启成功 (PID=$NEW_PID)"
    else
        log "[$tag] 重启失败，请检查日志: $SCRIPT_DIR/.dd_bot_${tag}_stdout.log"
    fi
done
