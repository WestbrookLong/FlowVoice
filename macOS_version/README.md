# macOS_version 目录说明

`/Users/ayana/Voice_from_Phone/macOS_version/` 现在按功能拆成两个可独立运行的子目录：

## 1. 手机实时输入 / 语音同步

目录：

```text
/Users/ayana/Voice_from_Phone/macOS_version/macOS_voice_input
```

启动：

```bash
cd /Users/ayana/Voice_from_Phone/macOS_version/macOS_voice_input
./start_desktop_client.sh
```

或命令行方式：

```bash
cd /Users/ayana/Voice_from_Phone/macOS_version/macOS_voice_input
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 server.py
```

详细说明见：

```text
/Users/ayana/Voice_from_Phone/macOS_version/macOS_voice_input/README.md
```

## 2. iPad 白板桥接

目录：

```text
/Users/ayana/Voice_from_Phone/macOS_version/macOS_whiteboard
```

启动：

```bash
cd /Users/ayana/Voice_from_Phone/macOS_version/macOS_whiteboard
./start_desktop_client.sh
```

详细说明见：

```text
/Users/ayana/Voice_from_Phone/macOS_version/macOS_whiteboard/README.md
```

## 说明

- 两个目录都包含各自独立运行所需的 `server.py`、`desktop_client.py`、`requirements.txt`、启动脚本和静态资源。
- 两个目录现在都各自包含独立的公网隧道能力；如果要让异地设备访问，请在对应子目录内安装 `cloudflared` 并从各自桌面窗口点击公网按钮。
- 根目录当前仍保留原有语音版文件，避免打断已有使用方式；以后建议优先从上面两个子目录进入。
