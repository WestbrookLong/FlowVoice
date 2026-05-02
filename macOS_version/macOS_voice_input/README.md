# 手机实时输入到 macOS 光标

这是独立的 macOS 语音/文字输入版本，放在 `macOS_version/macOS_voice_input/` 下运行，不依赖白板功能目录，也不依赖项目根目录中的 Windows 版本文件。

## 安装

```bash
cd /Users/ayana/Voice_from_Phone/macOS_version/macOS_voice_input
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 命令行运行

```bash
cd /Users/ayana/Voice_from_Phone/macOS_version/macOS_voice_input
python3 server.py
```

启动后终端会打印类似地址：

```text
http://192.168.1.20:8787/?token=xxxx
```

手机和 Mac 连接同一个局域网，用手机浏览器打开该地址。先把 Mac 光标放到目标输入框，再在手机页面输入或使用手机语音输入法。

## 桌面客户端

```bash
cd /Users/ayana/Voice_from_Phone/macOS_version/macOS_voice_input
python3 desktop_client.py
```

也可以运行：

```bash
cd /Users/ayana/Voice_from_Phone/macOS_version/macOS_voice_input
./start_desktop_client.sh
```

桌面客户端现在使用和 `FlowBridge` 相同的控制台式 UI：在一个深色窗口里集中显示服务状态、局域网地址、公网地址、Token、二维码和启动/停止按钮。手机 App 可以扫码连接，手机浏览器也可以直接打开窗口里显示的 URL。

## 公网连接

桌面客户端提供 `Start Public` 按钮，会使用 Cloudflare Tunnel 把本机 `127.0.0.1:8787` 暴露成临时公网 HTTPS 地址。

先安装：

```bash
brew install cloudflared
```

或保证 `cloudflared` 已经在 `PATH` 中。程序默认也会尝试查找：

- `/opt/homebrew/bin/cloudflared`
- `/usr/local/bin/cloudflared`

公网连接成功后，窗口会显示一条新的公网 URL，仍然带 `token` 参数。手机不和 Mac 在同一局域网时，可以直接打开这条公网 URL。

注意：

- 这是临时 `trycloudflare.com` 地址，不是固定域名。
- 当前安全边界是 `Cloudflare Tunnel + session token`，不要把链接发给不信任的人。
- 如果关闭桌面客户端或点击 `Stop Public`，公网地址立即失效。

## macOS 权限

macOS 可能会拦截模拟键盘输入。若手机端已连接但 Mac 没有输入文字，请到：

```text
系统设置 -> 隐私与安全性 -> 辅助功能
```

给 Terminal、iTerm、Python 或正在运行脚本的应用开启权限。必要时也在：

```text
系统设置 -> 隐私与安全性 -> 输入监控
```

开启对应权限。修改权限后通常需要重启终端或重新运行 Python。

## 测试输入

```bash
python3 server.py --test-text "你好 macOS"
```

运行后 3 秒内把光标放到任意文本框。如果权限正确，文本框会自动输入测试文字。

## 行为说明

- 手机端无需点击发送，输入框变化会自动同步。
- 手机语音输入法如果修正前文，Mac 端会从差异位置开始删除并重打一段尾部。
- 手机端换行会在 Mac 端执行 Return。
- 删除文字会在 Mac 端发送对应数量的 Delete/Backspace。
- 清空手机输入框只会重置本次会话状态，不会删除 Mac 上已经输入的内容。
- 当前安全边界默认是局域网 + session token；如果启用公网，则变为 Cloudflare Tunnel + session token。

## 目录边界

- `macOS_voice_input/`：手机实时输入/语音输入同步到 Mac 光标。
- `macOS_whiteboard/`：iPad 白板桥接到 Mac 当前前台绘图目标。

白板功能说明见：

```text
/Users/ayana/Voice_from_Phone/macOS_version/macOS_whiteboard/README.md
```
