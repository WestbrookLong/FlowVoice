# FlowBridge 代码阅读说明

`FlowBridge` 现在只保留手机语音输入到 Windows 当前光标的桥接逻辑。

## 入口

- `start_desktop_client.bat`：双击启动桌面客户端。
- `desktop_client.py`：pywebview 桌面壳，负责生成 token、启动 aiohttp 服务，并向 React 前端暴露控制 API。
- `desktop_ui/`：React + Vite + Tailwind 桌面控制台前端。
- `server.py`：HTTP/WebSocket 服务和 Windows `SendInput` 注入逻辑。
- `static/index.html`：手机网页端输入界面。

## 运行链路

```text
手机网页或 Android App
  -> WebSocket /ws
  -> server.py TextSession
  -> 公共前缀匹配
  -> Windows SendInput
  -> 当前光标处出现文字
```

## 核心逻辑

手机端发送当前输入框的完整文本：

```json
{
  "type": "sync_text",
  "token": "session-token",
  "seq": 1,
  "text": "语音输入结果"
}
```

电脑端 `TextSession.replace()` 会比较上一版文本和新版文本的最长公共前缀：

- 公共前缀不变的部分不处理。
- 旧文本公共前缀之后的内容用 Backspace 删除。
- 新文本公共前缀之后的内容用 Unicode `SendInput` 输入。

这个设计让普通追加保持流式输入，同时能适配手机语音输入法对前文的自动修正。

## 保留接口

- `GET /`：手机网页端。
- `GET /health`：健康检查。
- `GET /ws?token=...`：语音输入 WebSocket。
- `GET /static/...`：静态资源。
