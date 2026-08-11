# dd_bot 上手指南

钉钉群机器人 daemon，自动监听群消息并调用 QMind RAG 回答提问。

## 前置条件

1. macOS (Apple Silicon) + Python 3.9+
2. 安装 [QoderWork](https://qoder.com)（用于 DWS 钉钉消息能力）
3. 在 QoderWork 中登录你的钉钉账号

## 快速开始

```bash
# 1. 克隆仓库
git clone <repo-url> dd_bot
cd dd_bot

# 2. 运行 setup 脚本（配置 watchdog 自启动）
chmod +x setup.sh
./setup.sh

# 3. 手动启动 daemon 测试
python3 dd_bot_daemon.py
```

## 配置

群配置在 `dd_bot_watchdog.py` 中的 `GROUPS` 列表里。每个群需要以下字段：

```python
{
    'tag': 'renzouchaliang',                    # 群标签（日志用）
    'group_id': 'cidWuXsSf6NK/IyES4I61CAGw==', # 钉钉群 ID
    'bot_name': '大荔枝',                        # 群内机器人名称（可选）
    'webhook_url': 'https://...',               # Webhook 地址（可选，用于以机器人身份发消息）
    'webhook_secret': 'SEC...',                 # Webhook 密钥（可选）
}
```

获取群 ID 的方法：在钉钉群设置 -> 群管理 -> 群信息中查看。

## 日常操作

```bash
# 查看 daemon 日志
tail -f .dd_bot_stdout.log

# 查看 watchdog 日志
tail -f .dd_bot_watchdog.log

# 手动重启 daemon
kill $(cat .dd_bot.pid)
python3 dd_bot_daemon.py > .dd_bot_stdout.log 2>&1 &

# 停止 daemon
kill $(cat .dd_bot.pid)
```

## 工作原理

1. Watchdog 守护进程每 60s 检查一次 daemon 是否存活，挂了自动拉起
2. Daemon 每 15s 轮询每个群的钉钉消息
3. 匹配到 @机器人 + 关键词/问号 的消息，调用 QMind RAG 生成回答
4. 通过 Webhook 或用户身份将回答发回群里

## 触发条件

消息需要同时满足：
- 包含 @机器人名称
- 包含关键词（如"怎么"、"如何"、"为什么"等）或以问号结尾

## 常见问题

**Q: 启动报错 "DWS binary not found"**
A: 确保已安装 QoderWork 并在其中登录了钉钉。DWS 二进制位于 `~/.qoderwork/bin/ext/dws-core-darwin-arm64`。

**Q: 消息拉取失败 / DNS 错误**
A: 检查网络连接，DWS 需要访问 `mcp-gw.dingtalk.com`。

**Q: 两个 daemon 同时回复**
A: 检查是否有残留进程 `ps aux | grep dd_bot_daemon`，杀掉多余的再重启。
