# Windows EXE 打包说明

电脑端桌面客户端可以用 PyInstaller 打包为单文件 EXE。

## 已生成文件

当前已生成：

```text
dist/VoiceInput.exe
```

这个文件已经包含：

- Python 运行时
- `desktop_client.py`
- `server.py`
- `static/index.html`
- `desktop_ui/dist/` React 桌面客户端前端
- Python 依赖库

普通用户只需要运行 `VoiceInput.exe`，不需要安装 Python，也不需要使用 `start_desktop_client.bat`。

## 重新打包

如果修改了电脑端代码，重新打包：

```powershell
cd D:\Users\WESTBROOK\Projects\Voice_input\FlowBridge
python -m pip install -r requirements.txt
python -m pip install pyinstaller
npm install --prefix desktop_ui
npm run build --prefix desktop_ui
python -m PyInstaller --clean --noconfirm VoiceInput.spec
```

输出位置：

```text
D:\Users\WESTBROOK\Projects\Voice_input\FlowBridge\dist\VoiceInput.exe
```

## 注意事项

- 第一次运行 EXE 时，Windows 防火墙可能提示是否允许网络访问，需要允许局域网访问。
- 如果要向管理员权限窗口输入，请右键 `VoiceInput.exe`，选择“以管理员身份运行”。
- 当前 EXE 是单文件打包，启动时会先解压到临时目录，因此首次启动可能略慢。
- 当前桌面客户端使用 pywebview 承载 React 前端，EXE 会包含 WebView/GUI 相关依赖，体积会明显大于 Tkinter 版本。后续如果想进一步减小体积，可以用干净的 Python 虚拟环境重新打包。
