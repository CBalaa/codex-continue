# Codex Auto-Continue Patch

这套文件是给你本机 npm 安装版 `codex` 打补丁用的，目标是给新的交互式
`codex` 启动增加一个选项：

- `1) Normal`
- `2) Auto-continue after each completed turn`

开启后，每次 turn 完成，wrapper 会自动把续跑 prompt 输入到 TUI，然后发
`Tab` 让 Codex 排队/提交；如果你手动按裸 `Esc` 或 `Ctrl+C`，本场会话会关闭
auto-continue。

## 目录

- `./install_codex_auto_continue_patch.py`：安装脚本
- `./uninstall_codex_auto_continue_patch.py`：卸载脚本
- `./codex_npm_auto_continue/codex-wrapper.js`：替换 npm `codex.js` 的 wrapper
- `./codex_npm_auto_continue/codex-auto-continue-pty.py`：PTY 注入与自动续跑逻辑
- `./codex_npm_auto_continue/codex-auto-continue-notify.py`：接收 turn 完成通知

## 安装

默认 patch 当前 PATH 里的 `codex`：

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
- 把 wrapper 和 helper 文件复制到同一个 `bin/` 目录

## 卸载

恢复当前 PATH 里的 `codex`：

```bash
python3 uninstall_codex_auto_continue_patch.py
```

如果想保留 `bin/codex.real.js` 备份：

```bash
python3 uninstall_codex_auto_continue_patch.py --keep-backup
```

## 使用

直接启动：

```bash
codex
```

会出现启动模式选择。也可以显式指定：

```bash
codex --auto-continue
```

自定义自动发送的 prompt：

```bash
codex --auto-continue --auto-continue-prompt "继续"
```

## 调试

打开调试输出：

```bash
CODEX_AUTO_CONTINUE_DEBUG=1 codex
```

日志默认也会写到：

```bash
/tmp/codex-auto-continue-debug.log
```

## 说明

- 这是“本机 npm 包补丁”，不是重编译 Codex。
- 目前面向类 Unix 环境，依赖 `python3` 或 `python`。
- 注入的是本次启动专用的 `notify` override，不会改写你的 `~/.codex/config.toml`。
