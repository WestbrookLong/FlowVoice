# 手机语音输入到 Windows 光标注入技术架构说明

## 1. 项目目标

本项目实现一个局域网内可用的手机语音输入桥接工具：

- 用户在手机端使用系统输入法或第三方输入法进行语音输入。
- 手机端实时把当前输入框文本发送到电脑端。
- 电脑端把文本流式注入到当前 Windows 光标所在位置。
- 当手机语音输入法在句子结束后自动修正前文时，电脑端也能同步修正已经输入过的内容。
- 电脑端提供桌面客户端，避免每次手动打开命令行。
- 手机端支持扫码连接电脑端，避免手动输入 IP、端口和 token。

核心原则是：**语音识别不在本项目内实现，而是复用手机输入法已有的语音转文字能力；本项目只负责手机文本到电脑光标的实时同步与注入。**

## 2. 整体架构

```text
+----------------------+        WebSocket        +--------------------------+
|                      |  sync_text / reset      |                          |
|  手机端 Flutter App   | ----------------------> |  Windows 桌面客户端       |
|  或手机网页           |                         |  Python aiohttp Server   |
|                      |                         |  SendInput 注入模块       |
+----------------------+                         +--------------------------+
        |                                                   |
        | 手机输入法语音输入                                  | Windows 当前前台应用
        v                                                   v
  TextField / textarea                              当前光标处出现文字
```

主要分为三层：

- **手机输入层**：Flutter App 或网页输入框，承接手机输入法语音转文字结果。
- **局域网通信层**：通过 WebSocket 把文本状态实时发送到电脑端。
- **Windows 注入层**：电脑端使用 Win32 `SendInput` 将文字注入当前光标位置。

## 3. 技术栈

### 电脑端

- **Python 3**
- **aiohttp**：同时提供 HTTP 页面和 WebSocket 服务。
- **ctypes + Win32 SendInput**：调用 Windows 原生输入 API，向当前光标位置注入 Unicode 文本、Enter 和 Backspace。
- **pywebview**：提供 Windows 桌面窗口，承载 React 前端。
- **React + Vite + Tailwind CSS**：实现电脑端连接控制台界面。

相关文件：

- `server.py`：HTTP/WebSocket 服务与 Windows 输入注入核心逻辑。
- `desktop_client.py`：Windows 桌面客户端壳，负责启动服务、暴露 JS API、加载 React 前端。
- `desktop_ui/`：React 桌面客户端前端源码与构建产物。
- `start_desktop_client.bat`：双击启动桌面客户端。

### 手机端

- **Flutter / Dart**
- **dart:io WebSocket**：连接电脑端 WebSocket 服务。
- **mobile_scanner**：扫描电脑端二维码，自动解析连接 URL。
- **Android Camera Permission**：扫码连接需要摄像头权限。

相关文件：

- `../mobile_app/lib/main.dart`：Flutter 手机 App 主逻辑。
- `../mobile_app/android/app/src/main/AndroidManifest.xml`：网络权限与相机权限。

### 备用网页端

- **HTML + CSS + 原生 JavaScript**
- **Browser WebSocket API**

相关文件：

- `static/index.html`

网页端用于快速调试或无需安装 App 的场景。

## 4. 连接与安全模型

电脑端服务启动后会生成一个 session token，并形成连接 URL：

```text
http://<电脑局域网 IP>:8787/?token=<session-token>
```

手机端有两种连接方式：

- 扫描电脑桌面客户端显示的二维码。
- 手动粘贴完整 URL，自动解析 IP、端口和 token。

WebSocket 连接地址为：

```text
ws://<电脑局域网 IP>:8787/ws?token=<session-token>
```

每条 WebSocket 消息也会携带 token。电脑端会同时校验 URL query token 和消息体 token，避免局域网内其他设备随意注入文本。

当前安全边界是局域网 + session token，不涉及公网访问、账号体系或加密传输。该设计适合个人局域网工具，不适合直接暴露到公网。

## 5. WebSocket 协议

### 5.1 同步当前文本

手机端每次输入框内容变化时，发送当前完整文本：

```json
{
  "type": "sync_text",
  "token": "session-token",
  "seq": 12,
  "text": "今天下午三点开会"
}
```

字段说明：

- `type`：消息类型，`sync_text` 表示同步手机端当前完整文本。
- `token`：当前会话 token。
- `seq`：客户端递增序号，便于后续扩展确认、乱序处理和调试。
- `text`：手机输入框当前完整文本。

### 5.2 重置会话

手机端清空输入框时，发送：

```json
{
  "type": "reset_session",
  "token": "session-token",
  "seq": 13
}
```

电脑端收到后只清空内部会话状态，不会删除电脑上已经输入的内容。

### 5.3 兼容旧协议

电脑端仍保留旧版 `ops` 协议支持：

```json
{
  "type": "ops",
  "token": "session-token",
  "seq": 10,
  "ops": [
    { "type": "insert", "text": "文本" },
    { "type": "enter" },
    { "type": "backspace", "count": 1 }
  ]
}
```

当前主路径已经改为 `sync_text`，旧协议主要用于兼容早期网页端或调试脚本。

## 6. 流式注入与语音修正适配逻辑

手机语音输入法常见行为是：

1. 先实时输出一段初步识别文本。
2. 随着用户继续说话或停顿，输入法可能回头修正前文。
3. 最终输入框里的文本才是更准确的结果。

如果电脑端只做简单增量追加，就会出现问题：

```text
手机第一次输出：今天下午三点凯会
电脑已输入：    今天下午三点凯会

手机后续修正：今天下午三点开会
如果只追加，电脑无法自动修正“凯会”为“开会”
```

当前实现使用 **完整文本状态同步 + 公共前缀匹配**。

电脑端维护一个 `TextSession.text`，表示本次手机会话上一版已经注入到电脑的文本。每次收到新的 `sync_text` 后：

1. 计算旧文本和新文本的最长公共前缀长度。
2. 对旧文本中公共前缀之后的尾部执行 Backspace。
3. 输入新文本中公共前缀之后的尾部。
4. 更新会话状态为新文本。

示例 1：普通流式追加

```text
旧文本：今天下午三点开
新文本：今天下午三点开会
公共前缀：今天下午三点开
操作：输入“会”
```

此时没有退格，表现为即时流式输入。

示例 2：语音输入法修正前文

```text
旧文本：今天下午三点凯会
新文本：今天下午三点开会
公共前缀：今天下午三点
操作：Backspace 2 次，再输入“开会”
```

这样电脑端最终会和手机端当前文本保持一致。

示例 3：用户删除尾部

```text
旧文本：今天下午三点开会
新文本：今天下午三点
公共前缀：今天下午三点
操作：Backspace 2 次
```

### 该算法的优点

- 普通新增文本仍然是即时流式输入。
- 能适应手机语音输入法对前文的自动修正。
- 不需要电脑端知道手机输入法的内部组合态。
- 协议简单，手机端只发送当前完整文本。

### 该算法的限制

- 假设电脑光标仍位于本次会话文本末尾。
- 如果用户在电脑端手动移动光标，后续 Backspace 可能作用到错误位置。
- 如果目标应用拦截或限制模拟输入，`SendInput` 可能失败或表现不一致。
- 普通权限进程无法稳定向管理员权限窗口注入；如果目标窗口是管理员权限，桌面客户端也需要以管理员权限运行。

## 7. Windows 输入注入实现

电脑端使用 Win32 `SendInput`。

### 文本输入

对于普通文字，使用 `KEYEVENTF_UNICODE` 发送 UTF-16 code unit：

```text
Unicode 字符 -> UTF-16LE -> SendInput(KEYEVENTF_UNICODE)
```

这样可以直接输入中文，不依赖剪贴板，也不需要切换电脑输入法。

### 换行

手机端文本中出现换行时，电脑端会发送 Enter：

```text
\n -> VK_RETURN
```

### 删除

前缀匹配算法需要删除旧文本尾部时，电脑端发送对应次数的 Backspace：

```text
delete_count -> VK_BACK * delete_count
```

## 8. 当前已实现功能

- 手机端语音输入或键盘输入。
- 输入内容实时注入到 Windows 当前光标处。
- 普通追加保持流式输入，无人为等待。
- 手机语音输入法后续修正前文时，电脑端基于公共前缀匹配同步修正。
- 支持换行映射为电脑端 Enter。
- 支持删除映射为电脑端 Backspace。
- 支持手机网页端快速使用。
- 支持 Flutter Android App。
- 支持电脑端桌面客户端启动服务。
- 支持二维码扫码连接。
- 支持 session token 校验。

## 9. 使用流程

### 电脑端

推荐双击：

```text
start_desktop_client.bat
```

或命令行运行：

```powershell
python desktop_client.py
```

打开桌面客户端后：

1. 点击“启动服务”。
2. 查看或复制连接 URL。
3. 手机 App 扫描窗口中的二维码。

### 手机端

1. 打开 Flutter App。
2. 点击“扫码连接”。
3. 扫描电脑端二维码。
4. 点击“连接电脑”。
5. 把电脑光标放到目标输入位置。
6. 在手机 App 输入框中使用语音输入或键盘输入。

## 10. 后续可扩展方向

- 为手机 App 增加固定电脑历史记录。
- 增加连接状态心跳、断线自动恢复和更明确的错误提示。
- 增加目标窗口锁定，降低电脑端光标移动导致误删的风险。
- 增加 HTTPS/WSS 或局域网设备配对机制，提高安全性。
- 增加剪贴板注入 fallback，提升部分特殊应用中的兼容性。
