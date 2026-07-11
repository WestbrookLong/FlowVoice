import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:mobile_scanner/mobile_scanner.dart';
import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:sqflite/sqflite.dart';

const String _prefFilterPunctuation = 'filterPunctuation';
const String _prefConvertSpokenPunctuation = 'convertSpokenPunctuation';
const String _prefEnableVoiceCommands = 'enableVoiceCommands';
const String _prefPureBlackMode = 'pureBlackMode';
const String _prefPunctuationInsert = 'punctuationInsert';
const String _prefBuiltInVoiceInput = 'builtInVoiceInput';
const String _prefAutoVoiceKeyClick = 'autoVoiceKeyClick';
const String _prefAutoVoiceKeyClickDelayMs = 'autoVoiceKeyClickDelayMs';
const String _prefAutoVoiceKeyClickDurationMs = 'autoVoiceKeyClickDurationMs';
const String _prefTypeMemo = 'typeMemo';
const String _prefPunctuationKeyX = 'punctuationKeyX';
const String _prefPunctuationKeyY = 'punctuationKeyY';

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

class TypeMemoStore {
  TypeMemoStore._();

  static final TypeMemoStore instance = TypeMemoStore._();

  Database? _db;

  Future<Database> get database async {
    final existing = _db;
    if (existing != null) {
      return existing;
    }
    final dir = await getApplicationDocumentsDirectory();
    final dbPath = p.join(dir.path, 'type_memo.db');
    final db = await openDatabase(
      dbPath,
      version: 2,
      onCreate: (db, version) async {
        await _createDailyTable(db);
      },
      onUpgrade: (db, oldVersion, newVersion) async {
        if (oldVersion < 2) {
          await _createDailyTable(db);
        }
      },
    );
    _db = db;
    return db;
  }

  static Future<void> _createDailyTable(Database db) async {
    await db.execute('''
CREATE TABLE IF NOT EXISTS type_memo_days (
  day TEXT PRIMARY KEY,
  text TEXT NOT NULL
)
''');
  }

  Future<void> upsertDayText(String text, {DateTime? now}) async {
    final time = now ?? DateTime.now();
    final db = await database;
    await db.insert(
      'type_memo_days',
      <String, Object?>{
        'day': _formatDay(time),
        'text': text,
      },
      conflictAlgorithm: ConflictAlgorithm.replace,
    );
  }

  Future<String> loadDocument({String? day}) async {
    final db = await database;
    if (day != null) {
      final rows = await db.query(
        'type_memo_days',
        columns: <String>['text'],
        where: 'day = ?',
        whereArgs: <Object?>[day],
        limit: 1,
      );
      if (rows.isEmpty) {
        return '';
      }
      return rows.first['text'] as String;
    }

    final rows = await db.query(
      'type_memo_days',
      columns: <String>['day', 'text'],
      orderBy: 'day ASC',
    );
    final buffer = StringBuffer();
    for (final row in rows) {
      final rowDay = row['day'] as String;
      final text = row['text'] as String;
      if (text.isEmpty) {
        continue;
      }
      if (buffer.isNotEmpty) {
        buffer.writeln();
      }
      buffer.writeln('===== $rowDay =====');
      buffer.write(text);
      if (!text.endsWith('\n')) {
        buffer.writeln();
      }
    }
    return buffer.toString();
  }

  Future<List<String>> loadDays() async {
    final db = await database;
    final rows = await db.rawQuery(
      "SELECT day FROM type_memo_days WHERE text <> '' ORDER BY day DESC",
    );
    return rows.map((row) => row['day'] as String).toList();
  }

  Future<void> deleteDay(String day) async {
    final db = await database;
    await db.delete(
      'type_memo_days',
      where: 'day = ?',
      whereArgs: <Object?>[day],
    );
  }

  Future<void> deleteAll() async {
    final db = await database;
    await db.delete('type_memo_days');
  }

  static String _formatDay(DateTime time) {
    final local = time.toLocal();
    final year = local.year.toString().padLeft(4, '0');
    final month = local.month.toString().padLeft(2, '0');
    final day = local.day.toString().padLeft(2, '0');
    return '$year-$month-$day';
  }
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
  bool _punctuationInsert = false;
  bool _builtInVoiceInput = false;
  bool _autoVoiceKeyClick = false;
  bool _typeMemo = false;
  bool _secureWebSocket = false;
  double _autoVoiceKeyClickDelayMs = 500;
  double _autoVoiceKeyClickDurationMs = 500;
  String _lastTypeMemoText = '';
  bool _builtInVoiceListening = false;
  String _builtInVoiceStatus = 'ready';
  String _builtInVoiceBaseText = '';
  bool _settingsOpen = false;
  bool _scannerOpen = false;
  bool _recentlyTyping = false;
  bool _overlayUpdatingInput = false;
  Offset? _punctuationKeyOffset;
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
    if (Platform.isAndroid) {
      _overlayChannel.invokeMethod<void>('stopBuiltInVoice').catchError((_) {});
    }
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
    if (call.method == 'builtInVoiceStatus') {
      final status = call.arguments is String ? call.arguments as String : '';
      if (!mounted) {
        return null;
      }
      setState(() {
        _builtInVoiceStatus = status;
        _builtInVoiceListening = status == 'loading' || status == 'listening';
      });
      if (status.startsWith('error:')) {
        _showBuiltInVoiceDebugMessage(status.substring('error:'.length));
      } else if (status == 'permission_missing') {
        _showBuiltInVoiceDebugMessage('缺少麦克风权限，请允许 Flow Voice 使用麦克风。');
      }
      return null;
    }
    if (call.method == 'overlayDiagnostic') {
      final message = call.arguments is String ? call.arguments as String : '';
      if (message.isNotEmpty) {
        _showOverlayDiagnostic(message);
      }
      return null;
    }
    if (call.method == 'builtInVoiceText') {
      final args = call.arguments;
      if (args is! Map) {
        return null;
      }
      final text = args['text'] is String ? args['text'] as String : '';
      final isFinal = args['final'] == true;
      if (text.isEmpty) {
        return null;
      }
      final next = '$_builtInVoiceBaseText$text';
      if (_inputController.text != next) {
        _overlayUpdatingInput = true;
        _inputController.value = TextEditingValue(
          text: next,
          selection: TextSelection.collapsed(offset: next.length),
        );
        _overlayUpdatingInput = false;
        _recordTypeMemoChange(next);
        _markRecentlyTyping();
        _syncInput();
      }
      if (isFinal) {
        _builtInVoiceBaseText = next;
      }
      return null;
    }
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
    _recordTypeMemoChange(text);
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
        'builtInVoice': _builtInVoiceInput,
        'autoVoiceClick': _autoVoiceKeyClick && !_builtInVoiceInput,
        'autoVoiceClickDelayMs': _autoVoiceKeyClickDelayMs.round(),
        'autoVoiceClickDurationMs': _autoVoiceKeyClickDurationMs.round(),
      });
      return started ?? false;
    } catch (_) {
      return false;
    }
  }

  Future<void> _openFloatingInput() async {
    if (_builtInVoiceInput) {
      final ready = await _ensureBuiltInVoicePermission();
      if (!ready) {
        return;
      }
      _builtInVoiceBaseText = _inputController.text;
    }
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

  Future<bool> _ensureBuiltInVoicePermission() async {
    if (!Platform.isAndroid) {
      return false;
    }
    try {
      final granted = await _overlayChannel
              .invokeMethod<bool>('hasRecordAudioPermission') ??
          false;
      if (granted) {
        return true;
      }
      await _overlayChannel.invokeMethod<void>('requestRecordAudioPermission');
      return false;
    } catch (_) {
      return false;
    }
  }

  Future<void> _toggleBuiltInVoice() async {
    if (!_builtInVoiceInput || !Platform.isAndroid) {
      return;
    }
    if (_builtInVoiceListening) {
      try {
        await _overlayChannel.invokeMethod<void>('stopBuiltInVoice');
      } catch (_) {}
      if (mounted) {
        setState(() {
          _builtInVoiceListening = false;
          _builtInVoiceStatus = 'stopped';
        });
      }
      return;
    }
    final ready = await _ensureBuiltInVoicePermission();
    if (!ready) {
      return;
    }
    _builtInVoiceBaseText = _inputController.text;
    try {
      await _overlayChannel.invokeMethod<bool>('startBuiltInVoice');
      if (mounted) {
        setState(() {
          _builtInVoiceListening = true;
          _builtInVoiceStatus = 'loading';
        });
      }
    } catch (_) {
      if (mounted) {
        setState(() => _builtInVoiceStatus = 'error');
      }
    }
  }

  Future<void> _stopBuiltInVoice() async {
    if (!Platform.isAndroid) {
      return;
    }
    try {
      await _overlayChannel.invokeMethod<void>('stopBuiltInVoice');
    } catch (_) {}
    if (mounted) {
      setState(() {
        _builtInVoiceListening = false;
        _builtInVoiceStatus = 'stopped';
      });
    }
  }

  void _showBuiltInVoiceDebugMessage(String message) {
    if (!mounted || message.trim().isEmpty) {
      return;
    }
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) {
        return;
      }
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text('自带语音错误：${message.trim()}'),
          duration: const Duration(seconds: 8),
          behavior: SnackBarBehavior.floating,
        ),
      );
    });
  }

  void _showOverlayDiagnostic(String message) {
    final text = switch (message) {
      'accessibility_not_running' => '请先开启 Flow Voice 无障碍服务。',
      'voice_click_point_missing' => '请先校准语音键点击位置。',
      'voice_click_point_invalid' => '语音键点击位置无效，请重新校准。',
      'voice_click_dispatch_failed' => '自动点击失败，请检查无障碍权限。',
      'voice_hold_requires_android_8' => '长按语音需要 Android 8.0 或更高版本。',
      _ => message,
    };
    if (!mounted) {
      return;
    }
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) {
        return;
      }
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(text),
          duration: const Duration(seconds: 5),
          behavior: SnackBarBehavior.floating,
        ),
      );
    });
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
      _punctuationInsert = prefs.getBool(_prefPunctuationInsert) ?? false;
      _builtInVoiceInput = prefs.getBool(_prefBuiltInVoiceInput) ?? false;
      _autoVoiceKeyClick = prefs.getBool(_prefAutoVoiceKeyClick) ?? false;
      _typeMemo = prefs.getBool(_prefTypeMemo) ?? false;
      _autoVoiceKeyClickDelayMs =
          (prefs.getInt(_prefAutoVoiceKeyClickDelayMs) ?? 500).toDouble();
      _autoVoiceKeyClickDurationMs =
          (prefs.getInt(_prefAutoVoiceKeyClickDurationMs) ?? 500).toDouble();
      final punctuationX = prefs.getDouble(_prefPunctuationKeyX);
      final punctuationY = prefs.getDouble(_prefPunctuationKeyY);
      if (punctuationX != null && punctuationY != null) {
        _punctuationKeyOffset = Offset(punctuationX, punctuationY);
      }
      if (!_filterPunctuation) {
        _convertSpokenPunctuation = false;
      }
      _lastTypeMemoText = _inputController.text;
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
      prefs.setBool(_prefPunctuationInsert, _punctuationInsert),
      prefs.setBool(_prefBuiltInVoiceInput, _builtInVoiceInput),
      prefs.setBool(_prefAutoVoiceKeyClick, _autoVoiceKeyClick),
      prefs.setBool(_prefTypeMemo, _typeMemo),
      prefs.setInt(
        _prefAutoVoiceKeyClickDelayMs,
        _autoVoiceKeyClickDelayMs.round(),
      ),
      prefs.setInt(
        _prefAutoVoiceKeyClickDurationMs,
        _autoVoiceKeyClickDurationMs.round(),
      ),
    ]);
  }

  Future<void> _openAccessibilitySettings() async {
    if (!Platform.isAndroid) {
      return;
    }
    try {
      await _overlayChannel.invokeMethod<void>('openAccessibilitySettings');
    } catch (_) {}
  }

  Future<void> _startVoiceClickCalibration() async {
    if (!Platform.isAndroid) {
      return;
    }
    try {
      final granted =
          await _overlayChannel.invokeMethod<bool>('hasOverlayPermission') ??
              false;
      if (!granted) {
        await _overlayChannel.invokeMethod<void>('requestOverlayPermission');
        return;
      }
      final started = await _overlayChannel
              .invokeMethod<bool>('startVoiceClickCalibration') ??
          false;
      if (!started) {
        _showOverlayDiagnostic('voice_click_point_invalid');
      }
    } catch (_) {
      _showOverlayDiagnostic('voice_click_dispatch_failed');
    }
  }

  Future<void> _savePunctuationKeyOffset(Offset offset) async {
    final prefs = await SharedPreferences.getInstance();
    await Future.wait(<Future<bool>>[
      prefs.setDouble(_prefPunctuationKeyX, offset.dx),
      prefs.setDouble(_prefPunctuationKeyY, offset.dy),
    ]);
  }

  void _focusInputSoon({
    Duration delay = const Duration(milliseconds: 180),
    bool force = false,
  }) {
    if (_builtInVoiceInput) {
      return;
    }
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
    _recordTypeMemoChange(_inputController.text);
    _markRecentlyTyping();
    _syncInput();
  }

  void _recordTypeMemoChange(String currentText) {
    if (!_typeMemo) {
      _lastTypeMemoText = currentText;
      return;
    }
    if (currentText == _lastTypeMemoText) {
      return;
    }
    _lastTypeMemoText = currentText;
    unawaited(TypeMemoStore.instance.upsertDayText(currentText));
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

    final secureFromText =
        hostText.startsWith('https://') || hostText.startsWith('wss://');
    final normalized = hostText
        .replaceFirst(RegExp(r'^https?://'), '')
        .replaceFirst(RegExp(r'^wss?://'), '')
        .split('/')
        .first;
    final parts = normalized.split(':');
    final host = parts.first;
    final secure = _secureWebSocket || secureFromText;
    final port =
        parts.length > 1 ? int.tryParse(parts.last) : (secure ? null : 8787);

    return Uri(
      scheme: secure ? 'wss' : 'ws',
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

    _secureWebSocket = uri.scheme == 'https' || uri.scheme == 'wss';
    _hostController.text = uri.hasPort ? '${uri.host}:${uri.port}' : uri.host;
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
      if (message is Map) {
        final type = message['type'];
        if (type == 'error') {
          _setStatus(BridgeStatus.error, '电脑错误');
        } else if (type == 'voice_hold_start') {
          _startRemoteVoiceHold();
        } else if (type == 'voice_hold_stop') {
          _stopRemoteVoiceHold();
        }
      }
    } catch (_) {
      // Ignore diagnostics that are not JSON.
    }
  }

  Future<void> _startRemoteVoiceHold() async {
    if (!Platform.isAndroid) {
      return;
    }
    try {
      final result =
          await _overlayChannel.invokeMethod<String>('startVoiceHold');
      if (result != null && result != 'ok') {
        _showOverlayDiagnostic(result);
      }
    } catch (_) {
      _showOverlayDiagnostic('voice_click_dispatch_failed');
    }
  }

  Future<void> _stopRemoteVoiceHold() async {
    if (!Platform.isAndroid) {
      return;
    }
    try {
      await _overlayChannel.invokeMethod<void>('stopVoiceHold');
    } catch (_) {}
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

  void _insertPunctuation(String text) {
    _send(<String, Object?>{
      'type': 'insert_text',
      'token': _tokenController.text.trim(),
      'seq': ++_seq,
      'text': text,
      'source': 'punctuation_button',
    });
  }

  void _sendBackspace() {
    _send(<String, Object?>{
      'type': 'ops',
      'token': _tokenController.text.trim(),
      'seq': ++_seq,
      'ops': <Map<String, Object?>>[
        <String, Object?>{'type': 'backspace', 'count': 1},
      ],
    });
  }

  Offset _resolvedPunctuationOffset(BoxConstraints constraints) {
    final stageRect = _stageRect(constraints);
    final relative =
        _punctuationKeyOffset ?? _defaultPunctuationOffset(stageRect);
    return _clampPunctuationOffset(stageRect.topLeft + relative, constraints);
  }

  Rect _stageRect(BoxConstraints constraints) {
    final stageWidth = constraints.maxWidth.clamp(280.0, 820.0);
    final stageHeight = stageWidth / (820 / 680);
    final stageTop = 92 + ((constraints.maxHeight - 92 - stageHeight) / 2);
    return Rect.fromLTWH(
      (constraints.maxWidth - stageWidth) / 2,
      stageTop,
      stageWidth,
      stageHeight,
    );
  }

  Offset _defaultPunctuationOffset(Rect stageRect) {
    return Offset(
      (stageRect.width - _PunctuationToolbar.width) / 2,
      stageRect.height + 8,
    );
  }

  Offset _clampPunctuationOffset(
    Offset offset,
    BoxConstraints constraints,
  ) {
    final maxX = (constraints.maxWidth - _PunctuationToolbar.width)
        .clamp(0.0, double.infinity);
    final maxY = (constraints.maxHeight - _PunctuationToolbar.height)
        .clamp(0.0, double.infinity);
    return Offset(
      offset.dx.clamp(0.0, maxX),
      offset.dy.clamp(0.0, maxY),
    );
  }

  void _movePunctuationKey(Offset delta, BoxConstraints constraints) {
    final stageRect = _stageRect(constraints);
    final nextAbsolute = _clampPunctuationOffset(
      _resolvedPunctuationOffset(constraints) + delta,
      constraints,
    );
    final nextRelative = nextAbsolute - stageRect.topLeft;
    setState(() {
      _punctuationKeyOffset = nextRelative;
    });
    _savePunctuationKeyOffset(nextRelative);
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
              if (_builtInVoiceInput) {
                _inputFocusNode.unfocus();
                SystemChannels.textInput.invokeMethod<void>('TextInput.hide');
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
                  punctuationInsert: _punctuationInsert,
                  builtInVoiceInput: _builtInVoiceInput,
                  autoVoiceKeyClick: _autoVoiceKeyClick,
                  typeMemo: _typeMemo,
                  autoVoiceKeyClickDelayMs: _autoVoiceKeyClickDelayMs,
                  autoVoiceKeyClickDurationMs: _autoVoiceKeyClickDurationMs,
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
                  onPunctuationInsertChanged: (value) =>
                      update(() => _punctuationInsert = value),
                  onBuiltInVoiceInputChanged: (value) => update(() {
                    if (!value) {
                      _stopBuiltInVoice();
                    }
                    _builtInVoiceInput = value;
                    if (value) {
                      _builtInVoiceBaseText = _inputController.text;
                    }
                  }),
                  onAutoVoiceKeyClickChanged: (value) =>
                      update(() => _autoVoiceKeyClick = value),
                  onAutoVoiceKeyClickDelayChanged: (value) =>
                      update(() => _autoVoiceKeyClickDelayMs = value),
                  onAutoVoiceKeyClickDurationChanged: (value) =>
                      update(() => _autoVoiceKeyClickDurationMs = value),
                  onTypeMemoChanged: (value) => update(() {
                    _typeMemo = value;
                    _lastTypeMemoText = _inputController.text;
                  }),
                  onOpenTypeMemo: _openTypeMemoPage,
                  onOpenAccessibilitySettings: _openAccessibilitySettings,
                  onCalibrateVoiceKeyClick: _startVoiceClickCalibration,
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

  void _openTypeMemoPage() {
    Navigator.of(context).pop();
    Future<void>.delayed(const Duration(milliseconds: 180), () {
      if (!mounted) {
        return;
      }
      Navigator.of(context).push<void>(
        MaterialPageRoute<void>(
          builder: (_) => const TypeMemoPage(),
        ),
      );
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
            child: LayoutBuilder(
              builder: (context, constraints) {
                final punctuationOffset =
                    _resolvedPunctuationOffset(constraints);
                return Stack(
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
                            _enableVoiceCommands ||
                            _builtInVoiceInput ||
                            _autoVoiceKeyClick,
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
                    if (_punctuationInsert)
                      Positioned(
                        left: punctuationOffset.dx,
                        top: punctuationOffset.dy,
                        child: _PunctuationToolbar(
                          onInsert: _insertPunctuation,
                          onBackspace: _sendBackspace,
                          onMoved: (offset) =>
                              _movePunctuationKey(offset, constraints),
                        ),
                      ),
                    if (_builtInVoiceInput)
                      Positioned(
                        left: 0,
                        right: 0,
                        bottom: 22,
                        child: Center(
                          child: _BuiltInVoiceDock(
                            listening: _builtInVoiceListening,
                            status: _builtInVoiceStatus,
                            onTap: _toggleBuiltInVoice,
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
                );
              },
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

class _BuiltInVoiceDock extends StatelessWidget {
  const _BuiltInVoiceDock({
    required this.listening,
    required this.status,
    required this.onTap,
  });

  final bool listening;
  final String status;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final label = listening ? '正在录音' : '自带语音';
    final statusLabel = switch (status) {
      'loading' => '加载中',
      'listening' => '识别中',
      'permission_missing' => '需要麦克风权限',
      'stopped' => '已停止',
      _ when status.startsWith('error:') => '模型错误',
      _ => '点击开始',
    };
    return Semantics(
      label: label,
      button: true,
      child: Material(
        color: Colors.transparent,
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(18),
          child: Container(
            padding: const EdgeInsets.fromLTRB(10, 8, 14, 8),
            decoration: BoxDecoration(
              color: const Color(0xFF111111),
              borderRadius: BorderRadius.circular(18),
              border: Border.all(color: const Color(0xFF050505), width: 2.5),
              boxShadow: const <BoxShadow>[
                BoxShadow(
                  color: Color(0xFF111111),
                  offset: Offset(3, 3),
                  blurRadius: 0,
                ),
              ],
            ),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: <Widget>[
                Container(
                  width: 38,
                  height: 38,
                  decoration: BoxDecoration(
                    color: listening
                        ? const Color(0xFF28F58D)
                        : const Color(0xFFF4FFF8),
                    borderRadius: BorderRadius.circular(12),
                    border: Border.all(
                      color: const Color(0xFF050505),
                      width: 2,
                    ),
                  ),
                  child: Icon(
                    listening ? Icons.stop : Icons.mic,
                    color: const Color(0xFF050505),
                    size: 22,
                  ),
                ),
                const SizedBox(width: 10),
                Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    Text(
                      label,
                      style: const TextStyle(
                        color: Color(0xFFF4FFF8),
                        fontSize: 13,
                        fontWeight: FontWeight.w800,
                      ),
                    ),
                    Text(
                      statusLabel,
                      style: const TextStyle(
                        color: Color(0xFFA5B6AA),
                        fontSize: 10,
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                  ],
                ),
              ],
            ),
          ),
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

class _PunctuationToolbar extends StatefulWidget {
  const _PunctuationToolbar({
    required this.onInsert,
    required this.onBackspace,
    required this.onMoved,
  });

  static const double keySize = 54;
  static const double gap = 10;
  static const double width = keySize * 2 + gap;
  static const double height = keySize;

  final ValueChanged<String> onInsert;
  final VoidCallback onBackspace;
  final ValueChanged<Offset> onMoved;

  @override
  State<_PunctuationToolbar> createState() => _PunctuationToolbarState();
}

class _PunctuationToolbarState extends State<_PunctuationToolbar> {
  Offset _lastDragOffset = Offset.zero;
  bool _dragging = false;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTap: () {},
      onLongPressStart: (_) {
        _dragging = true;
        _lastDragOffset = Offset.zero;
      },
      onLongPressMoveUpdate: (details) {
        if (_dragging) {
          final delta = details.offsetFromOrigin - _lastDragOffset;
          _lastDragOffset = details.offsetFromOrigin;
          widget.onMoved(delta);
        }
      },
      onLongPressEnd: (_) {
        _dragging = false;
        _lastDragOffset = Offset.zero;
      },
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: <Widget>[
          _PunctuationButton(
            enabled: !_dragging,
            onInsert: widget.onInsert,
          ),
          const SizedBox(width: _PunctuationToolbar.gap),
          _BackspaceButton(
            enabled: !_dragging,
            onBackspace: widget.onBackspace,
          ),
        ],
      ),
    );
  }
}

class _PunctuationButton extends StatefulWidget {
  const _PunctuationButton({
    required this.enabled,
    required this.onInsert,
  });

  final bool enabled;
  final ValueChanged<String> onInsert;

  @override
  State<_PunctuationButton> createState() => _PunctuationButtonState();
}

class _PunctuationButtonState extends State<_PunctuationButton> {
  Offset? _downPosition;
  bool _handled = false;

  void _handleUp(PointerUpEvent event) {
    if (!widget.enabled || _handled) {
      _downPosition = null;
      _handled = false;
      return;
    }
    final start = _downPosition;
    _downPosition = null;
    _handled = true;
    final deltaY = start == null ? 0.0 : event.position.dy - start.dy;
    widget.onInsert(deltaY < -18 ? '。' : '，');
  }

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTap: () {},
      child: Listener(
        behavior: HitTestBehavior.opaque,
        onPointerDown: (event) {
          _downPosition = event.position;
          _handled = false;
        },
        onPointerMove: (event) {
          final start = _downPosition;
          if (!widget.enabled || start == null || _handled) {
            return;
          }
          if (event.position.dy - start.dy < -26) {
            _handled = true;
            widget.onInsert('。');
          }
        },
        onPointerUp: _handleUp,
        onPointerCancel: (_) {
          _downPosition = null;
          _handled = false;
        },
        child: const RepaintBoundary(
          child: CustomPaint(
            painter: _PunctuationKeyPainter(),
            child: SizedBox(
              width: _PunctuationToolbar.keySize,
              height: _PunctuationToolbar.keySize,
            ),
          ),
        ),
      ),
    );
  }
}

class _BackspaceButton extends StatelessWidget {
  const _BackspaceButton({
    required this.enabled,
    required this.onBackspace,
  });

  final bool enabled;
  final VoidCallback onBackspace;

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTap: enabled ? onBackspace : null,
      child: const RepaintBoundary(
        child: CustomPaint(
          painter: _BackspaceKeyPainter(),
          child: SizedBox(
            width: _PunctuationToolbar.keySize,
            height: _PunctuationToolbar.keySize,
          ),
        ),
      ),
    );
  }
}

class _PunctuationKeyPainter extends CustomPainter {
  const _PunctuationKeyPainter();

  @override
  void paint(Canvas canvas, Size size) {
    final shadowPaint = Paint()..color = const Color(0xFF111111);
    final buttonPaint = Paint()..color = Colors.white;
    final borderPaint = Paint()
      ..color = const Color(0xFF111111)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 2.5;
    const radius = Radius.circular(15);
    final buttonRect = Offset.zero & Size(size.width - 3, size.height - 3);
    final shadowRect = buttonRect.shift(const Offset(3, 3));
    canvas.drawRRect(RRect.fromRectAndRadius(shadowRect, radius), shadowPaint);
    canvas.drawRRect(RRect.fromRectAndRadius(buttonRect, radius), buttonPaint);
    canvas.drawRRect(RRect.fromRectAndRadius(buttonRect, radius), borderPaint);

    final textPainter = TextPainter(
      textAlign: TextAlign.center,
      textDirection: TextDirection.ltr,
    );
    void drawMark(String mark, double y) {
      textPainter.text = TextSpan(
        text: mark,
        style: const TextStyle(
          color: Color(0xFF050505),
          fontSize: 19,
          fontWeight: FontWeight.w900,
          height: 1,
        ),
      );
      textPainter.layout();
      textPainter.paint(
        canvas,
        Offset((buttonRect.width - textPainter.width) / 2, y),
      );
    }

    drawMark('。', 10);
    drawMark('，', 27);
  }

  @override
  bool shouldRepaint(covariant _PunctuationKeyPainter oldDelegate) => false;
}

class _BackspaceKeyPainter extends CustomPainter {
  const _BackspaceKeyPainter();

  @override
  void paint(Canvas canvas, Size size) {
    final shadowPaint = Paint()..color = const Color(0xFF111111);
    final buttonPaint = Paint()..color = Colors.white;
    final borderPaint = Paint()
      ..color = const Color(0xFF111111)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 2.5;
    const radius = Radius.circular(15);
    final buttonRect = Offset.zero & Size(size.width - 3, size.height - 3);
    final shadowRect = buttonRect.shift(const Offset(3, 3));
    canvas.drawRRect(RRect.fromRectAndRadius(shadowRect, radius), shadowPaint);
    canvas.drawRRect(RRect.fromRectAndRadius(buttonRect, radius), buttonPaint);
    canvas.drawRRect(RRect.fromRectAndRadius(buttonRect, radius), borderPaint);

    final textPainter = TextPainter(
      text: const TextSpan(
        text: '×',
        style: TextStyle(
          color: Color(0xFF050505),
          fontSize: 28,
          fontWeight: FontWeight.w900,
          height: 1,
        ),
      ),
      textAlign: TextAlign.center,
      textDirection: TextDirection.ltr,
    )..layout();
    textPainter.paint(
      canvas,
      Offset(
        (buttonRect.width - textPainter.width) / 2,
        (buttonRect.height - textPainter.height) / 2 - 1,
      ),
    );
  }

  @override
  bool shouldRepaint(covariant _BackspaceKeyPainter oldDelegate) => false;
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

class _TimingSlider extends StatelessWidget {
  const _TimingSlider({
    required this.title,
    required this.value,
    required this.min,
    required this.max,
    required this.divisions,
    required this.onChanged,
  });

  final String title;
  final double value;
  final double min;
  final double max;
  final int divisions;
  final ValueChanged<double> onChanged;

  @override
  Widget build(BuildContext context) {
    final displayValue = value.round();
    return Container(
      padding: const EdgeInsets.fromLTRB(12, 10, 12, 8),
      decoration: BoxDecoration(
        color: const Color(0x6608100D),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: const Color(0x2428F58D)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: <Widget>[
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: <Widget>[
              Text(
                title,
                style: const TextStyle(
                  color: Color(0xFFDDE7DF),
                  fontSize: 14,
                  fontWeight: FontWeight.w900,
                ),
              ),
              Text(
                '${displayValue}ms',
                style: const TextStyle(
                  color: Color(0xFF28F58D),
                  fontSize: 13,
                  fontWeight: FontWeight.w900,
                ),
              ),
            ],
          ),
          SliderTheme(
            data: SliderTheme.of(context).copyWith(
              activeTrackColor: const Color(0xFF28F58D),
              inactiveTrackColor: const Color(0x3328F58D),
              thumbColor: const Color(0xFFF4FFF8),
              overlayColor: const Color(0x3328F58D),
            ),
            child: Slider(
              value: value.clamp(min, max),
              min: min,
              max: max,
              divisions: divisions,
              onChanged: onChanged,
            ),
          ),
        ],
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
    required this.punctuationInsert,
    required this.builtInVoiceInput,
    required this.autoVoiceKeyClick,
    required this.typeMemo,
    required this.autoVoiceKeyClickDelayMs,
    required this.autoVoiceKeyClickDurationMs,
    required this.onFilterChanged,
    required this.onConvertChanged,
    required this.onCommandChanged,
    required this.onPureBlackChanged,
    required this.onPunctuationInsertChanged,
    required this.onBuiltInVoiceInputChanged,
    required this.onAutoVoiceKeyClickChanged,
    required this.onTypeMemoChanged,
    required this.onOpenTypeMemo,
    required this.onAutoVoiceKeyClickDelayChanged,
    required this.onAutoVoiceKeyClickDurationChanged,
    required this.onOpenAccessibilitySettings,
    required this.onCalibrateVoiceKeyClick,
    required this.onClose,
  });

  final bool filterPunctuation;
  final bool convertSpokenPunctuation;
  final bool enableVoiceCommands;
  final bool pureBlackMode;
  final bool punctuationInsert;
  final bool builtInVoiceInput;
  final bool autoVoiceKeyClick;
  final bool typeMemo;
  final double autoVoiceKeyClickDelayMs;
  final double autoVoiceKeyClickDurationMs;
  final ValueChanged<bool> onFilterChanged;
  final ValueChanged<bool>? onConvertChanged;
  final ValueChanged<bool> onCommandChanged;
  final ValueChanged<bool> onPureBlackChanged;
  final ValueChanged<bool> onPunctuationInsertChanged;
  final ValueChanged<bool> onBuiltInVoiceInputChanged;
  final ValueChanged<bool> onAutoVoiceKeyClickChanged;
  final ValueChanged<bool> onTypeMemoChanged;
  final VoidCallback onOpenTypeMemo;
  final ValueChanged<double> onAutoVoiceKeyClickDelayChanged;
  final ValueChanged<double> onAutoVoiceKeyClickDurationChanged;
  final VoidCallback onOpenAccessibilitySettings;
  final VoidCallback onCalibrateVoiceKeyClick;
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
              _SettingSwitch(
                title: '标点插入',
                description: '开启后显示可拖动按键组；标点键点击逗号、上划句号，× 键执行 backspace。',
                value: punctuationInsert,
                onChanged: onPunctuationInsertChanged,
              ),
              const SizedBox(height: 12),
              _SettingSwitch(
                title: '自带语音输入',
                description: '开启后不依赖系统输入法键盘，使用内置 sherpa-onnx 离线模型识别语音。',
                value: builtInVoiceInput,
                onChanged: onBuiltInVoiceInputChanged,
              ),
              const SizedBox(height: 12),
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: <Widget>[
                  Expanded(
                    child: _SettingSwitch(
                      title: 'Type memo',
                      description: '保存手机端原始输入内容，支持按日期查看和正则搜索。',
                      value: typeMemo,
                      onChanged: onTypeMemoChanged,
                    ),
                  ),
                  const SizedBox(width: 10),
                  SizedBox(
                    width: 88,
                    child: _VoiceButton(
                      label: '查看',
                      onPressed: onOpenTypeMemo,
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 12),
              _SettingSwitch(
                title: '自动点击键盘语音键',
                description: '普通悬浮窗唤起键盘后，按下方参数点击你校准的位置，用来打开系统输入法语音输入。',
                value: autoVoiceKeyClick,
                onChanged: onAutoVoiceKeyClickChanged,
              ),
              const SizedBox(height: 10),
              _TimingSlider(
                title: '点击延迟',
                value: autoVoiceKeyClickDelayMs,
                min: 200,
                max: 1200,
                divisions: 100,
                onChanged: onAutoVoiceKeyClickDelayChanged,
              ),
              const SizedBox(height: 10),
              _TimingSlider(
                title: '按压时长',
                value: autoVoiceKeyClickDurationMs,
                min: 100,
                max: 1000,
                divisions: 90,
                onChanged: onAutoVoiceKeyClickDurationChanged,
              ),
              const SizedBox(height: 10),
              _VoiceButton(
                label: '打开无障碍设置',
                onPressed: onOpenAccessibilitySettings,
              ),
              const SizedBox(height: 10),
              _VoiceButton(
                label: '调整语音键位置',
                onPressed: onCalibrateVoiceKeyClick,
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

class TypeMemoPage extends StatefulWidget {
  const TypeMemoPage({super.key});

  @override
  State<TypeMemoPage> createState() => _TypeMemoPageState();
}

class _TypeMemoPageState extends State<TypeMemoPage> {
  final TextEditingController _searchController = TextEditingController();
  final ScrollController _scrollController = ScrollController();

  List<String> _days = <String>[];
  String? _selectedDay;
  String _document = '';
  List<RegExpMatch> _matches = <RegExpMatch>[];
  int _currentMatch = 0;
  String? _regexError;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _searchController.addListener(_runSearch);
    _load();
  }

  @override
  void dispose() {
    _searchController.dispose();
    _scrollController.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    final days = await TypeMemoStore.instance.loadDays();
    var selectedDay = _selectedDay;
    if (selectedDay != null && !days.contains(selectedDay)) {
      selectedDay = null;
    }
    final document =
        await TypeMemoStore.instance.loadDocument(day: selectedDay);
    if (!mounted) {
      return;
    }
    setState(() {
      _days = days;
      _selectedDay = selectedDay;
      _document = document;
      _loading = false;
    });
    _runSearch();
  }

  void _runSearch() {
    final pattern = _searchController.text;
    if (pattern.isEmpty) {
      setState(() {
        _matches = <RegExpMatch>[];
        _currentMatch = 0;
        _regexError = null;
      });
      return;
    }
    try {
      final regex = RegExp(pattern, multiLine: true);
      final matches = regex
          .allMatches(_document)
          .where((match) => match.end > match.start)
          .toList();
      setState(() {
        _matches = matches;
        _currentMatch =
            matches.isEmpty ? 0 : _currentMatch.clamp(0, matches.length - 1);
        _regexError = null;
      });
      _scrollToCurrentMatch();
    } on FormatException catch (error) {
      setState(() {
        _matches = <RegExpMatch>[];
        _currentMatch = 0;
        _regexError = error.message;
      });
    }
  }

  void _moveMatch(int delta) {
    if (_matches.isEmpty) {
      return;
    }
    setState(() {
      _currentMatch = (_currentMatch + delta) % _matches.length;
      if (_currentMatch < 0) {
        _currentMatch += _matches.length;
      }
    });
    _scrollToCurrentMatch();
  }

  void _scrollToCurrentMatch() {
    if (_matches.isEmpty ||
        !_scrollController.hasClients ||
        _document.isEmpty) {
      return;
    }
    final start = _matches[_currentMatch].start;
    final ratio = start / _document.length;
    final target = _scrollController.position.maxScrollExtent * ratio;
    _scrollController.animateTo(
      target.clamp(0.0, _scrollController.position.maxScrollExtent),
      duration: const Duration(milliseconds: 180),
      curve: Curves.easeOut,
    );
  }

  Future<void> _deleteSelectedDay() async {
    final day = _selectedDay;
    if (day == null) {
      return;
    }
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('删除当天记录'),
        content: Text('确定删除 $day 的 Type memo 吗？'),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('取消'),
          ),
          TextButton(
            onPressed: () => Navigator.of(context).pop(true),
            child: const Text('删除'),
          ),
        ],
      ),
    );
    if (confirmed != true) {
      return;
    }
    await TypeMemoStore.instance.deleteDay(day);
    if (!mounted) {
      return;
    }
    setState(() => _selectedDay = null);
    await _load();
  }

  Future<void> _deleteAll() async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('删除全部 Type memo'),
        content: const Text('这会永久删除全部 Type memo 记录，且不可恢复。确定继续吗？'),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('取消'),
          ),
          TextButton(
            onPressed: () => Navigator.of(context).pop(true),
            child: const Text('全部删除'),
          ),
        ],
      ),
    );
    if (confirmed != true) {
      return;
    }
    await TypeMemoStore.instance.deleteAll();
    if (!mounted) {
      return;
    }
    setState(() => _selectedDay = null);
    await _load();
  }

  TextSpan _buildHighlightedText() {
    if (_matches.isEmpty) {
      return TextSpan(text: _document);
    }
    final spans = <TextSpan>[];
    var cursor = 0;
    for (var i = 0; i < _matches.length; i += 1) {
      final match = _matches[i];
      if (match.start > cursor) {
        spans.add(TextSpan(text: _document.substring(cursor, match.start)));
      }
      final current = i == _currentMatch;
      spans.add(TextSpan(
        text: _document.substring(match.start, match.end),
        style: TextStyle(
          color: current ? const Color(0xFF06100B) : const Color(0xFF050807),
          backgroundColor:
              current ? const Color(0xFF28F58D) : const Color(0xFFE6D56D),
          fontWeight: FontWeight.w900,
        ),
      ));
      cursor = match.end;
    }
    if (cursor < _document.length) {
      spans.add(TextSpan(text: _document.substring(cursor)));
    }
    return TextSpan(children: spans);
  }

  @override
  Widget build(BuildContext context) {
    final matchLabel = _matches.isEmpty
        ? '0 / 0'
        : '${_currentMatch + 1} / ${_matches.length}';
    return Scaffold(
      backgroundColor: const Color(0xFF050807),
      appBar: AppBar(
        title: const Text('Type memo'),
        backgroundColor: const Color(0xFF050807),
        foregroundColor: const Color(0xFFF0FFF5),
        actions: <Widget>[
          IconButton(
            tooltip: '删除当天',
            onPressed: _selectedDay == null ? null : _deleteSelectedDay,
            icon: const Icon(Icons.delete_outline),
          ),
          IconButton(
            tooltip: '全部删除',
            onPressed: _deleteAll,
            icon: const Icon(Icons.delete_forever),
          ),
        ],
      ),
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(14),
          child: Column(
            children: <Widget>[
              Row(
                children: <Widget>[
                  Expanded(
                    child: DropdownButtonFormField<String?>(
                      initialValue: _selectedDay,
                      dropdownColor: const Color(0xFF08100D),
                      decoration: const InputDecoration(
                        labelText: '范围',
                        border: OutlineInputBorder(),
                      ),
                      items: <DropdownMenuItem<String?>>[
                        const DropdownMenuItem<String?>(
                          value: null,
                          child: Text('全部'),
                        ),
                        for (final day in _days)
                          DropdownMenuItem<String?>(
                            value: day,
                            child: Text(day),
                          ),
                      ],
                      onChanged: (value) async {
                        setState(() => _selectedDay = value);
                        await _load();
                      },
                    ),
                  ),
                  const SizedBox(width: 10),
                  Text(
                    matchLabel,
                    style: const TextStyle(
                      color: Color(0xFF28F58D),
                      fontWeight: FontWeight.w900,
                    ),
                  ),
                  IconButton(
                    onPressed: _matches.isEmpty ? null : () => _moveMatch(-1),
                    icon: const Icon(Icons.keyboard_arrow_up),
                  ),
                  IconButton(
                    onPressed: _matches.isEmpty ? null : () => _moveMatch(1),
                    icon: const Icon(Icons.keyboard_arrow_down),
                  ),
                ],
              ),
              const SizedBox(height: 10),
              TextField(
                controller: _searchController,
                decoration: InputDecoration(
                  labelText: '正则搜索',
                  errorText: _regexError,
                  prefixIcon: const Icon(Icons.search),
                  border: const OutlineInputBorder(),
                ),
              ),
              const SizedBox(height: 10),
              Expanded(
                child: Container(
                  width: double.infinity,
                  padding: const EdgeInsets.all(14),
                  decoration: BoxDecoration(
                    color: const Color(0xC706100B),
                    borderRadius: BorderRadius.circular(16),
                    border: Border.all(color: const Color(0x1F28F58D)),
                  ),
                  child: _loading
                      ? const Center(child: CircularProgressIndicator())
                      : SingleChildScrollView(
                          controller: _scrollController,
                          child: SelectableText.rich(
                            _buildHighlightedText(),
                            style: const TextStyle(
                              color: Color(0xFFDDE7DF),
                              fontSize: 14,
                              height: 1.55,
                              fontFamily: 'monospace',
                            ),
                          ),
                        ),
                ),
              ),
            ],
          ),
        ),
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
