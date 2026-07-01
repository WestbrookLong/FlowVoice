# Flow Voice Android App

这是手机端 Flutter/Dart 客户端，复用电脑端 `FlowBridge/server.py` 的 WebSocket 协议。

## APK

已生成可安装 APK：

```text
FlowVoice.apk
```

原始构建产物：

```text
build/app/outputs/flutter-apk/app-release.apk
```

## 使用

1. 电脑端运行 `../FlowBridge/start_desktop_client.bat`。
2. 点击电脑客户端里的“语音输入”二维码。
3. 手机安装并打开 `FlowVoice.apk`。
4. 在 App 中点击“扫码连接”，扫描电脑端二维码。
5. 点击“连接电脑”，然后在电脑上把光标放到目标输入框。

App 中输入、语音输入、换行和删除都会同步到电脑当前光标处。

## 构建

```powershell
cd D:\Users\WESTBROOK\Projects\Voice_input\mobile_app
flutter pub get
flutter analyze
flutter test
flutter build apk --release
```

Android 已配置：

- `INTERNET` 权限。
- `CAMERA` 权限，用于扫码。
- `usesCleartextTraffic=true`，用于局域网 `ws://` 连接。
