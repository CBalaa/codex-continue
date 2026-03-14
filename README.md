# Codex Auto-Continue Patch

这套补丁给本机 npm 安装版 `codex` 增加一个“私有网页控制台”方案。

当前主流程已经改成：

- 先启动一个本地常驻 manager：`codex --web-console`
- 浏览器登录网页控制台
- 在网页里点击“新建标签页”
- manager 在后台自动启动一个新的 Codex PTY 会话
- 每个标签页对应一个独立的 Codex 实例

第一阶段目标是本地可跑、后续易于公网部署；不再依赖 ntfy，也不再依赖手动输入 key。

## 当前能力

- 单用户密码登录
- 多标签页管理多个 Codex 实例
- 网页直接新建后台 Codex 会话
- `chat` 模式发送消息
- `auto` 模式设置任务队列
- `stop_auto`
- 查看当前模式、空闲/执行中/启动中/失败状态
- 查看 chat 队列长度
- 查看 auto 剩余任务进度
- 查看最近 Codex 回复和控制回执
- SSE 实时刷新前端状态

当前实现只支持 Unix / Linux / macOS；Windows 代码和旧 ntfy transport 已移除。

## 语义保持不变

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
- `codex_npm_auto_continue/codex-auto-continue-pty.py`：Unix PTY bridge + helper
- `codex_npm_auto_continue/codex-auto-continue-notify.py`：接收 Codex turn 完成通知
- `codex_npm_auto_continue/codex-auto-continue-web-server.py`：本地 manager + 网页控制台服务
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
- 把 wrapper、helper、manager、网页静态资源复制到同一个 `bin/` 目录

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

默认路径：

```bash
~/.codex/config.toml
```

如果你平时通过 `CODEX_HOME` 或 `-c config_file="..."` 改过 Codex 的配置路径，
wrapper 会自动跟着使用同一个文件。

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

现在不再使用：

- 网页输入 `control key`
- 用 `/status` 里的 Codex session id 绑定网页标签页

网页和实例的绑定关系由本地 manager 内部维护，标签页只认 manager 的内部 `instance_id`。

## 快速开始

1. 安装 patch

```bash
python3 install_codex_auto_continue_patch.py
```

2. 在 Codex 配置文件里写入网页控制台密码

```toml
codex-remote-web-password = "change-me-now"
```

3. 启动本地 manager

```bash
codex --web-console
```

4. 浏览器打开终端打印出来的地址，例如：

```text
http://127.0.0.1:8765/
```

5. 用配置文件里的网页登录密码登录

6. 点击页面上方“新建标签页”

7. 等待新标签页从“启动中”变成“空闲”

8. 在网页里：

- `Chat 模式` 面板发送消息
- `Auto 模式` 面板提交任务队列 JSON，例如：

```json
[{"message":"继续","count":5}]
```

- 点击 `stop_auto` 可以在当前 turn 完成后停掉 auto
- 右侧查看最近 Codex 回复和控制回执

## 使用方式

### 推荐方式：网页创建 Codex

启动 manager：

```bash
codex --web-console
```

这条命令会前台运行本地网页服务。你可以：

- 直接在当前 shell 保持运行
- 放到 `tmux` / `screen`
- 或者用 `nohup` / `systemd --user` / `supervisord` 之类方式常驻

如果你直接在前台用 `Ctrl+C` 停掉 `codex --web-console`，它会一并终止当前由网页创建的后台 Codex 实例。

### 网页内操作

登录后可以：

- 新建标签页：后台启动一个新的空白 `chat` 会话
- 发送 chat 消息
- 更新 auto 队列
- 发送 `stop_auto`
- 关闭某个实例
- 查看每个实例的状态、队列、剩余任务、最近回复、最近回执

说明：

- 对网页刚新建的后台实例，第一条自动发送的消息会在 Codex TUI 启动稳定后再注入，所以首条消息可能会比后续消息多等几秒。

### 可选兼容方式：手动启动交互式 Codex

如果你仍然手动启动：

```bash
codex --chat-mode
```

或：

```bash
codex --auto-mode
```

这个交互式实例在连接到本地 manager 后，也会自动出现在网页标签页里，不再需要手动输入 key。

### 回到原始 Codex

```bash
codex --native
```

### auto 启动参数

交互式 `auto` 模式下自定义启动任务 prompt：

```bash
codex --auto-mode --auto-continue-prompt "继续"
```

限制启动时默认自动任务次数：

```bash
codex --auto-mode --auto-continue-limit 3
```

### 给网页创建的实例带默认 Codex 参数

如果你希望网页里新建的所有实例默认继承某些 Codex 参数，可以在启动 manager 时一起带上：

```bash
codex --web-console -c model=\"gpt-5\" -c approval_policy=\"never\"
```

之后网页里每次“新建标签页”，都会用这些额外参数去启动新的 Codex 实例。

## 控制协议

网页端发给 helper 的控制语义仍然保持统一 JSON。

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

- 当前标签页列表
- 当前模式
- 空闲 / 执行中 / 启动中 / 失败状态
- chat 队列长度
- auto 剩余总数
- 当前 auto 任务内容与剩余次数
- 最近 Codex 回复
- 最近控制回执与状态事件

## 本地验证

建议按下面方式本地验收一遍：

1. 启动 manager

```bash
codex --web-console
```

2. 浏览器打开网页并登录

3. 点击“新建标签页”

4. 等实例变成 `空闲`

5. 发送一条 chat 消息

6. 等 turn 完成，确认网页出现回复和回执

7. 切到 `Auto 模式`，提交：

```json
[{"message":"继续","count":2}]
```

8. 确认当前 turn 完成后自动继续

9. 执行中点击 `stop_auto`

10. 确认只在当前 turn 完成后停掉 auto

11. 再新建第二个标签页，确认两个实例互不串线

## 故障排查

### 登录成功但“新建标签页”失败

优先检查：

- `codex --web-console` 这个 manager 是否是用当前 patch 后的 `codex` 启动的
- `bin/codex.real.js` 是否还存在
- `python3` / `python` 是否在 PATH
- `node` 是否是 npm 安装版 `codex` 正在使用的那份运行时

### 网页能看到实例，但发消息失败

先看标签页状态：

- `启动中`：等 helper 完成注册
- `失败`：查看标签页里的错误信息
- `离线` / `已退出`：对应 helper 或 Codex 子进程已经结束

### `/status` 里的 session id 不能用于网页绑定

这是预期行为。网页现在不使用 Codex 原生 session id，也不使用 `codex resume <id>` 去建立控制关系。
网页只使用本地 manager 创建和维护的实例记录。

## 公网部署建议

第一阶段先本地跑通；后续如果要挂到公网，建议至少做到：

- manager 先继续只监听 `127.0.0.1`
- 外层用 Nginx / Caddy 反代公开出去
- 必须启用 HTTPS
- 使用足够强的网页登录密码
- 对登录和写接口做限流
- 反代层明确拦截 `/internal/*`
- 如果做公网暴露，不要把 manager 直接绑定到 `0.0.0.0` 后裸奔
- 建议加一层基础访问控制，例如 VPN、Zero Trust、HTTP Basic Auth、IP 白名单

Nginx / Caddy 反代时，最重要的一条是：

- 不要把 `/internal/register`、`/internal/update`、`/internal/unregister`、`/internal/poll` 暴露给公网

这些接口是给本机 helper 回连 manager 用的。
