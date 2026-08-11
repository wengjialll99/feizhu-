#!/usr/bin/env python3
"""大荔枝 watchdog - 检测守护进程是否存活，不存活则重启（单进程多群模式）
附带自动 git pull：每 60 分钟拉取远程最新代码，有变更则重启 daemon。"""

import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DAEMON = os.path.join(SCRIPT_DIR, 'dd_bot_daemon.py')
LOG_FILE = os.path.join(SCRIPT_DIR, '.dd_bot_watchdog.log')
PID_FILE = os.path.join(SCRIPT_DIR, '.dd_bot.pid')
STDOUT_LOG = os.path.join(SCRIPT_DIR, '.dd_bot_stdout.log')
PULL_TS_FILE = os.path.join(SCRIPT_DIR, '.dd_bot_last_pull')
PULL_INTERVAL = 3600  # 每 60 分钟自动拉取一次

# ===== 群配置列表（新增群在这里加） =====
GROUPS = [
    {'tag': 'renzouchaliang', 'group_id': 'cidWuXsSf6NK/IyES4I61CAGw==', 'bot_name': '大荔枝'},
    {'tag': 'chuangxin',     'group_id': 'cidLiCKX7xtseTamYRHphUBRA==', 'bot_name': '大荔枝'},
    {
        'tag': 'yiqinuli',
        'group_id': 'cidPa+hSdOllKJlF33qVol49w==',
        'bot_name': '大荔枝',
        'webhook_url': 'https://oapi.dingtalk.com/robot/send?access_token=a4ff77b10d8fd30bd135049a7fc6ef2f99378189a6d381a9492a60194d491f9f',
        'webhook_secret': 'SEC096b94d62108b2646f7c3808176fcca6385c57075577a3d94cc418b6bebff280',
    },
    {
        'tag': 'haidilao',
        'group_id': 'cidWuXsSf6NK/IyES4I61CAGw==',
        'bot_name': '与时',
        'profile': 'haidilao',
        'notebook_id': '019fefa1-d4ab-75af-8950-7c69d310682a',
        'webhook_url': 'https://oapi.dingtalk.com/robot/send?access_token=754d6d615403a77721eac8b91240dda159827ea583bfaebdf604695bd3023c8e',
        'webhook_secret': 'SECb82fe6efbeb85725a945e71dc302d767883cc5056e0fb18d3f2d44e2479c4983',
    },
]


def log(msg):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(LOG_FILE, 'a') as f:
        f.write(f'[{ts}] {msg}\n')


def is_alive():
    if not os.path.isfile(PID_FILE):
        return False
    try:
        with open(PID_FILE) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        return True
    except (ValueError, ProcessLookupError, PermissionError, OSError):
        return False


def _kill_daemon():
    """优雅终止 daemon 进程"""
    if not os.path.isfile(PID_FILE):
        return False
    try:
        with open(PID_FILE) as f:
            pid = int(f.read().strip())
        os.kill(pid, signal.SIGTERM)
        # 等几秒确认退出
        for _ in range(10):
            time.sleep(0.5)
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
        # 清理 pid 文件
        for f in [PID_FILE, PID_FILE + '.lock']:
            if os.path.isfile(f):
                os.remove(f)
        return True
    except Exception:
        return False


def try_git_pull():
    """定期从远程仓库拉取最新代码。如果有更新，杀掉 daemon 让下一轮重启。"""
    # 检查距离上次 pull 是否已过 PULL_INTERVAL 秒
    last_pull = 0.0
    if os.path.isfile(PULL_TS_FILE):
        try:
            last_pull = float(open(PULL_TS_FILE).read().strip())
        except (ValueError, OSError):
            pass

    if time.time() - last_pull < PULL_INTERVAL:
        return  # 还没到时间

    # 记录本次 pull 时间（无论成功与否，避免反复重试）
    with open(PULL_TS_FILE, 'w') as f:
        f.write(str(time.time()))

    # 检查是否有 remote 配置
    try:
        r = subprocess.run(
            ['git', 'remote'], capture_output=True, text=True,
            cwd=SCRIPT_DIR, timeout=5,
        )
        if not r.stdout.strip():
            return  # 没有 remote，跳过
    except Exception:
        return

    # 执行 git pull
    try:
        r = subprocess.run(
            ['git', 'pull', '--ff-only'],
            capture_output=True, text=True,
            cwd=SCRIPT_DIR, timeout=30,
        )
        output = (r.stdout or '') + (r.stderr or '')

        if 'Already up to date' in output or 'Already up-to-date' in output:
            log('git pull: 已是最新')
            return

        if r.returncode != 0:
            log(f'git pull 失败: {output.strip()}')
            return

        # 有更新！
        log(f'git pull 成功，检测到新代码，准备重启 daemon')

        if is_alive():
            if _kill_daemon():
                log('已停止旧 daemon，下一轮 watchdog 将用新代码重启')
            else:
                log('停止旧 daemon 失败，可能需手动重启')
        else:
            log('daemon 未运行，下一轮 watchdog 将用新代码启动')

    except subprocess.TimeoutExpired:
        log('git pull 超时')
    except Exception as e:
        log(f'git pull 异常: {e}')


def main():
    # 先尝试自动拉取远程代码
    try_git_pull()

    if is_alive():
        return

    log('进程未运行，正在重启（多群模式）...')

    env = os.environ.copy()
    env['DD_GROUPS'] = json.dumps(GROUPS, ensure_ascii=False)

    with open(STDOUT_LOG, 'a') as log_f:
        proc = subprocess.Popen(
            [sys.executable, DAEMON],
            env=env,
            stdout=log_f,
            stderr=subprocess.STDOUT,
            cwd=SCRIPT_DIR,
            start_new_session=True,
        )

    time.sleep(2)

    if proc.poll() is None:
        log(f'重启成功 (PID={proc.pid}), 监听 {len(GROUPS)} 个群')
    else:
        log(f'重启失败，请检查日志: {STDOUT_LOG}')


if __name__ == '__main__':
    main()
