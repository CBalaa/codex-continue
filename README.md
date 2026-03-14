# Codex Auto-Continue Patch

这套文件是给你本机 npm 安装版 `codex` 打补丁用的，目标是给交互式 `codex`
增加一个“私有网页控制台”远程控制方案：

- 默认进入 `chat mode`
- 通过一个共享网页控制台管理多个运行中的 Codex 实例
- 每次启动都会打印一个当前 Codex 实例专属的 `control key`
- turn 完成后网页实时看到 Codex 回复和控制回执
- `--auto-mode` 仍可从启动时直接带一个默认自动任务
- `--native` 回到原始 Codex，不启用补丁逻辑

当前实现只支持 Unix / Linux / macOS；Windows 适配和旧的 ntfy transport 已移除。

## 当前语义

下面这些行为保持不变：

- `sender:"user"` 表示远程用户发来的控制消息
- `sender:"codex"` 表示 Codex 返回的结果/回执
- `chat` 模式下：`messages` 追加到 FIFO 队列
- `auto` 模式下：`tasks:[{message,count}]` 覆盖后续自动任务
- `stop_auto` 在当前 turn 完成后生效
- 远程更新不能打断当前 turn，只能在当前 turn 完成后生效
- turn 完成后会把结果发回网页端
- auto 模式会回传剩余任务进度
- 本地手动发起的 turn 不会混入网页端最近回复列表
- 已修过的“窗口聚焦后误判 Esc 导致自动注入失效”行为保持不回归

## 目录

- `install_codex_auto_continue_patch.py`：安装脚本
- `uninstall_codex_auto_continue_patch.py`：卸载脚本
- `codex_npm_auto_continue/codex-wrapper.js`：替换 npm `codex.js` 的 wrapper
- `codex_npm_auto_continue/codex-auto-continue-pty.py`：Unix PTY bridge + helper 注册/控制逻辑
- `codex_npm_auto_continue/codex-auto-continue-notify.py`：接收 Codex turn 完成通知
- `codex_npm_auto_continue/codex-auto-continue-web-server.py`：共享网页控制台服务
- `codex_npm_auto_continue/codex-auto-continue-web.html`：网页控制台
- `codex_npm_auto_continue/codex-auto-continue-web.css`：网页控制台样式
- `codex_npm_auto_continue/codex-auto-continue-web.js`：网页控制台前端逻辑

## 安装

默认 patch 当前 PATH 里的 `codex`。

```bash
python3 install_codex_auto_continue_patch.py
```

如果要指定 launcher 所在目录：

```bash
python3 install_codex_auto_continue_patch.py --install-dir /path/to/@openai/codex/bin
```

安装时会：

- 解析 `codex` 实际指向的 npm 包目录
- 备份原始 `bin/codex.js` 为 `bin/codex.real.js`
- 把 wrapper、helper、共享网页服务、网页静态资源复制到同一个 `bin/` 目录

## 卸载

```bash
python3 uninstall_codex_auto_continue_patch.py
```

如果想保留 `bin/codex.real.js` 备份：

```bash
python3 uninstall_codex_auto_continue_patch.py --keep-backup
```

## 配置

remote 配置从 Codex 使用的同一个 `config.toml` 路径读取。

默认路径是：

```bash
~/.codex/config.toml
```

如果你平时通过 `CODEX_HOME` 或 `-c config_file="..."` 改过 Codex 的配置路径，
wrapper 会自动跟着用同一个文件。

最小配置：

```toml
codex-remote-web-password = "change-me-now"
```

推荐本地配置：

```toml
codex-remote-web-bind = "127.0.0.1"
codex-remote-web-port = 8765
codex-remote-web-password = "change-me-now"
```

也兼容注释写法：

```toml
# codex-remote-web-bind = "127.0.0.1"
# codex-remote-web-port = 8765
# codex-remote-web-password = "change-me-now"
```

配置项说明：

- `codex-remote-web-bind`：网页控制台监听地址；默认 `127.0.0.1`
- `codex-remote-web-port`：网页控制台监听端口；默认 `8765`
- `codex-remote-web-password`：网页登录密码；必填

`control key` 不写入配置文件，它会在每次启动 `codex` 时由 helper 现场生成，并在终端打印出来。
helper 重启后，旧 key 自动失效。
它不是 Codex `/status` 里的 session id，也不依赖 `codex resume <id>`。
网页登录只需要密码；`control key` 在登录后新增标签页时输入。

如果当前是 chat / auto 模式，但没有找到 `codex-remote-web-password`，wrapper 会警告并自动退回 native 模式。

## 快速开始

1. 安装 patch

```bash
python3 install_codex_auto_continue_patch.py
```

2. 在 Codex 配置文件里写入网页控制台密码

```toml
codex-remote-web-password = "change-me-now"
```

3. 在电脑上启动 Codex

```bash
codex
```

4. 终端里会打印共享网页地址和本次启动的 control key，例如：

```text
[codex-auto-continue] private web console on "http://127.0.0.1:8765/" from /home/you/.codex/config.toml.
[codex-auto-continue] control key for this Codex: JYk1xwQ4dP6k4uCqf7x95QxP
```

5. 浏览器打开这个地址，先用配置文件里的网页登录密码登录

6. 登录后，在页面上方点击/填写“新标签页 Key”，输入终端刚打印出来的 `control key`

7. 在网页里：

- 每个标签页对应一个正在运行的 Codex 实例
- `Chat 模式` 面板发送消息
- `Auto 模式` 面板提交任务队列 JSON，例如：

```json
[{"message":"继续","count":5}]
```

- 点击 `stop_auto` 可以在当前 turn 完成后停掉 auto
- 可以继续新增别的标签页，并输入其他 Codex 实例的 key
- 在页面右侧查看当前标签页对应实例的最近 Codex 回复和控制回执

## 使用

直接启动：

```bash
codex
```

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

- `codex`：默认 chat 模式，等待网页端控制消息
- `codex --chat-mode`：显式进入 chat 模式
- `codex --auto-mode`：显式进入 auto 模式
- `codex --native`：跳过 wrapper，进入原始 Codex

auto 模式下自定义启动任务 prompt：

```bash
codex --auto-mode --auto-continue-prompt "继续"
```

限制启动时默认自动任务次数：

```bash
codex --auto-mode --auto-continue-limit 3
```

运行时网页端实际发送到 helper 的控制协议仍然是统一 JSON 语义：

chat：

```json
{"sender":"user","mode":"chat","messages":["你好"]}
```

auto：

```json
{"sender":"user","mode":"auto","tasks":[{"message":"继续","count":5}]}
```

stop_auto：

```json
{"sender":"user","command":"stop_auto"}
```

网页会显示：

- 当前已连接的 Codex 标签页列表
- 当前模式
- 空闲 / 执行中状态
- chat 队列长度
- auto 剩余总数
- 当前 auto 任务内容与剩余次数
- 当前标签页对应实例的 key 摘要
- 最近 Codex 回复
- 最近控制回执与状态事件

如果你本地手动按裸 `Esc` 或 `Ctrl+C`，本场会话会立刻切回 manual。

## 本地启动与验证

建议按下面方式本地验证一遍：

1. 启动 `codex`
2. 再启动第二个 `codex`
3. 浏览器打开网页并登录
   - 只输入配置密码
4. 新建第一个标签页
   - 输入第一个终端打印的 `control key`
5. 新建第二个标签页
   - 输入第二个终端打印的 `control key`
6. 切换到任一标签页，在 `Chat 模式` 发送一条消息
7. 等 turn 完成，确认页面只在当前标签页看到对应 Codex 的回复
8. 切到 `Auto 模式`，提交：

```json
[{"message":"继续","count":2}]
```

9. 确认当前 turn 完成后继续执行，并看到剩余次数递减
10. 点击 `stop_auto`，确认在当前 turn 完成后回到 manual

## 公网部署建议

第一阶段先本地跑通即可；后续要挂公网时，建议至少做到：

- 用 Nginx 或 Caddy 做反向代理，不要直接裸露 helper 监听端口
- 强制 HTTPS
- 使用强密码，不要复用弱口令
- `control key` 只通过当前终端查看，不要记录到长期共享文档里
- 监听地址改成内网地址或 `127.0.0.1`，由反代暴露外层入口
- 在反代层加基础限流
- 最好限制来源 IP 或再套一层额外认证
- 定期轮换密码
- 公网环境不要把密码继续明文放在长期共享配置里，后续应迁移到更安全的 secret 管理方式

## 调试

打开 debug 日志：

```bash
export CODEX_AUTO_CONTINUE_DEBUG=1
codex
```

默认日志路径：

```bash
/tmp/codex-auto-continue-debug.log
```

## 已移除

- 旧的 ntfy remote transport
- Windows / ConPTY 运行路径
- `--auto-continue`，改用 `--auto-mode`
- `--no-auto-continue`，改用 `--native`
- 旧的 ntfy 配置项与相关 CLI 参数
