#!/usr/bin/env python3
"""大荔枝 watchdog - 检测守护进程是否存活，不存活则重启（单进程多群模式）"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DAEMON = os.path.join(SCRIPT_DIR, 'dd_bot_daemon.py')
LOG_FILE = os.path.join(SCRIPT_DIR, '.dd_bot_watchdog.log')
PID_FILE = os.path.join(SCRIPT_DIR, '.dd_bot.pid')
STDOUT_LOG = os.path.join(SCRIPT_DIR, '.dd_bot_stdout.log')

# ===== 群配置列表（新增群在这里加） =====
GROUPS = [
    {'tag': 'renzouchaliang', 'group_id': 'cidWuXsSf6NK/IyES4I61CAGw==', 'bot_name': '大荔枝'},
    {'tag': 'chuangxin',     'group_id': 'cidLiCKX7xtseTamYRHphUBRA=='},
    {
        'tag': 'yiqinuli',
        'group_id': 'cidPa+hSdOllKJlF33qVol49w==',
        'bot_name': '大荔枝',
        'webhook_url': 'https://oapi.dingtalk.com/robot/send?access_token=a4ff77b10d8fd30bd135049a7fc6ef2f99378189a6d381a9492a60194d491f9f',
        'webhook_secret': 'SEC096b94d62108b2646f7c3808176fcca6385c57075577a3d94cc418b6bebff280',
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


def main():
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
