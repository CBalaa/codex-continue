# Codex Auto-Continue Patch

这套文件是给你本机 npm 安装版 `codex` 打补丁用的，目标是给交互式 `codex`
增加远程控制能力：

- 默认直接进入 `chat mode`，等待手机通过 ntfy 发消息
- `--auto-mode`：进入自动模式，按队列自动重复发消息
- `--native`：回到原始 Codex，不启用远程控制

开启自动模式后，每次 turn 完成，wrapper 会自动把续跑 prompt 输入到 TUI，然
后发 `Tab` 让 Codex 排队/提交；如果你手动按裸 `Esc` 或 `Ctrl+C`，本场会话会
切回 manual。

远程控制现在统一是单 topic JSON 协议：手机和 Codex 共用同一个 ntfy topic，用
`sender` 字段区分 `user` 和 `codex`。

remote topic 现在从 Codex 使用的同一个 `config.toml` 路径里读取；如果当前
是 chat / auto 模式，但没有找到 topic 配置，wrapper 会警告并自动退回原始
Codex。

现在同时支持：

- Linux / macOS / 其他类 Unix 环境
- Windows 10 1809+ / Windows Server 2019+（基于 ConPTY）

## 目录

- `./install_codex_auto_continue_patch.py`：安装脚本
- `./uninstall_codex_auto_continue_patch.py`：卸载脚本
- `./codex_npm_auto_continue/codex-wrapper.js`：替换 npm `codex.js` 的 wrapper
- `./codex_npm_auto_continue/codex-auto-continue-pty.py`：跨平台终端桥接与自动续跑逻辑
- `./codex_npm_auto_continue/codex-auto-continue-notify.py`：接收 turn 完成通知

## 安装

默认 patch 当前 PATH 里的 `codex`。

类 Unix：

```bash
python3 install_codex_auto_continue_patch.py
```

Windows：

```powershell
py -3 .\install_codex_auto_continue_patch.py
```

如果要指定 launcher 所在目录：

```bash
python3 install_codex_auto_continue_patch.py --install-dir /path/to/@openai/codex/bin
```

Windows 对应示例：

```powershell
py -3 .\install_codex_auto_continue_patch.py --install-dir C:\path\to\@openai\codex\bin
```

安装时会：

- 解析 `codex` 实际指向的 npm 包目录
- 备份原始 `bin/codex.js` 为 `bin/codex.real.js`
- 把 wrapper 和 helper 文件复制到同一个 `bin/` 目录

## 卸载

恢复当前 PATH 里的 `codex`。

类 Unix：

```bash
python3 uninstall_codex_auto_continue_patch.py
```

Windows：

```powershell
py -3 .\uninstall_codex_auto_continue_patch.py
```

如果想保留 `bin/codex.real.js` 备份：

```bash
python3 uninstall_codex_auto_continue_patch.py --keep-backup
```

## 快速开始

如果你只是想先跑通一遍，按这个最短流程来：

1. 安装 patch

```bash
python3 install_codex_auto_continue_patch.py
```

2. 在 Codex 使用的配置文件里加上 remote topic

默认路径是 `~/.codex/config.toml`。

如果你平时通过 `CODEX_HOME` 或 `-c config_file="..."` 改过 Codex 的配置路径，
wrapper 会自动跟着用同一个文件。

把下面几行加到那个 `config.toml` 里：

```toml
codex-remote-topic = "your-topic"
codex-remote-base-url = "https://ntfy.sh"
codex-remote-timeout-ms = 3000
```

也兼容注释写法：

```toml
# codex-remote-topic = "your-topic"
# codex-remote-base-url = "https://ntfy.sh"
# codex-remote-timeout-ms = 3000
```

3. 在电脑上启动 Codex

```bash
codex
```

这时默认就是 chat 模式；如果没有找到上面的 topic 配置，会自动警告并退回原始
Codex。

4. 在 iPhone 的 ntfy App 里订阅同一个 `your-topic`
> 或者在`https://ntfy.sh/app`发送

5. 直接向这个 topic 发一条 JSON

chat 模式最小例子：

```json
{"sender":"user","mode":"chat","messages":["你好"]}
```

auto 模式最小例子：

```json
{"sender":"user","mode":"auto","tasks":[{"message":"继续","count":5}]}
```

6. 等 Codex 回复；回复和控制回执都会作为 `{"sender":"codex", ...}` 发回同一个 topic

如果你想停掉自动模式，发：

```json
{"sender":"user","command":"stop_auto"}
```

## 使用

直接启动：

```bash
codex
```

现在不会再弹出启动模式选择；默认直接按 chat 模式启动。

显式指定模式：

```bash
codex --chat-mode
```

```bash
codex --auto-mode
```

```bash
codex --native
```

常用行为：

- `codex`：默认 chat 模式，等待远程消息
- `codex --chat-mode`：显式进入 chat 模式
- `codex --auto-mode`：显式进入 auto 模式
- `codex --native`：跳过 wrapper，进入原始 Codex

如果当前是 chat / auto 模式，但没有在配置文件里找到 topic，wrapper 会警告并自
动退回 native 模式。

当前最常用的配置是：

```toml
codex-remote-topic = "your-topic"
```

如果要走自建 ntfy 服务，也可以加：

```toml
codex-remote-topic = "your-topic"
codex-remote-base-url = "https://ntfy.example.com"
codex-remote-timeout-ms = 5000
```

这里的 `topic`，就是 ntfy App 里订阅的那个 topic 名。

无论是自动模式还是 chat 模式，新的远程配置都不会打断当前正在执行的 turn，而
是在当前 turn 完成后生效。

auto 模式下自定义自动发送的 prompt：

```bash
codex --auto-mode --auto-continue-prompt "继续"
```

限制自动发送“继续”的次数（正整数；不指定时默认无限次）：

```bash
codex --auto-mode --auto-continue-limit 3
```

现在只使用一个 ntfy topic：手机和 Codex 都通过它通信，控制消息与结果消息都
走同一个 topic。

如果你不想把 base URL / timeout 写在 `config.toml` 注释里，也可以用参数覆盖：

```bash
codex --auto-mode \
  --auto-continue-ntfy-base-url https://ntfy.example.com \
  --auto-continue-notify-timeout-ms 5000
```

也可以长期放到环境变量里：

类 Unix：

```bash
export CODEX_AUTO_CONTINUE_NTFY_BASE_URL=https://ntfy.sh
export CODEX_AUTO_CONTINUE_NOTIFY_TIMEOUT_MS=3000
codex
```

Windows PowerShell：

```powershell
$env:CODEX_AUTO_CONTINUE_NTFY_BASE_URL = "https://ntfy.sh"
$env:CODEX_AUTO_CONTINUE_NOTIFY_TIMEOUT_MS = "3000"
codex
```

已移除：

- `--auto-continue`，改用 `--auto-mode`
- `--no-auto-continue`，改用 `--native`
- `--auto-continue-ntfy-topic`，改为从 `config.toml` 注释读取
- `CODEX_AUTO_CONTINUE_NTFY_TOPIC`，改为从 `config.toml` 注释读取

这一条 topic 里所有消息都会统一改成 JSON：

- 手机上的控制消息必须带 `{"sender":"user", ...}`
- Codex 发出来的回执和 turn 通知会带 `{"sender":"codex", ...}`
- helper 只消费 `sender == "user"` 的消息，自动忽略 `sender == "codex"`

## iPhone / ntfy App 怎么发

如果你用的是 iPhone，直接用 ntfy 官方 App 就行：

1. 在 App 里订阅和电脑相同的 topic
2. 打开这个 topic，点发送 / Publish
3. 把消息正文直接填成 JSON
4. 发出去后，电脑端 helper 会从同一个 topic 收到并解析

也就是说，这里的 `topic` 就是 ntfy App 里的 topic，不是额外的新概念。

控制消息目前只支持 JSON。App 里直接贴 JSON 正文即可；如果在电脑上测试，也可
以用 `curl`。

最小测试例子：

```json
{"sender":"user","mode":"chat","messages":["你好"]}
```

如果 Codex 当前空闲，它会马上开始回复；回复完成后，结果也会作为
`{"sender":"codex", ...}` 发回同一个 topic。

下面是完整 `curl` 示例：

切到自动模式，并把任务覆盖为“你好”重复 10 次：

```bash
curl \
  -H "Content-Type: application/json; charset=utf-8" \
  -d '{"sender":"user","mode":"auto","tasks":[{"message":"你好","count":10}]}' \
  https://ntfy.sh/your-topic
```

切到自动模式，并设置多段任务队列：

```bash
curl \
  -H "Content-Type: application/json; charset=utf-8" \
  -d '{"sender":"user","mode":"auto","tasks":[{"message":"你好","count":10},{"message":"再见","count":20}]}' \
  https://ntfy.sh/your-topic
```

切到远程对话模式，并顺序追加两条消息：

```bash
curl \
  -H "Content-Type: application/json; charset=utf-8" \
  -d '{"sender":"user","mode":"chat","messages":["你好","再见"]}' \
  https://ntfy.sh/your-topic
```

停止自动模式，切回手动：

```bash
curl \
  -H "Content-Type: application/json; charset=utf-8" \
  -d '{"sender":"user","command":"stop_auto"}' \
  https://ntfy.sh/your-topic
```

自动模式下，控制消息会整体覆盖任务队列；对话模式下，控制消息会把新消息追加
到 FIFO 队列里。`stop_auto` 只对自动模式生效，并且会在当前 turn 完成后停止后
续自动发送。

发送到手机的 turn 完成通知会包含：

- 当前 `mode`
- 当前 `cwd`
- 本轮最后一条用户输入
- Codex 的最终输出（`last-assistant-message`）

自动模式下还会额外带上任务进度：

- `remaining-total`
- `current-task-remaining`
- `current-task-message`

对话模式下会带上当前 `queued-chat-messages`。

另外，远程控制命令本身也会收到一条执行结果通知，方便确认是否应用成功。

Codex 发到同一 topic 的正文大致像这样：

```json
{"sender":"codex","type":"turn-complete","mode":"chat","queued_chat_messages":0,"assistant":"...","text":"..."}
```

控制回执也会走同一个 topic，例如：

```json
{"sender":"codex","type":"control-response","mode":"auto","remaining_total":10,"text":"..."}
```

如果 ntfy 推送失败，当前版本会记 debug 日志，然后仍然继续自动发送“继续”。
这属于 best-effort 行为，避免续跑被外部通知服务卡住。

## 调试

打开调试输出：

类 Unix：

```bash
CODEX_AUTO_CONTINUE_DEBUG=1 codex
```

Windows PowerShell：

```powershell
$env:CODEX_AUTO_CONTINUE_DEBUG = "1"
codex
```

日志默认会写到系统临时目录：

- 类 Unix：`/tmp/codex-auto-continue-debug.log`
- Windows：`%TEMP%\codex-auto-continue-debug.log`

## 说明

- 这是“本机 npm 包补丁”，不是重编译 Codex。
- 自动续跑模式依赖 Python；类 Unix 会查找 `python3` / `python`，Windows 会查找 `python` / `py -3`。
- 安装脚本现在可以识别 Windows 下的 npm shim（如 `codex.cmd`）并定位真实的 `bin/codex.js`。
- 注入的是本次启动专用的 `notify` override，不会改写你的 `~/.codex/config.toml`。
- ntfy 很适合手机接收：装官方 app 后订阅同一个 topic 即可；也可以换成自建 ntfy 服务。
- 在终端里按裸 `Esc` 或 `Ctrl+C`，会把当前 helper 切回手动模式；后续仍可再通过远程控制重新切回 auto/chat。
- helper 自动向 Codex 注入消息时，会临时屏蔽本地 stdin 透传，避免高频远程发消息时把缓冲输入混进正在发送的内容，导致卡在半输入状态。
