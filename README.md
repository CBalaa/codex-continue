# Codex Auto Continue

最小可用示例。

前提：

- 已经准备好 `server/users.local.json`
- 本机可以运行 `python3`
- 本机可以运行 `codex`

## 启动 Server

```bash
./server/start \
  --bind 0.0.0.0 \
  --port 7894 \
  --users-file server/users.local.json \
  --state-file server/state.local.json
```

## 启动 Client

如果 client 和 server 在同一台机器上，可以这样跑：

```bash
./client/start \
  --server-url http://127.0.0.1:7894 \
  --machine-key-file ~/.codex/codex-auto-continue-machine-key.txt \
  --machine-name local-test
```

如果 client 通过公网 IP 连接 server，可以这样跑：

```bash
./client/start \
  --server-url http://101.200.129.93:7894 \
  --machine-key-file ~/.codex/codex-auto-continue-machine-key.txt \
  --machine-name local-test
```

注意：

- `--bind 0.0.0.0` 只是 server 的监听方式，不是 client 要填写的地址
- `--server-url` 必须写真实可访问的地址
- 不要写成 `http://127.0.101.200.129.93:7894/`，这个地址是错的
