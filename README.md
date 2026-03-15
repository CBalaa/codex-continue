# Codex Auto-Continue Web Bundle

这个仓库现在只做一件事：给原生 `codex` 提供一套网页控制台链路。

包括：

- 中心网页服务器
- 本地 agent
- 网页里新建的 Codex 实例

不包括：

- 接管本地 `codex` 命令
- 给本地 `codex` 再包一层外部 wrapper
- 修改 native Codex 安装目录

所以本地终端里的 `codex` 必须保持原生，下面这些旧入口都不再使用：

- `codex --chat-mode`
- `codex --auto-mode`
- `codex --native`
- `codex --web-console`
- `codex --web-console-agent`

`.reference/` 只用于参考，不参与运行。

## 核心结论

- 本地直接运行 `codex`，行为应该和原版完全一致。
- 从仓库目录启动时，直接用 `./server/start` 和 `./client/start`。
- 仓库根目录的 `npm run server` / `npm run client` 也只是执行当前目录下这两个脚本。
- 内部链路仍通过 `client/codex-auto-continue-launch` 串起来。
- `codex-auto-continue-pty.py` 在最后一步会直接执行系统里的原生 `codex`。
- 只要 `codex` 在 PATH 中可执行，网页链路就能启动实例。

## 最短用法

如果你就在仓库目录里，直接运行下面两条命令。

第一个终端启动网页服务器：

```bash
./server/start \
  --bind 127.0.0.1 \
  --port 8765 \
  --password change-me-now
```

第二个终端启动本地 agent：

```bash
./client/start \
  --server-url http://127.0.0.1:8765 \
  --machine-key-file ~/.codex/codex-auto-continue-machine-key.txt \
  --machine-name local-test
```

然后：

1. 打开 `http://127.0.0.1:8765/`
2. 输入网页密码 `change-me-now`
3. 输入 agent 启动时打印出来的 machine key
4. 点击“新建标签页”
5. 在网页里发送 chat / auto / stop_auto

## 运行链路

运行时的真实链路是：

1. 你手工执行 `./server/start` 或 `./client/start`
2. `./server/start` 启动中心网页服务器
3. `./client/start` 启动本地 agent
4. agent 在本地拉起一个 manager
5. 网页点击“新建标签页”后，manager 再调用 `client/codex-auto-continue-launch`
6. `codex-auto-continue-pty.py` 最终执行原生 `codex`

## 前提

需要满足：

- 原生 `codex` 已经正常安装
- 在 shell 里直接执行 `codex` 能正常工作
- Python 3 可用

建议先自检：

```bash
codex --version
python3 --version
```

## 常用启动方式

下面默认你就在仓库根目录里。

### 启动网页服务器

```bash
./server/start \
  --bind 127.0.0.1 \
  --port 8765 \
  --password change-me-now
```

参数说明：

- `--bind`：网页服务器监听地址
- `--port`：网页服务器端口
- `--password`：网页登录密码

### 启动本地 agent

```bash
./client/start \
  --server-url http://127.0.0.1:8765 \
  --machine-key-file ~/.codex/codex-auto-continue-machine-key.txt \
  --machine-name local-test
```

参数说明：

- `--server-url`：agent 要连接的网页服务器地址
- `--machine-key-file`：本地机器 key 保存路径
- `--machine-name`：网页里显示的机器名

agent 启动后会打印：

- remote server
- machine name
- machine key

也可以通过 npm 执行当前目录脚本：

```bash
npm run server -- --bind 127.0.0.1 --port 8765 --password change-me-now
npm run client -- --server-url http://127.0.0.1:8765 --machine-key-file ~/.codex/codex-auto-continue-machine-key.txt --machine-name local-test
```

## 给网页实例透传原生 codex 参数

`./client/start` 后面可以通过 `--` 继续附加参数，这些参数会透传给网页里后续创建的 Codex 实例。

例如：

```bash
./client/start \
  --server-url http://127.0.0.1:8765 \
  --machine-key-file ~/.codex/codex-auto-continue-machine-key.txt \
  --machine-name local-test \
  -- \
  -m gpt-5 \
  -C /path/to/workspace
```

这样网页里新建出来的实例都会带上这些参数。

## 网页里的使用流程

1. 浏览器打开 `http://127.0.0.1:8765/`
2. 输入网页登录密码
3. 输入 machine key，连接目标 agent
4. 点击“新建标签页”
5. 等待实例从 `starting` 变成 `idle`
6. 在网页里发送 chat / auto / stop_auto

## Launch Script 契约

内部统一脚本 `client/codex-auto-continue-launch` 支持 3 个 mode。

网页服务器：

```bash
client/codex-auto-continue-launch server \
  --bind <bind> \
  --port <port> \
  --password <password>
```

本地 agent：

```bash
client/codex-auto-continue-launch agent \
  --server-url <url> \
  --machine-key-file <path> \
  [--machine-name <name>] \
  -- \
  <extra codex args...>
```

网页里的 Codex 实例：

```bash
client/codex-auto-continue-launch codex \
  --mode <chat|auto> \
  --prompt <text> \
  [--limit <n>] \
  --web-bind <bind> \
  --web-port <port> \
  --web-password <password> \
  [--instance-id <id>] \
  -- \
  <extra codex args...>
```

最后这一种通常不需要手工运行，它是给网页服务器和 agent 内部调用的。

## 目录

- `server/start`：中心网页服务器启动脚本
- `server/codex-auto-continue-remote-server.py`：中心网页服务器
- `client/start`：本地 agent 启动脚本
- `client/codex-auto-continue-launch`：内部统一入口脚本
- `client/codex-auto-continue-agent.py`：本地 agent
- `client/codex-auto-continue-web-server.py`：本地 manager
- `client/codex-auto-continue-pty.py`：PTY bridge
- `client/codex-auto-continue-web.html` / `.css` / `.js`：网页前端静态资源
