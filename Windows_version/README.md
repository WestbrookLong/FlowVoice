# 手机实时输入到 Windows 光标

手机打开电脑提供的网页后，在输入框中打字或使用手机输入法语音输入，电脑会把变化实时写入当前光标位置。

## 使用

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python server.py
```

启动后终端会打印类似地址：

```text
http://192.168.1.20:8787/?token=xxxx
```

手机和电脑连到同一个局域网，用手机浏览器打开该地址。先把电脑光标放到目标输入框，再在手机页面输入即可。

## 行为说明

- 手机端无需点击发送，输入框变化会自动同步。
- 语音输入法如果在句子结束后修正前文，电脑端会从差异位置开始退格并重打一段尾部，普通追加仍是即时流式输入。
- 手机端换行会在电脑端执行 Enter。
- 删除文字会在电脑端发送对应数量的 Backspace。
- 清空手机输入框只会重置手机端会话，不会删除电脑上已经输入的内容。
- 普通权限程序无法稳定向管理员权限窗口输入；如需输入管理员窗口，请用管理员权限运行本服务。

## Flutter App

`../mobile_app/` 中提供了 Flutter/Dart 手机端 App 源码。当前电脑未安装 Flutter/Dart，因此未在本机编译 APK；安装 Flutter 后参考 `../mobile_app/README.md` 运行。

## Windows 桌面客户端

不想每次打开命令行时，可以运行：

```powershell
python desktop_client.py
```

桌面客户端使用 React 前端 + pywebview 桌面窗口，界面会显示手机访问地址、二维码、复制按钮和服务启停按钮。手机 App 或网页版都可以使用这个地址连接。

桌面客户端也会显示连接二维码。新版手机 App 点击“扫码连接”后扫描二维码，会自动填充电脑地址和 Token。

如果要分享给别人使用，推荐直接分享 `dist/VoiceInput.exe`。打包说明见 `PACKAGING.md`。
