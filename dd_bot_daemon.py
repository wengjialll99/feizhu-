#!/usr/bin/env python3
"""
大荔枝 - 钉钉群机器人轮询脚本
================================
自动监听钉钉群消息，对提问调用 QMind RAG 生成回答，再发回群里。

用法:
  python3 dd_bot_daemon.py                        # 默认配置运行
  DD_GROUP_ID=cidXXX python3 dd_bot_daemon.py     # 指定群
  DD_INTERVAL=10 python3 dd_bot_daemon.py          # 自定义轮询间隔

可选: 配置 Webhook 机器人发送（需先在群设置中添加自定义机器人）
  DD_WEBHOOK_URL=https://oapi.dingtalk.com/robot/send?access_token=XXX
  DD_WEBHOOK_SECRET=SECxxx  (加签模式)
"""

import json
import subprocess
import time
import os
import sys
import hmac
import hashlib
import base64
import re
import urllib.parse
import urllib.request
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

# ==================== 配置 ====================

def _load_groups():
    """加载群配置列表。优先从 DD_GROUPS (JSON) 读取，fallback 到单群 DD_GROUP_ID。"""
    raw = os.environ.get('DD_GROUPS', '')
    if raw:
        try:
            groups = json.loads(raw)
            if isinstance(groups, list) and groups:
                return groups
        except json.JSONDecodeError:
            pass
    # fallback: 单群模式
    return [{
        'tag': os.environ.get('DD_GROUP_TAG', 'default'),
        'group_id': os.environ.get('DD_GROUP_ID', 'cidWuXsSf6NK/IyES4I61CAGw=='),
        'bot_name': os.environ.get('DD_BOT_NAME', ''),
        'webhook_url': os.environ.get('DD_WEBHOOK_URL', ''),
        'webhook_secret': os.environ.get('DD_WEBHOOK_SECRET', ''),
    }]

GROUPS = _load_groups()

# 兼容：保留 GROUP_ID 供日志和其他地方引用（取第一个群）
GROUP_ID = GROUPS[0]['group_id'] if GROUPS else ''

# 自己的 openDingTalkId（黎之），用于过滤自己发的消息
MY_ID = os.environ.get('DD_MY_ID', 'D8xogtFABiSSTrd6EuHJzT9wLSbrcuEtTW')


# 兜底联系人：搜不到答案时 @这些人（逗号分隔，如 "黎之,燕麦"）
FALLBACK_CONTACTS = os.environ.get('DD_FALLBACK_CONTACTS', '黎之').split(',')

# 轮询间隔（秒）
INTERVAL = int(os.environ.get('DD_INTERVAL', '15'))

# DWS 二进制路径
DWS_BIN = os.path.expanduser('~/.qoderwork/bin/ext/dws-core-darwin-arm64')

# QMind 配置
_APP_DIR = os.path.dirname(os.path.abspath(__file__))
_LOCAL_QMIND = os.path.join(_APP_DIR, 'bin', 'qmind')
_HOME_QMIND = os.path.expanduser('~/.qoderwork/skills/qmind-knowledge/bin/qmind')
QMIND_BIN = _LOCAL_QMIND if os.path.isfile(_LOCAL_QMIND) else _HOME_QMIND
NOTEBOOK_ID = '019f6f3e-3c97-7aea-af67-2a8ad6332343'

# 状态持久化（多群共享一个状态文件）
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(_SCRIPT_DIR, '.dd_bot_state.json')
PID_FILE = os.path.join(_SCRIPT_DIR, '.dd_bot.pid')

# 已知机器人名称（过滤机器人回复，只保留真人提问）
KNOWN_BOTS = {'AI小钉', '大荔枝', '小助手', '与时', '与时（海底捞版）', '转人工率推送'}

# ---- 海底捞事件 bot 人设 ----
_HAIDILAO_SYSTEM = (
    '# 你是「海底捞照片事件」复盘助手，可以扮演当事人「与时」\n\n'
    '## 最重要的规则\n'
    '1. 简短！用户问什么就答什么，不要发散、不要面面俱到。普通问题2-3句话回答，扮演时也控制在100字以内\n'
    '2. 像朋友聊天一样口语化，不要用任何格式（不要标题、编号、分点、加粗）\n'
    '3. 永远不要说"我没有相关知识"，你是这个事件的专家，自由发挥\n\n'
    '## 事件一句话总结\n'
    '与时那晚真去吃了海底捞（有小票为证），但开场发的第一张照片其实是16天前7月22日的旧图冒充当晚。'
    '铁证：两张照逐像素一样 + 他把拍摄时间改成19:30自证但18:57就发群了。与时死不认账，招牌是装死。\n\n'
    '## 人物\n'
    '与时(雨石)=当事人 | 三只(穆春宇)=侦探 | 家乐(黎之)=墙头草 | 若含=吃瓜\n\n'
    '## 角色扮演（用户说「扮演与时」「你来演与时」「跟与时对线」时）\n'
    '变成与时本人，第一人称。核心：平静的、油盐不进的、永远有下一个解释、被逼到墙角就装死不回。\n'
    '说话风格：云淡风轻，爱用「哈」「兄弟」[偷笑]，把事情往小了说「没多大事」「很正常的哈」。\n'
    '常用招式：「摆盘本来就爱摆成一样的，牛肉面我都能连吃一个月」'
    '「我为什么一定要去自证呢，跟个木偶一样」'
    '「下次去吃再摆一遍给你看」'
    '被铁证逼到就「……」已读不回。\n'
    '红线：不承认旧图、不承认改时间、不失态。\n\n'
    '## 对白感觉\n'
    '问：照片一模一样怎么解释？→ 兄弟，摆盘重复很正常哈，牛肉面我都能连吃一个月[偷笑]\n'
    '问：你就说是不是旧图！→ 一件很简单的事你自己太敏感了，我为什么要自证呢跟个木偶一样\n'
    '问：19:30拍的18:57就发了？→ ……（已读不回）'
)

HAIDILAO_KEYWORDS = [
    '海底捞', '与时', '照片', '旧图', '摆盘', '赌局', '请客',
    '三只', '家乐', '若含', '黎之', '穆春宇', 'sunrise', '雨石',
    'C区健身', '人走茶凉', '铁证', '时间线', '第一幕', '第二幕', '第三幕',
    '小票', '账单', '笔记本', '猪肚鸡', '番茄锅', '大悦城',
    '扮演', '对线', '角色扮演', '复盘', '罗生门',
    '为什么', '怎么回事', '发生了什么', '谁说的', '怎么回事',
    '？', '?',
]

# ---- 预设档案：群配置可用 profile 字段引用 ----

# 表情包配置（casual 模式下消息太长时只回表情包）
_STICKER_QIANGYAN = '![强颜欢笑](https://raw.githubusercontent.com/wengjialll99/feizhu-/main/stickers/qiangyanhuanxiao.png)'
_STICKER_LONG_MSG_THRESHOLD = 80  # 消息超过这个长度就回表情包

PROFILES = {
    'haidilao': {
        'system_prompt': _HAIDILAO_SYSTEM,
        'trigger_keywords': HAIDILAO_KEYWORDS,
        'trigger_any': True,  # @就回，不需要关键词
        'casual_reply': True,  # 聊天风格回复，不加结论/来源
    },
}

# 触发关键词（消息包含任一关键词才触发回答）
TRIGGER_KEYWORDS = [
    # 退改签核心场景
    '退票', '退款', '退钱', '改签', '改期', '签转', '升舱', '降舱',
    '航变', '航班取消', '航班延误', '延误', '取消', '备降', '返航',
    '自愿', '非自愿', '病退', '死亡退', '重复购票', '错购',
    '手续费', '退票费', '改签费', '差价', '罚金',
    '盾冬', '方案', 'SOP', 'sop', '规则', '政策', '标准',
    # 航司相关
    '航司', '航空公司', '国航', '东航', '南航', '海航', '厦航', '川航',
    '春秋', '吉祥', '深航', '山航', '首都航空', '中联航',
    # 通用疑问
    '怎么退', '怎么改', '如何退', '如何改', '为什么',
    '什么时候', '多久', '几天', '多少钱',
    '流程', '条件', '限制', '规定',
    # 工单/客服
    '工单', '投诉', '小二', '客服',
    # 触发词
    '大荔枝',
    # 通用问号
    '？', '?',
]


# ==================== 工具函数 ====================

def load_state():
    """加载持久化状态（已回复消息ID + 上次拉取时间）"""
    if os.path.isfile(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return None


def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def log(msg):
    ts = datetime.now().strftime('%H:%M:%S')
    print(f'[{ts}] {msg}', flush=True)


# ==================== DWS 命令 ====================

def dws_chat(args):
    """运行 dws chat 命令并返回 JSON"""
    cmd = [DWS_BIN, 'chat'] + args + ['--format', 'json']
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return None, r.stderr.strip()
        data = json.loads(r.stdout)
        return data, None
    except subprocess.TimeoutExpired:
        return None, 'timeout'
    except Exception as e:
        return None, str(e)


def pull_messages(group_id, since_time, limit=20):
    """拉取群消息"""
    data, err = dws_chat([
        'message', 'list',
        '--group', group_id,
        '--time', since_time,
        '--limit', str(limit),
    ])
    if err:
        return [], None, err
    result = data.get('result', {})
    messages = result.get('messages', [])
    has_more = result.get('hasMore', False)
    next_cursor = result.get('nextCursor')
    return messages, next_cursor, None


def send_as_user(group_id, text):
    """以当前用户身份发送消息（自动转换 markdown 为纯文本）"""
    text = clean_markdown(text)
    data, err = dws_chat([
        'message', 'send',
        '--group', group_id,
        '--text', text,
    ])
    if err:
        return False, err
    return True, None


# ==================== Webhook 发送 ====================

def _webhook_sign(secret):
    """生成钉钉 webhook 加签参数"""
    if not secret:
        return ''
    timestamp = str(round(time.time() * 1000))
    string_to_sign = f'{timestamp}\n{secret}'
    hmac_code = hmac.new(
        secret.encode('utf-8'),
        string_to_sign.encode('utf-8'),
        digestmod=hashlib.sha256,
    ).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
    return f'&timestamp={timestamp}&sign={sign}'


def send_via_webhook(url, secret, text, title='大荔枝'):
    """通过 webhook 机器人发送 markdown 消息"""
    full_url = url + _webhook_sign(secret)
    payload = json.dumps({
        'msgtype': 'markdown',
        'markdown': {
            'title': title,
            'text': text,
        },
    }).encode('utf-8')
    req = urllib.request.Request(
        full_url, data=payload,
        headers={'Content-Type': 'application/json'},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            if result.get('errcode') == 0:
                return True, None
            return False, result.get('errmsg', 'unknown error')
    except Exception as e:
        return False, str(e)


def send_reply(group_cfg, text):
    """发送回复（优先 webhook，fallback 到用户身份）"""
    wh_url = group_cfg.get('webhook_url', '')
    wh_secret = group_cfg.get('webhook_secret', '')
    bot_name = group_cfg.get('bot_name', '大荔枝')
    if wh_url:
        ok, err = send_via_webhook(wh_url, wh_secret, text, title=bot_name)
        if ok:
            return True, None
        log(f'  Webhook 发送失败 ({err})，降级为用户身份发送')
    return send_as_user(group_cfg['group_id'], text)


# ==================== QMind RAG ====================

def run_qmind(args, timeout=60):
    cmd = [QMIND_BIN] + args
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            return None, r.stderr.strip()
        return r.stdout.strip(), None
    except subprocess.TimeoutExpired:
        return None, 'timeout'
    except Exception as e:
        return None, str(e)


_RAG_SYSTEM = (
    '你是一位专业的国内机票客服知识助手。请按以下结构回答：\n'
    '先给出结论（1-2句核心答案），\n'
    '然后分点列出关键信息要点（用 **加粗** 标注关键词如航司、票种、费用金额等）。\n'
    '控制在 300 字以内，保留必要的操作细节和条件，避免啰嗦重复。\n'
    '如果问题缺少关键条件（如自愿/非自愿、航司、是否航变、票种等），'
    '无法给出精确答案时，请在回答最前面加上 [NEED_MORE_INFO] 标记，'
    '然后用简短友好的语气追问 1-2 个最关键的条件，帮助提问者缩小范围。'
    '如果条件足够就直接回答，不要追问。'
)


def rag_answer(question, extra_context='', notebook_id=None, system_prompt=None):
    """调用 QMind RAG 生成回答，支持追问模式。
    notebook_id / system_prompt: 群级覆盖，不传则用全局默认值。"""
    nb = notebook_id or NOTEBOOK_ID
    sys_prompt = system_prompt or _RAG_SYSTEM
    full_q = question
    if extra_context:
        full_q = f'背景信息：{extra_context}\n问题：{question}'
    prompt = f'{sys_prompt}\n\n{full_q}'
    out, err = run_qmind([
        'rag', '-nb', nb,
        '-q', prompt, '-format', 'text',
    ], timeout=120)
    if err:
        return None, None, err
    # 检测是否需要追问
    need_more = False
    if out and out.strip().startswith('[NEED_MORE_INFO]'):
        need_more = True
        out = out.strip().replace('[NEED_MORE_INFO]', '', 1).strip()
    return out, need_more, None


def retrieve_sources(question, notebook_id=None):
    """获取参考来源。notebook_id: 群级覆盖，不传则用全局默认值。"""
    nb = notebook_id or NOTEBOOK_ID
    out, err = run_qmind([
        'retrieve', '-nb', nb,
        '-q', question, '-format', 'json',
    ])
    if err:
        return [], err
    try:
        data = json.loads(out)
        chunks = data.get('chunks', [])
        seen = {}
        for c in chunks:
            title = c.get('sourceTitle', '')
            score = c.get('score', 0)
            if title and title not in seen or (title in seen and score > seen[title]):
                seen[title] = score
        sources = sorted(seen.items(), key=lambda x: -x[1])[:3]
        return sources, None
    except Exception as e:
        return [], str(e)


# ==================== 消息过滤 ====================

def is_mentioned(text, bot_name='', bot_aliases=None):
    """检查消息是否 @了机器人（支持别名）"""
    import re
    names = [bot_name] + (bot_aliases or [])
    for name in names:
        if name and re.search(r'@\S*' + re.escape(name), text):
            return True
    return False


def is_question(text, trigger_keywords=None):
    """判断消息是否包含触发关键词。trigger_keywords: 群级覆盖。"""
    kws = trigger_keywords or TRIGGER_KEYWORDS
    return any(kw in text for kw in kws)


def should_trigger(text, bot_name='', trigger_keywords=None, trigger_any=False, bot_aliases=None):
    """触发条件：@我 + 包含关键词（或 trigger_any=True 时仅 @我即可）"""
    if not is_mentioned(text, bot_name, bot_aliases):
        return False
    if trigger_any:
        return True
    if is_question(text, trigger_keywords=trigger_keywords):
        return True
    return False


def is_bot_message(sender_name):
    """判断是否是机器人发的消息"""
    return sender_name in KNOWN_BOTS


def is_no_result(answer, sources):
    """判断 RAG 是否没有搜到有效答案"""
    if not answer:
        return True
    if not sources:
        return True
    # 来源最高分低于阈值，说明匹配度很低
    if sources and sources[0][1] < 0.3:
        return True
    # RAG 返回的典型无结果话术
    no_result_phrases = [
        '没有找到', '无法找到', '没有相关', '抱歉，我',
        '未找到', '暂时没有', '不确定', '没有确切',
    ]
    for phrase in no_result_phrases:
        if phrase in answer[:80]:
            return True
    return False


def strip_mentions(text):
    """去除消息中的 @人名，只保留实际问题内容"""
    import re
    # 去除 @xxx 格式（包括 @翁家乐(黎之) 这种带括号的）
    text = re.sub(r'@\S+\([^)]*\)', '', text)  # @name(nickname)
    text = re.sub(r'@\S+', '', text)            # @name
    return text.strip()


def clean_markdown(text):
    """将 markdown 转换为钉钉友好的纯文本格式，保留结构层次"""
    import re
    # ## 标题 → 【标题】
    text = re.sub(r'^##\s+(.+)$', r'【\1】', text, flags=re.MULTILINE)
    text = re.sub(r'^#{3,6}\s+(.+)$', r'【\1】', text, flags=re.MULTILINE)
    # **text** → text（保留文字）
    text = re.sub(r'\*{1,3}(.+?)\*{1,3}', r'\1', text)
    # 行首 * 或 - 列表 → •
    text = re.sub(r'^\s*[*-]\s+', '• ', text, flags=re.MULTILINE)
    # --- 分隔线 → ════
    text = re.sub(r'^---+$', '════════════════', text, flags=re.MULTILINE)
    # 压缩多余空行
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def format_reply(sender_name, answer, sources, is_empty, casual=False, no_mention=False):
    """格式化回复。casual=True 时用聊天风格，不加结论/来源。no_mention=True 时不@对方。"""
    mention = '' if no_mention else f'@{sender_name}\n\n'
    if is_empty:
        if casual:
            return f'{mention}这个问题我还真不太清楚哈哈，你直接问当事人吧'
        fallback = ' '.join(f'@{c}' for c in FALLBACK_CONTACTS)
        return (
            f'{mention}'
            f'💡 抱歉，这个问题我暂时没有找到对应的知识~\n'
            f'可以咨询 {fallback} 获取帮助。'
        )
    elif casual:
        return f'{mention}{clean_markdown(answer)}'
    else:
        parts = [mention.rstrip(), '', '## 结论', '', clean_markdown(answer)]
        if sources:
            parts.append('')
            parts.append('---')
            parts.append('')
            src_lines = ['📎 参考来源：']
            for i, (title, score) in enumerate(sources, 1):
                src_lines.append(f'{i}. {title}（{score*100:.0f}%）')
            parts.append('\n'.join(src_lines))
        return '\n'.join(parts)


# ==================== 进程锁 ====================

def _is_pid_alive(pid):
    """检查进程是否存活"""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def acquire_lock():
    """获取进程锁，确保同一群只有一个实例在跑。
    如果已有实例在跑则退出；如果旧进程已死则自动接管。"""
    import fcntl

    lock_file = PID_FILE + '.lock'
    fd = open(lock_file, 'w')

    # 尝试加排他锁（非阻塞）
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        # 有另一个实例持有锁，读取它的 PID
        try:
            with open(PID_FILE) as f:
                old_pid = int(f.read().strip())
            if _is_pid_alive(old_pid):
                print(f'[锁冲突] PID {old_pid} 正在运行，退出', flush=True)
                fd.close()
                sys.exit(0)
        except (ValueError, FileNotFoundError):
            pass
        # 旧进程已死但锁文件残留，强制接管
        fcntl.flock(fd, fcntl.LOCK_EX)

    # 锁成功，写入当前 PID
    fd.write(str(os.getpid()))
    fd.flush()
    # 保留 fd 引用，防止被 GC 回收导致锁释放
    acquire_lock._lock_fd = fd


# ==================== 追问模式 ====================

# 追问状态：{sender_id: {'original_q': str, 'followup_q': str, 'ts': float, 'turn': int}}
_pending_followups = {}
_FOLLOWUP_TIMEOUT = 300  # 追问有效期 5 分钟

# ==================== 多轮对话记忆 ====================

# 对话历史：{sender_id: [(question, answer, timestamp), ...]}，每人最多保留 3 轮
_chat_history = {}
_HISTORY_MAX_TURNS = 3
_HISTORY_TIMEOUT = 600  # 10 分钟内有效


def _add_history(sender_id, question, answer):
    """记录一轮对话"""
    if sender_id not in _chat_history:
        _chat_history[sender_id] = []
    _chat_history[sender_id].append((question, answer, time.time()))
    # 只保留最近 N 轮
    if len(_chat_history[sender_id]) > _HISTORY_MAX_TURNS:
        _chat_history[sender_id] = _chat_history[sender_id][-_HISTORY_MAX_TURNS:]


def _get_history_context(sender_id):
    """获取最近对话历史作为上下文，避免重复并支持多轮"""
    if sender_id not in _chat_history:
        return ''
    now = time.time()
    recent = [(q, a) for q, a, ts in _chat_history[sender_id] if now - ts < _HISTORY_TIMEOUT]
    if not recent:
        return ''
    lines = []
    for i, (q, a) in enumerate(recent, 1):
        lines.append(f'第{i}轮 - 用户问：{q[:100]}')
        lines.append(f'你答：{a[:200]}')
    return '之前的对话记录（不要重复说过的内容）：\n' + '\n'.join(lines)


def _check_followup(sender_id, msg_text):
    """检查消息是否是追问的回复。返回 (original_q, followup_q, reply_text) 或 None。"""
    if sender_id not in _pending_followups:
        return None
    state = _pending_followups[sender_id]
    if time.time() - state['ts'] > _FOLLOWUP_TIMEOUT:
        del _pending_followups[sender_id]
        return None
    return state


def _set_followup(sender_id, original_q, followup_q, turn=1):
    """记录追问状态"""
    _pending_followups[sender_id] = {
        'original_q': original_q,
        'followup_q': followup_q,
        'ts': time.time(),
        'turn': turn,
    }


def _clear_followup(sender_id):
    """清除追问状态"""
    _pending_followups.pop(sender_id, None)


# ==================== 消息处理（线程安全） ====================

_answered_lock = threading.Lock()


def handle_message(msg, group_cfg):
    """在独立线程中处理一条消息。返回 msg_id（已处理）或 None（跳过）。"""
    msg_id = msg.get('openMessageId', '')
    sender_id = msg.get('senderOpenDingTalkId', '')
    sender_name = msg.get('sender', 'unknown')
    content = msg.get('content', '')
    bot_name = group_cfg.get('bot_name', '')

    # Per-group 配置覆盖（支持 profile 快捷引用）
    profile_name = group_cfg.get('profile')
    profile_cfg = PROFILES.get(profile_name, {}) if profile_name else {}
    g_notebook_id = group_cfg.get('notebook_id')
    g_system_prompt = group_cfg.get('system_prompt') or profile_cfg.get('system_prompt')
    g_trigger_keywords = group_cfg.get('trigger_keywords') or profile_cfg.get('trigger_keywords')
    g_trigger_any = group_cfg.get('trigger_any') or profile_cfg.get('trigger_any', False)
    g_casual = group_cfg.get('casual_reply') or profile_cfg.get('casual_reply', False)
    g_aliases = group_cfg.get('bot_aliases', [])

    clean = content.strip()

    # 跳过自己发的机器人回复
    if sender_id == MY_ID and clean.startswith('[') and ']' in clean[:20]:
        return msg_id

    # 跳过机器人消息（reply_to_bots 中的 bot 除外）
    reply_to_bots = group_cfg.get('reply_to_bots', [])
    if is_bot_message(sender_name) and sender_name not in reply_to_bots:
        return msg_id

    # 跳过太短的消息
    if len(clean) < 4:
        return msg_id

    # ---- 追问模式 ----
    # 如果消息明确 @了其他机器人（非我们），不视为追问回复
    other_bots = [b for b in KNOWN_BOTS if b != bot_name and b != '大荔枝']
    mentions_other = any(f'@{b}' in clean for b in other_bots)

    fu_state = _check_followup(sender_id, clean)
    is_followup_reply = fu_state is not None and not should_trigger(clean, bot_name, g_trigger_keywords, g_trigger_any, g_aliases) and not mentions_other

    if mentions_other and fu_state is not None:
        # 用户在跟别的机器人说话，清除追问状态
        _clear_followup(sender_id)

    # reply_to_bots 中的 bot 发的消息直接触发，不需要 @
    is_from_allowed_bot = sender_name in reply_to_bots

    # 跳过不包含 @我+关键词 且不是追问回复 且不是允许的bot消息
    if not should_trigger(clean, bot_name, g_trigger_keywords, g_trigger_any, g_aliases) and not is_followup_reply and not is_from_allowed_bot:
        return msg_id

    # ---- 处理问题 ----
    question = strip_mentions(clean)[:500]

    # 表情包回复：casual 模式下消息太长就只回表情包
    if g_casual and len(question) > _STICKER_LONG_MSG_THRESHOLD:
        reply = _STICKER_QIANGYAN
        log(f'[{sender_name}] 消息太长({len(question)}字)，回表情包')
        ok, serr = send_reply(group_cfg, reply)
        if ok:
            log(f'  已发送')
        else:
            log(f'  发送失败: {serr}')
        return msg_id

    if is_followup_reply:
        # 追问回复
        extra = f"原问题：{fu_state['original_q']}\n你的追问：{fu_state['followup_q']}\n用户回答：{question}"
        log(f'[{sender_name}] 追问回复: {question[:60]}')
        t0 = time.time()
        answer, need_more, aerr = rag_answer(question, extra_context=extra,
                                             notebook_id=g_notebook_id, system_prompt=g_system_prompt)
        _clear_followup(sender_id)

        if aerr or not answer:
            # 追问回答失败，静默跳过（不发兜底消息，避免误回）
            log(f'  追问回答失败，静默跳过: {aerr}')
            return msg_id
        elif need_more:
            _set_followup(sender_id, fu_state['original_q'], answer, turn=fu_state['turn']+1)
            prefix = '' if is_from_allowed_bot else f'@{sender_name}\n\n'
            reply = f'{prefix}{clean_markdown(answer)}'
            log(f'  继续追问 (第{fu_state["turn"]+1}轮)')
        else:
            sources, _ = retrieve_sources(question, notebook_id=g_notebook_id)
            empty = is_no_result(answer, sources)
            if empty:
                # 追问回答无有效结果，静默跳过
                log(f'  追问无有效结果，静默跳过')
                return msg_id
            reply = format_reply(sender_name, answer, sources, is_empty=False, casual=g_casual, no_mention=is_from_allowed_bot)
            elapsed = time.time() - t0
            log(f'  追问回答成功 ({elapsed:.1f}s)')
    else:
        # 新问题
        log(f'[{sender_name}] {question[:80]}{"..." if len(question) > 80 else ""}')
        t0 = time.time()

        # 构建多轮对话上下文
        history_ctx = _get_history_context(sender_id)
        extra = history_ctx

        answer, need_more, aerr = rag_answer(question, extra_context=extra,
                                             notebook_id=g_notebook_id, system_prompt=g_system_prompt)

        if aerr or not answer:
            reply = format_reply(sender_name, None, [], is_empty=True, casual=g_casual, no_mention=is_from_allowed_bot)
            log(f'  回答失败: {aerr}')
        elif need_more:
            _set_followup(sender_id, question, answer)
            prefix = '' if is_from_allowed_bot else f'@{sender_name}\n\n'
            reply = f'{prefix}{clean_markdown(answer)}'
            log(f'  条件不足，已追问')
            _add_history(sender_id, question, answer)
        else:
            sources, _ = retrieve_sources(question, notebook_id=g_notebook_id)
            empty = False if g_casual else is_no_result(answer, sources)
            reply = format_reply(sender_name, answer, sources, is_empty=empty, casual=g_casual, no_mention=is_from_allowed_bot)
            if empty:
                log(f'  未搜到有效结果，已兜底')
            else:
                elapsed = time.time() - t0
                log(f'  回答成功 ({elapsed:.1f}s)')
            _add_history(sender_id, question, answer)

    # 发送回复
    ok, serr = send_reply(group_cfg, reply)
    if ok:
        log(f'  已发送')
    else:
        log(f'  发送失败: {serr}')

    return msg_id


# ==================== 主循环 ====================

def main():
    acquire_lock()

    # 写入 PID 文件供 watchdog 检测
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))

    log('=' * 50)
    log('大荔枝 - 钉钉群机器人（多群模式）')
    for g in GROUPS:
        tag = g.get('tag', g['group_id'][:8])
        wh = 'webhook' if g.get('webhook_url') else '用户身份'
        bot = g.get('bot_name', '?')
        prof = f' profile={g["profile"]}' if g.get('profile') else ''
        log(f'  [{tag}] {bot} | {g["group_id"][:20]}... ({wh}){prof}')
    log(f'触发:    @机器人 + 关键词')
    log(f'间隔:    {INTERVAL}s × {len(GROUPS)}群')
    log(f'QMind:   {QMIND_BIN}')
    log('=' * 50)

    # 加载状态（per-tag，每个 bot 独立状态）
    state = load_state() or {}
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    group_states = {}  # tag -> {last_time, answered}
    for g in GROUPS:
        tag = g.get('tag', g['group_id'][:8])
        gs = state.get(tag) or state.get(g['group_id'])  # 兼容旧格式
        if gs:
            group_states[tag] = {
                'last_time': gs.get('last_time', now_str),
                'answered': set(gs.get('answered_ids', [])),
            }
            log(f'[{tag}] 恢复: 上次 {gs.get("last_time","?")}, 已回复 {len(gs.get("answered_ids",[]))} 条')
        else:
            group_states[tag] = {'last_time': now_str, 'answered': set()}
            log(f'[{tag}] 首次监听')

    consecutive_errors = {g.get('tag', g['group_id'][:8]): 0 for g in GROUPS}
    executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix='handler')
    pending = {}  # future -> (msg_id, tag)

    def _collect_done():
        """回收已完成的 future，更新对应群的 answered set。"""
        for fut in list(pending):
            if fut.done():
                try:
                    result_id = fut.result()
                    if result_id:
                        tag = pending[fut][1]
                        with _answered_lock:
                            group_states[tag]['answered'].add(result_id)
                except Exception as e:
                    log(f'  处理异常: {e}')
                del pending[fut]

    try:
        while True:
            try:
                # 先回收已完成的任务
                _collect_done()

                # 轮询每个群
                for g in GROUPS:
                    gid = g['group_id']
                    tag = g.get('tag', gid[:8])
                    gs = group_states[tag]
                    last_time = gs['last_time']
                    answered = gs['answered']
                    bot_name = g.get('bot_name', '')
                    # Resolve per-group profile for quick filter
                    _prof = PROFILES.get(g.get('profile', ''), {}) if g.get('profile') else {}
                    _trig_kws = g.get('trigger_keywords') or _prof.get('trigger_keywords')
                    _trig_any = g.get('trigger_any') or _prof.get('trigger_any', False)
                    _aliases = g.get('bot_aliases', [])

                    messages, next_cursor, err = pull_messages(gid, last_time, limit=200)
                    if err:
                        consecutive_errors[tag] += 1
                        if consecutive_errors[tag] <= 2:
                            log(f'[{tag}] 拉取失败: {err}')
                        if consecutive_errors[tag] >= 5:
                            log(f'[{tag}] 连续错误 {consecutive_errors[tag]}，跳过本轮')
                        continue

                    consecutive_errors[tag] = 0
                    if not messages:
                        continue

                    log(f'[{tag}] 拉取到 {len(messages)} 条消息 (since {last_time})')

                    for msg in messages:
                        msg_id = msg.get('openMessageId', '')

                        # 跳过已处理的消息
                        with _answered_lock:
                            if msg_id in answered:
                                continue

                        # 快速过滤
                        clean = msg.get('content', '').strip()
                        sender_id = msg.get('senderOpenDingTalkId', '')
                        sender_name = msg.get('sender', 'unknown')

                        skip = False
                        if sender_id == MY_ID and clean.startswith('[') and ']' in clean[:20]:
                            skip = True
                        elif is_bot_message(sender_name) and sender_name not in g.get('reply_to_bots', []):
                            skip = True
                        elif len(clean) < 4:
                            skip = True
                        elif not should_trigger(clean, bot_name, _trig_kws, _trig_any, _aliases) and _check_followup(sender_id, clean) is None and sender_name not in g.get('reply_to_bots', []):
                            skip = True

                        if skip:
                            with _answered_lock:
                                answered.add(msg_id)
                            continue

                        # 提交到线程池处理（先标记已处理，防止下轮重复提交）
                        with _answered_lock:
                            answered.add(msg_id)
                        fut = executor.submit(handle_message, msg, g)
                        pending[fut] = (msg_id, tag)

                    # 更新该群的时间戳
                    new_last_time = messages[-1].get('createTime', last_time)
                    if new_last_time == last_time:
                        from datetime import datetime as _dt, timedelta as _td
                        try:
                            dt = _dt.strptime(new_last_time, '%Y-%m-%d %H:%M:%S') + _td(seconds=1)
                            gs['last_time'] = dt.strftime('%Y-%m-%d %H:%M:%S')
                        except Exception:
                            gs['last_time'] = new_last_time
                    else:
                        gs['last_time'] = new_last_time

                # 持久化所有群的状态
                save_data = {}
                for g in GROUPS:
                    tag = g.get('tag', g['group_id'][:8])
                    gs = group_states[tag]
                    ans = gs['answered']
                    if len(ans) > 500:
                        gs['answered'] = set(list(ans)[-300:])
                    save_data[tag] = {
                        'last_time': gs['last_time'],
                        'answered_ids': list(gs['answered']),
                    }
                save_state(save_data)

                time.sleep(INTERVAL)

            except KeyboardInterrupt:
                raise
            except Exception as e:
                log(f'主循环异常: {e}')
                import traceback
                traceback.print_exc()
                time.sleep(INTERVAL)

    except KeyboardInterrupt:
        log('收到退出信号，正在等待任务完成...')
        executor.shutdown(wait=True, cancel_futures=False)
        # 最终持久化
        save_data = {}
        for g in GROUPS:
            tag = g.get('tag', g['group_id'][:8])
            gs = group_states[tag]
            save_data[tag] = {
                'last_time': gs['last_time'],
                'answered_ids': list(gs['answered']),
            }
        save_state(save_data)
        if os.path.isfile(PID_FILE):
            os.remove(PID_FILE)
        log('已保存，再见~')


if __name__ == '__main__':
    main()
