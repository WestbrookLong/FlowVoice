import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:mobile_scanner/mobile_scanner.dart';
import 'package:shared_preferences/shared_preferences.dart';

const String _prefFilterPunctuation = 'filterPunctuation';
const String _prefConvertSpokenPunctuation = 'convertSpokenPunctuation';
const String _prefEnableVoiceCommands = 'enableVoiceCommands';
const String _prefPureBlackMode = 'pureBlackMode';

void main() {
  runApp(const FlowVoiceApp());
}

class FlowVoiceApp extends StatelessWidget {
  const FlowVoiceApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Flow Voice',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        useMaterial3: true,
        brightness: Brightness.dark,
        scaffoldBackgroundColor: const Color(0xFF050807),
        colorScheme: ColorScheme.fromSeed(
          seedColor: const Color(0xFF28F58D),
          brightness: Brightness.dark,
        ),
        fontFamily: 'sans',
      ),
      home: const FlowVoicePage(),
    );
  }
}

enum BridgeStatus {
  disconnected,
  connecting,
  connected,
  error,
}

class FlowVoicePage extends StatefulWidget {
  const FlowVoicePage({super.key});

  @override
  State<FlowVoicePage> createState() => _FlowVoicePageState();
}

class _FlowVoicePageState extends State<FlowVoicePage>
    with WidgetsBindingObserver {
  static const MethodChannel _overlayChannel =
      MethodChannel('flowvoice/overlay');

  final TextEditingController _urlController = TextEditingController();
  final TextEditingController _hostController = TextEditingController();
  final TextEditingController _tokenController = TextEditingController();
  final TextEditingController _inputController = TextEditingController();
  final FocusNode _inputFocusNode = FocusNode();

  WebSocket? _socket;
  BridgeStatus _status = BridgeStatus.disconnected;
  String _statusText = '离线';
  String _lastSnapshotKey = '';
  int _seq = 0;
  int _connectionGeneration = 0;
  bool _isConnecting = false;
  bool _filterPunctuation = true;
  bool _convertSpokenPunctuation = true;
  bool _enableVoiceCommands = true;
  bool _pureBlackMode = false;
  bool _settingsOpen = false;
  bool _scannerOpen = false;
  bool _recentlyTyping = false;
  bool _overlayUpdatingInput = false;
  Timer? _reconnectTimer;
  Timer? _typingIdleTimer;
  final List<Map<String, Object?>> _queue = <Map<String, Object?>>[];

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _overlayChannel.setMethodCallHandler(_handleOverlayCall);
    _inputController.addListener(_handleInputChanged);
    _loadPrefs();
    WidgetsBinding.instance
        .addPostFrameCallback((_) => _focusInputSoon(force: true));
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _reconnectTimer?.cancel();
    _typingIdleTimer?.cancel();
    _overlayChannel.setMethodCallHandler(null);
    _stopFloatingInput();
    _socket?.close();
    _urlController.dispose();
    _hostController.dispose();
    _tokenController.dispose();
    _inputController.dispose();
    _inputFocusNode.dispose();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) {
      _stopFloatingInput();
      _focusInputSoon(delay: const Duration(milliseconds: 260), force: true);
    }
  }

  Future<dynamic> _handleOverlayCall(MethodCall call) async {
    if (call.method != 'overlayTextChanged') {
      return null;
    }
    final text = call.arguments is String ? call.arguments as String : '';
    if (_inputController.text == text) {
      return null;
    }
    _overlayUpdatingInput = true;
    _inputController.value = TextEditingValue(
      text: text,
      selection: TextSelection.collapsed(offset: text.length),
    );
    _overlayUpdatingInput = false;
    _markRecentlyTyping();
    _syncInput();
    return null;
  }

  Future<bool> _startFloatingInput() async {
    if (!Platform.isAndroid || _settingsOpen || _scannerOpen) {
      return false;
    }
    try {
      final granted =
          await _overlayChannel.invokeMethod<bool>('hasOverlayPermission') ??
              false;
      if (!granted) {
        await _overlayChannel.invokeMethod<void>('requestOverlayPermission');
        return false;
      }
      final started = await _overlayChannel
          .invokeMethod<bool>('startOverlay', <String, Object?>{
        'text': _inputController.text,
        'connected': _status == BridgeStatus.connected,
      });
      return started ?? false;
    } catch (_) {
      return false;
    }
  }

  Future<void> _openFloatingInput() async {
    final started = await _startFloatingInput();
    if (!started) {
      return;
    }
    try {
      await _overlayChannel.invokeMethod<void>('sendToBackground');
    } catch (_) {
      SystemNavigator.pop();
    }
  }

  Future<void> _stopFloatingInput() async {
    if (!Platform.isAndroid) {
      return;
    }
    try {
      await _overlayChannel.invokeMethod<void>('stopOverlay');
    } catch (_) {
      // Best effort cleanup only.
    }
  }

  Future<void> _loadPrefs() async {
    final prefs = await SharedPreferences.getInstance();
    if (!mounted) {
      return;
    }
    setState(() {
      _filterPunctuation = prefs.getBool(_prefFilterPunctuation) ?? true;
      _convertSpokenPunctuation =
          prefs.getBool(_prefConvertSpokenPunctuation) ?? true;
      _enableVoiceCommands = prefs.getBool(_prefEnableVoiceCommands) ?? true;
      _pureBlackMode = prefs.getBool(_prefPureBlackMode) ?? false;
      if (!_filterPunctuation) {
        _convertSpokenPunctuation = false;
      }
    });
    _syncInput(force: true);
    _focusInputSoon(force: true);
  }

  Future<void> _savePrefs() async {
    final prefs = await SharedPreferences.getInstance();
    await Future.wait(<Future<bool>>[
      prefs.setBool(_prefFilterPunctuation, _filterPunctuation),
      prefs.setBool(_prefConvertSpokenPunctuation, _convertSpokenPunctuation),
      prefs.setBool(_prefEnableVoiceCommands, _enableVoiceCommands),
      prefs.setBool(_prefPureBlackMode, _pureBlackMode),
    ]);
  }

  void _focusInputSoon({
    Duration delay = const Duration(milliseconds: 180),
    bool force = false,
  }) {
    Future<void>.delayed(delay, () {
      if (!mounted || _settingsOpen || _scannerOpen) {
        return;
      }
      if (force) {
        _inputFocusNode.unfocus();
        SystemChannels.textInput.invokeMethod<void>('TextInput.hide');
      }
      FocusScope.of(context).requestFocus(_inputFocusNode);
      Future<void>.delayed(const Duration(milliseconds: 40), () {
        if (!mounted || _settingsOpen || _scannerOpen) {
          return;
        }
        SystemChannels.textInput.invokeMethod<void>('TextInput.show');
      });
    });
  }

  void _handleInputChanged() {
    if (_overlayUpdatingInput) {
      return;
    }
    _markRecentlyTyping();
    _syncInput();
  }

  void _markRecentlyTyping() {
    _typingIdleTimer?.cancel();
    if (!_recentlyTyping && mounted) {
      setState(() {
        _recentlyTyping = true;
      });
    }
    _typingIdleTimer = Timer(const Duration(seconds: 2), () {
      if (!mounted) {
        return;
      }
      setState(() {
        _recentlyTyping = false;
      });
    });
  }

  Uri? get _wsUri {
    final hostText = _hostController.text.trim();
    final token = _tokenController.text.trim();
    if (hostText.isEmpty || token.isEmpty) {
      return null;
    }

    final normalized = hostText
        .replaceFirst(RegExp(r'^https?://'), '')
        .replaceFirst(RegExp(r'^wss?://'), '')
        .split('/')
        .first;
    final parts = normalized.split(':');
    final host = parts.first;
    final port = parts.length > 1 ? int.tryParse(parts.last) ?? 8787 : 8787;

    return Uri(
      scheme: 'ws',
      host: host,
      port: port,
      path: '/ws',
      queryParameters: <String, String>{'token': token},
    );
  }

  Future<void> _scanQrCode() async {
    _scannerOpen = true;
    _inputFocusNode.unfocus();
    final result = await Navigator.of(context).push<String>(
      MaterialPageRoute<String>(builder: (_) => const QrScanPage()),
    );
    _scannerOpen = false;
    if (result == null || result.trim().isEmpty) {
      _focusInputSoon();
      return;
    }
    _urlController.text = result.trim();
    _parseAndFillUrl(result);
    await _connect();
    _focusInputSoon();
  }

  void _parseAndFillUrl(String raw) {
    final value = raw.trim();
    if (value.isEmpty) {
      return;
    }
    final uri = Uri.tryParse(value);
    if (uri == null || uri.host.isEmpty) {
      _setStatus(BridgeStatus.error, 'URL 无效');
      return;
    }

    final port = uri.hasPort ? uri.port : 8787;
    _hostController.text = '${uri.host}:$port';
    final token = uri.queryParameters['token'];
    if (token != null && token.isNotEmpty) {
      _tokenController.text = token;
    }
    _setStatus(BridgeStatus.disconnected, '已读取');
  }

  Future<void> _connect({bool fromRetry = false}) async {
    if (_isConnecting) {
      return;
    }
    final uri = _wsUri;
    if (uri == null) {
      _setStatus(BridgeStatus.error, '请扫码');
      return;
    }

    _reconnectTimer?.cancel();
    _isConnecting = true;
    _setStatus(BridgeStatus.connecting, '连接中');

    try {
      final generation = ++_connectionGeneration;
      final oldSocket = _socket;
      _socket = null;
      await oldSocket?.close();
      final socket = await WebSocket.connect(uri.toString());
      if (generation != _connectionGeneration) {
        await socket.close();
        return;
      }
      _socket = socket;
      _setStatus(BridgeStatus.connected, '在线');
      _flushQueue();
      _syncInput(force: true);
      _focusInputSoon();

      socket.listen(
        _handleSocketMessage,
        onDone: () => _handleSocketClosed(socket, retry: true),
        onError: (_) => _handleSocketClosed(socket, retry: true),
        cancelOnError: true,
      );
    } catch (_) {
      _setStatus(BridgeStatus.error, fromRetry ? '重连失败' : '连接失败');
      _scheduleReconnect();
    } finally {
      _isConnecting = false;
    }
  }

  void _handleSocketMessage(dynamic data) {
    if (data is! String) {
      return;
    }
    try {
      final message = jsonDecode(data);
      if (message is Map && message['type'] == 'error') {
        _setStatus(BridgeStatus.error, '电脑错误');
      }
    } catch (_) {
      // Ignore diagnostics that are not JSON.
    }
  }

  void _handleSocketClosed(WebSocket socket, {required bool retry}) {
    if (!mounted) {
      return;
    }
    if (!identical(socket, _socket)) {
      return;
    }
    _socket = null;
    _setStatus(BridgeStatus.disconnected, '断开');
    if (retry) {
      _scheduleReconnect();
    }
  }

  void _scheduleReconnect() {
    _reconnectTimer?.cancel();
    _reconnectTimer = Timer(const Duration(seconds: 2), () {
      if (mounted) {
        _connect(fromRetry: true);
      }
    });
  }

  void _setStatus(BridgeStatus status, String text) {
    if (!mounted) {
      return;
    }
    setState(() {
      _status = status;
      _statusText = text;
    });
  }

  Map<String, Object?> _settingsPayload() {
    return <String, Object?>{
      'filterPunctuation': _filterPunctuation,
      'convertSpokenPunctuation': _convertSpokenPunctuation,
      'enableVoiceCommands': _enableVoiceCommands,
    };
  }

  void _syncInput({bool force = false}) {
    final message = <String, Object?>{
      'type': 'sync_state',
      'token': _tokenController.text.trim(),
      'seq': ++_seq,
      'text': _inputController.text,
      'settings': _settingsPayload(),
    };
    final snapshotKey = jsonEncode(<String, Object?>{
      'text': message['text'],
      'settings': message['settings'],
    });
    if (!force && snapshotKey == _lastSnapshotKey) {
      return;
    }
    _lastSnapshotKey = snapshotKey;
    _send(message);
  }

  void _send(Map<String, Object?> message) {
    final socket = _socket;
    if (socket == null || socket.readyState != WebSocket.open) {
      _queue.add(message);
      if (_status != BridgeStatus.connected &&
          _status != BridgeStatus.connecting) {
        _setStatus(BridgeStatus.error, '离线 ${_queue.length}');
      }
      return;
    }
    socket.add(jsonEncode(message));
  }

  void _flushQueue() {
    final pending = List<Map<String, Object?>>.from(_queue);
    _queue.clear();
    for (final message in pending) {
      _send(message);
    }
  }

  void _openSettings() {
    _settingsOpen = true;
    _inputFocusNode.unfocus();
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      showDragHandle: false,
      backgroundColor: Colors.transparent,
      builder: (context) {
        return StatefulBuilder(
          builder: (context, setSheetState) {
            void update(VoidCallback fn) {
              setState(fn);
              setSheetState(() {});
              _savePrefs();
              _syncInput(force: true);
              if (_pureBlackMode) {
                _focusInputSoon(force: true);
              }
            }

            return SafeArea(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: _SettingsSheetV2(
                  filterPunctuation: _filterPunctuation,
                  convertSpokenPunctuation: _convertSpokenPunctuation,
                  enableVoiceCommands: _enableVoiceCommands,
                  pureBlackMode: _pureBlackMode,
                  onFilterChanged: (value) => update(() {
                    _filterPunctuation = value;
                    if (!value) {
                      _convertSpokenPunctuation = false;
                    }
                  }),
                  onConvertChanged: _filterPunctuation
                      ? (value) =>
                          update(() => _convertSpokenPunctuation = value)
                      : null,
                  onCommandChanged: (value) =>
                      update(() => _enableVoiceCommands = value),
                  onPureBlackChanged: (value) =>
                      update(() => _pureBlackMode = value),
                  onClose: () => Navigator.of(context).pop(),
                ),
              ),
            );
          },
        );
      },
    ).whenComplete(() {
      _settingsOpen = false;
      _focusInputSoon(force: _pureBlackMode);
    });
  }

  @override
  Widget build(BuildContext context) {
    if (_pureBlackMode) {
      return Scaffold(
        backgroundColor: Colors.black,
        body: SafeArea(
          child: Listener(
            behavior: HitTestBehavior.opaque,
            onPointerDown: (_) => _focusInputSoon(force: true),
            child: Stack(
              children: <Widget>[
                const Positioned.fill(child: ColoredBox(color: Colors.black)),
                Positioned(
                  top: 18,
                  right: 18,
                  child: _BlackModeSettingsButton(onPressed: _openSettings),
                ),
                Positioned(
                  left: 1,
                  bottom: 1,
                  child: _HiddenVoiceInput(
                    controller: _inputController,
                    focusNode: _inputFocusNode,
                  ),
                ),
              ],
            ),
          ),
        ),
      );
    }

    return Scaffold(
      body: SafeArea(
        child: GestureDetector(
          behavior: HitTestBehavior.opaque,
          onTap: _focusInputSoon,
          child: _VoiceBackground(
            child: Stack(
              children: <Widget>[
                Positioned(
                  left: 18,
                  right: 18,
                  top: 22,
                  child: _Header(
                    status: _status,
                    statusText: _statusText,
                    settingsActive: _filterPunctuation ||
                        _convertSpokenPunctuation ||
                        _enableVoiceCommands,
                    onSettings: _openSettings,
                    onFloatingInput: _openFloatingInput,
                    onScan: _scanQrCode,
                  ),
                ),
                Positioned.fill(
                  top: 92,
                  child: Center(
                    child: _SceneStage(
                      working: _recentlyTyping,
                      controller: _inputController,
                    ),
                  ),
                ),
                Positioned(
                  left: 1,
                  bottom: 1,
                  child: _HiddenVoiceInput(
                    controller: _inputController,
                    focusNode: _inputFocusNode,
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _VoiceBackground extends StatelessWidget {
  const _VoiceBackground({required this.child});

  final Widget child;

  @override
  Widget build(BuildContext context) {
    return DecoratedBox(
      decoration: const BoxDecoration(color: Color(0xFFFDFDFB)),
      child: Stack(
        fit: StackFit.expand,
        children: <Widget>[
          const _PixelStar(left: 0.07, top: 0.08, size: 18),
          const _PixelStar(left: 0.22, top: 0.19, size: 22),
          const _PixelStar(left: 0.37, top: 0.14, size: 14),
          const _PixelStar(left: 0.82, top: 0.25, size: 22),
          const _PixelStar(left: 0.12, top: 0.43, size: 18),
          const _PixelStar(left: 0.67, top: 0.74, size: 20),
          const _PixelStar(left: 0.80, top: 0.86, size: 22),
          const _PixelDot(left: 0.11, top: 0.31),
          const _PixelDot(left: 0.89, top: 0.39),
          const _PixelDot(left: 0.41, top: 0.81),
          const _PixelDot(left: 0.28, top: 0.89),
          child,
        ],
      ),
    );
  }
}

class _PixelStar extends StatelessWidget {
  const _PixelStar({
    required this.left,
    required this.top,
    required this.size,
  });

  final double left;
  final double top;
  final double size;

  @override
  Widget build(BuildContext context) {
    return Positioned.fill(
      child: FractionallySizedBox(
        alignment: Alignment(left * 2 - 1, top * 2 - 1),
        widthFactor: 0,
        heightFactor: 0,
        child: CustomPaint(
          size: Size.square(size),
          painter: const _PixelStarPainter(),
        ),
      ),
    );
  }
}

class _PixelDot extends StatelessWidget {
  const _PixelDot({
    required this.left,
    required this.top,
  });

  final double left;
  final double top;

  @override
  Widget build(BuildContext context) {
    return Positioned.fill(
      child: FractionallySizedBox(
        alignment: Alignment(left * 2 - 1, top * 2 - 1),
        widthFactor: 0,
        heightFactor: 0,
        child: Container(
          width: 8,
          height: 8,
          color: const Color(0xFFC9C9C9),
        ),
      ),
    );
  }
}

class _PixelStarPainter extends CustomPainter {
  const _PixelStarPainter();

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()..color = const Color(0xFFC7C7C7);
    final unit = size.width / 5;
    final blocks = <Offset>[
      const Offset(2, 0),
      const Offset(2, 1),
      const Offset(0, 2),
      const Offset(1, 2),
      const Offset(2, 2),
      const Offset(3, 2),
      const Offset(4, 2),
      const Offset(2, 3),
      const Offset(2, 4),
    ];
    for (final block in blocks) {
      canvas.drawRect(
        Rect.fromLTWH(block.dx * unit, block.dy * unit, unit, unit),
        paint,
      );
    }
  }

  @override
  bool shouldRepaint(covariant _PixelStarPainter oldDelegate) => false;
}

class _Header extends StatelessWidget {
  const _Header({
    required this.status,
    required this.statusText,
    required this.settingsActive,
    required this.onSettings,
    required this.onFloatingInput,
    required this.onScan,
  });

  final BridgeStatus status;
  final String statusText;
  final bool settingsActive;
  final VoidCallback onSettings;
  final VoidCallback onFloatingInput;
  final VoidCallback onScan;

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisAlignment: MainAxisAlignment.end,
      children: <Widget>[
        _StatusPill(status: status, text: statusText),
        const SizedBox(width: 16),
        _RoundIconButton(
          icon: Icons.settings,
          active: settingsActive,
          onPressed: onSettings,
        ),
        const SizedBox(width: 16),
        _RoundIconButton(
          icon: Icons.picture_in_picture_alt,
          active: false,
          onPressed: onFloatingInput,
        ),
        const SizedBox(width: 16),
        _RoundIconButton(
          icon: Icons.qr_code_scanner,
          active: false,
          onPressed: onScan,
        ),
      ],
    );
  }
}

class _StatusPill extends StatelessWidget {
  const _StatusPill({
    required this.status,
    required this.text,
  });

  final BridgeStatus status;
  final String text;

  @override
  Widget build(BuildContext context) {
    final color = switch (status) {
      BridgeStatus.connected => const Color(0xFF28D85F),
      BridgeStatus.connecting => const Color(0xFFB8B8B8),
      BridgeStatus.disconnected ||
      BridgeStatus.error =>
        const Color(0xFFE1513F),
    };
    return Semantics(
      label: '连接状态：$text',
      button: false,
      child: _PixelButtonFrame(
        child: Center(
          child: Container(
            width: 18,
            height: 18,
            decoration: BoxDecoration(
              color: color,
              shape: BoxShape.circle,
              boxShadow: <BoxShadow>[
                BoxShadow(color: color.withValues(alpha: 0.3), blurRadius: 8),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _RoundIconButton extends StatelessWidget {
  const _RoundIconButton({
    required this.icon,
    required this.active,
    required this.onPressed,
  });

  final IconData icon;
  final bool active;
  final VoidCallback onPressed;

  @override
  Widget build(BuildContext context) {
    return _PixelButtonFrame(
      active: active,
      onTap: onPressed,
      child: Icon(
        icon,
        size: 25,
        color: const Color(0xFF050505),
      ),
    );
  }
}

class _BlackModeSettingsButton extends StatelessWidget {
  const _BlackModeSettingsButton({required this.onPressed});

  final VoidCallback onPressed;

  @override
  Widget build(BuildContext context) {
    return Semantics(
      label: '打开设置',
      button: true,
      child: Material(
        color: Colors.transparent,
        child: InkWell(
          onTap: onPressed,
          borderRadius: BorderRadius.circular(12),
          child: Container(
            width: 42,
            height: 42,
            decoration: BoxDecoration(
              color: const Color(0xFF050505),
              border: Border.all(color: const Color(0xFF1C1C1C), width: 1.5),
              borderRadius: BorderRadius.circular(12),
            ),
            child: const Icon(
              Icons.settings,
              color: Color(0xFF2B2B2B),
              size: 21,
            ),
          ),
        ),
      ),
    );
  }
}

class _PixelButtonFrame extends StatelessWidget {
  const _PixelButtonFrame({
    required this.child,
    this.active = false,
    this.onTap,
  });

  final Widget child;
  final bool active;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: Colors.transparent,
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(15),
        child: Container(
          width: 54,
          height: 54,
          decoration: BoxDecoration(
            color: active ? const Color(0xFFF4FFF8) : Colors.white,
            border: Border.all(color: const Color(0xFF111111), width: 2.5),
            borderRadius: BorderRadius.circular(15),
            boxShadow: const <BoxShadow>[
              BoxShadow(
                color: Color(0xFF111111),
                offset: Offset(3, 3),
                blurRadius: 0,
              ),
            ],
          ),
          child: child,
        ),
      ),
    );
  }
}

class _SceneStage extends StatelessWidget {
  const _SceneStage({
    required this.working,
    required this.controller,
  });

  final bool working;
  final TextEditingController controller;

  @override
  Widget build(BuildContext context) {
    final image = working
        ? 'assets/flowvoice_working_scene.png'
        : 'assets/flowvoice_idle_scene.png';
    return LayoutBuilder(
      builder: (context, constraints) {
        final width = constraints.maxWidth.clamp(280.0, 820.0);
        return SizedBox(
          width: width,
          child: AspectRatio(
            aspectRatio: 820 / 680,
            child: Stack(
              fit: StackFit.expand,
              children: <Widget>[
                AnimatedSwitcher(
                  duration: const Duration(milliseconds: 180),
                  child: Image.asset(
                    image,
                    key: ValueKey<String>(image),
                    fit: BoxFit.contain,
                    filterQuality: FilterQuality.none,
                  ),
                ),
                Positioned(
                  left: width * 0.39,
                  top: width / (820 / 680) * 0.145,
                  width: width * 0.34,
                  height: width / (820 / 680) * 0.25,
                  child: _MonitorText(controller: controller),
                ),
              ],
            ),
          ),
        );
      },
    );
  }
}

class _MonitorText extends StatelessWidget {
  const _MonitorText({required this.controller});

  final TextEditingController controller;

  String _tailText(String text) {
    final trimmed = text.trim();
    if (trimmed.length <= 24) {
      return trimmed;
    }
    return trimmed.substring(trimmed.length - 24);
  }

  @override
  Widget build(BuildContext context) {
    return ValueListenableBuilder<TextEditingValue>(
      valueListenable: controller,
      builder: (context, value, _) {
        final text = _tailText(value.text);
        if (text.isEmpty) {
          return const SizedBox.shrink();
        }
        return Padding(
          padding: const EdgeInsets.all(8),
          child: Align(
            alignment: Alignment.topLeft,
            child: Text(
              text,
              maxLines: 3,
              overflow: TextOverflow.fade,
              style: const TextStyle(
                color: Color(0xFFE7F3EA),
                fontSize: 10.5,
                height: 1.28,
                fontWeight: FontWeight.w600,
              ),
            ),
          ),
        );
      },
    );
  }
}

class _HiddenVoiceInput extends StatelessWidget {
  const _HiddenVoiceInput({
    required this.controller,
    required this.focusNode,
  });

  final TextEditingController controller;
  final FocusNode focusNode;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: 1,
      height: 1,
      child: Opacity(
        opacity: 0.01,
        child: TextField(
          controller: controller,
          focusNode: focusNode,
          keyboardType: TextInputType.multiline,
          textInputAction: TextInputAction.newline,
          autocorrect: true,
          enableSuggestions: true,
          maxLines: null,
          decoration: const InputDecoration(
            border: InputBorder.none,
            isCollapsed: true,
          ),
        ),
      ),
    );
  }
}

class _VoiceButton extends StatelessWidget {
  const _VoiceButton({
    required this.label,
    required this.onPressed,
  });

  final String label;
  final VoidCallback onPressed;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: 54,
      child: OutlinedButton(
        onPressed: onPressed,
        style: OutlinedButton.styleFrom(
          foregroundColor: const Color(0xFFDDE7DF),
          side: const BorderSide(color: Color(0x1F28F58D)),
          backgroundColor: const Color(0xC208100D),
          textStyle: const TextStyle(
            fontSize: 16,
            fontWeight: FontWeight.w900,
          ),
          shape:
              RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
        ),
        child: Text(label),
      ),
    );
  }
}

class _SettingsSheetV2 extends StatelessWidget {
  const _SettingsSheetV2({
    required this.filterPunctuation,
    required this.convertSpokenPunctuation,
    required this.enableVoiceCommands,
    required this.pureBlackMode,
    required this.onFilterChanged,
    required this.onConvertChanged,
    required this.onCommandChanged,
    required this.onPureBlackChanged,
    required this.onClose,
  });

  final bool filterPunctuation;
  final bool convertSpokenPunctuation;
  final bool enableVoiceCommands;
  final bool pureBlackMode;
  final ValueChanged<bool> onFilterChanged;
  final ValueChanged<bool>? onConvertChanged;
  final ValueChanged<bool> onCommandChanged;
  final ValueChanged<bool> onPureBlackChanged;
  final VoidCallback onClose;

  @override
  Widget build(BuildContext context) {
    return ConstrainedBox(
      constraints: BoxConstraints(
        maxHeight: MediaQuery.sizeOf(context).height * 0.82,
      ),
      child: Container(
        padding: const EdgeInsets.all(18),
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(26),
          border: Border.all(color: const Color(0x3828F58D)),
          gradient: const LinearGradient(
            begin: Alignment.topLeft,
            end: Alignment.bottomRight,
            colors: <Color>[
              Color(0xFF08100D),
              Color(0xFF0B1D14),
            ],
          ),
          boxShadow: const <BoxShadow>[
            BoxShadow(
              color: Color(0x8C000000),
              blurRadius: 90,
              offset: Offset(0, 30),
            ),
          ],
        ),
        child: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: <Widget>[
              const Text(
                '设置',
                style: TextStyle(
                  color: Color(0xFFF0FFF5),
                  fontSize: 22,
                  fontWeight: FontWeight.w800,
                ),
              ),
              const SizedBox(height: 12),
              _SettingSwitch(
                title: '标点过滤',
                description: '开启后，电脑端只接收过滤真实标点后的文本；手机输入框内容保持原样。',
                value: filterPunctuation,
                onChanged: onFilterChanged,
              ),
              const SizedBox(height: 12),
              Padding(
                padding: const EdgeInsets.only(left: 12),
                child: _SettingSwitch(
                  title: '口述标点转换',
                  description: '把“逗号、句号、问号”等文字命令转换为真实标点，并按上下文选择样式。',
                  value: convertSpokenPunctuation,
                  onChanged: onConvertChanged,
                ),
              ),
              const SizedBox(height: 12),
              _SettingSwitch(
                title: '英文语音命令',
                description:
                    '支持 enter、back、backspace / back space、delete all。命令大小写不敏感。',
                value: enableVoiceCommands,
                onChanged: onCommandChanged,
              ),
              const SizedBox(height: 12),
              _SettingSwitch(
                title: '纯黑屏模式',
                description: '开启后主界面变为纯黑屏，点击屏幕会持续唤起输入法；通过右上角设置关闭后恢复当前界面。',
                value: pureBlackMode,
                onChanged: onPureBlackChanged,
              ),
              const SizedBox(height: 12),
              _VoiceButton(label: '完成', onPressed: onClose),
            ],
          ),
        ),
      ),
    );
  }
}

// ignore: unused_element
class _SettingsSheet extends StatelessWidget {
  const _SettingsSheet({
    required this.filterPunctuation,
    required this.convertSpokenPunctuation,
    required this.enableVoiceCommands,
    required this.pureBlackMode,
    required this.onFilterChanged,
    required this.onConvertChanged,
    required this.onCommandChanged,
    required this.onPureBlackChanged,
    required this.onClose,
  });

  final bool filterPunctuation;
  final bool convertSpokenPunctuation;
  final bool enableVoiceCommands;
  final bool pureBlackMode;
  final ValueChanged<bool> onFilterChanged;
  final ValueChanged<bool>? onConvertChanged;
  final ValueChanged<bool> onCommandChanged;
  final ValueChanged<bool> onPureBlackChanged;
  final VoidCallback onClose;

  @override
  Widget build(BuildContext context) {
    return ConstrainedBox(
      constraints: BoxConstraints(
        maxHeight: MediaQuery.sizeOf(context).height * 0.82,
      ),
      child: Container(
        padding: const EdgeInsets.all(18),
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(26),
          border: Border.all(color: const Color(0x3828F58D)),
          gradient: const LinearGradient(
            begin: Alignment.topLeft,
            end: Alignment.bottomRight,
            colors: <Color>[
              Color(0xFF08100D),
              Color(0xFF0B1D14),
            ],
          ),
          boxShadow: const <BoxShadow>[
            BoxShadow(
              color: Color(0x8C000000),
              blurRadius: 90,
              offset: Offset(0, 30),
            ),
          ],
        ),
        child: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: <Widget>[
              const Text(
                '设置',
                style: TextStyle(
                  color: Color(0xFFF0FFF5),
                  fontSize: 22,
                  fontWeight: FontWeight.w800,
                ),
              ),
              const SizedBox(height: 12),
              _SettingSwitch(
                title: '标点过滤',
                description: '开启后，电脑端只接收过滤真实标点后的文本；手机输入框内容保持原样。',
                value: filterPunctuation,
                onChanged: onFilterChanged,
              ),
              const SizedBox(height: 12),
              Padding(
                padding: const EdgeInsets.only(left: 12),
                child: _SettingSwitch(
                  title: '口述标点转换',
                  description: '把“逗号、句号、问号”等文字命令转换为真实标点，并按上下文选择样式。',
                  value: convertSpokenPunctuation,
                  onChanged: onConvertChanged,
                ),
              ),
              const SizedBox(height: 12),
              _SettingSwitch(
                title: '英文语音命令',
                description:
                    '支持 enter、back、backspace / back space、delete all。命令大小写不敏感。',
                value: enableVoiceCommands,
                onChanged: onCommandChanged,
              ),
              const SizedBox(height: 12),
              _VoiceButton(label: '完成', onPressed: onClose),
            ],
          ),
        ),
      ),
    );
  }
}

class _SettingSwitch extends StatelessWidget {
  const _SettingSwitch({
    required this.title,
    required this.description,
    required this.value,
    required this.onChanged,
  });

  final String title;
  final String description;
  final bool value;
  final ValueChanged<bool>? onChanged;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: const Color(0xC706100B),
        border: Border.all(color: const Color(0x1F28F58D)),
        borderRadius: BorderRadius.circular(20),
      ),
      child: Row(
        children: <Widget>[
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text(
                  title,
                  style: TextStyle(
                    color: onChanged == null
                        ? const Color(0x805B7062)
                        : const Color(0xFFDDE7DF),
                    fontSize: 16,
                    fontWeight: FontWeight.w800,
                  ),
                ),
                const SizedBox(height: 6),
                Text(
                  description,
                  style: TextStyle(
                    color: onChanged == null
                        ? const Color(0x705B7062)
                        : const Color(0xFF5B7062),
                    fontSize: 12,
                    height: 1.45,
                    fontWeight: FontWeight.w500,
                  ),
                ),
              ],
            ),
          ),
          Switch(
            value: value,
            onChanged: onChanged,
            activeThumbColor: const Color(0xFF28F58D),
            inactiveThumbColor: const Color(0xFF8EA99A),
            inactiveTrackColor: const Color(0x385B7062),
          ),
        ],
      ),
    );
  }
}

class QrScanPage extends StatefulWidget {
  const QrScanPage({super.key});

  @override
  State<QrScanPage> createState() => _QrScanPageState();
}

class _QrScanPageState extends State<QrScanPage> {
  bool _handled = false;

  void _handleDetect(BarcodeCapture capture) {
    if (_handled) {
      return;
    }

    for (final barcode in capture.barcodes) {
      final value = barcode.rawValue;
      if (value == null || value.trim().isEmpty) {
        continue;
      }
      _handled = true;
      Navigator.of(context).pop(value.trim());
      return;
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.black,
      appBar: AppBar(
        title: const Text('扫码连接'),
        backgroundColor: Colors.black,
        foregroundColor: Colors.white,
      ),
      body: Stack(
        fit: StackFit.expand,
        children: <Widget>[
          MobileScanner(onDetect: _handleDetect),
          Center(
            child: Container(
              width: 246,
              height: 246,
              decoration: BoxDecoration(
                border: Border.all(color: const Color(0xFF28F58D), width: 3),
                borderRadius: BorderRadius.circular(24),
              ),
            ),
          ),
          const Positioned(
            left: 24,
            right: 24,
            bottom: 42,
            child: Text(
              '扫描电脑端二维码',
              textAlign: TextAlign.center,
              style: TextStyle(
                color: Colors.white,
                fontSize: 16,
                fontWeight: FontWeight.w800,
              ),
            ),
          ),
        ],
      ),
    );
  }
}
