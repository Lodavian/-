import 'dart:convert';
import 'dart:io';

import 'package:flutter/services.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:uuid/uuid.dart';

class DeviceSigner {
  DeviceSigner({FlutterSecureStorage? storage})
      : _storage = storage ??
            const FlutterSecureStorage(
                aOptions: AndroidOptions(encryptedSharedPreferences: true));

  static const _channel = MethodChannel('com.fastorder.mobile/device_key');
  static const _uuid = Uuid();
  final FlutterSecureStorage _storage;

  Future<String?> get deviceId => _storage.read(key: 'device_id');

  Future<String> ensureRegistered(String apiBaseUrl, String accessToken) async {
    final current = await deviceId;
    if (current != null) return current;
    final publicKey = await _channel.invokeMethod<String>('ensureKey');
    if (publicKey == null) throw StateError('Android Keystore 无法创建设备密钥');
    final client = HttpClient();
    try {
      final request =
          await client.postUrl(Uri.parse('$apiBaseUrl/v1/devices/register'));
      request.headers
          .set(HttpHeaders.authorizationHeader, 'Bearer $accessToken');
      request.headers.contentType = ContentType.json;
      request.write(jsonEncode({
        'label': '${Platform.operatingSystem} device',
        'publicKeyPem': publicKey
      }));
      final response = await request.close();
      final payload =
          jsonDecode(await utf8.decodeStream(response)) as Map<String, dynamic>;
      if (response.statusCode != 201) {
        throw StateError(payload['error']?['message']?.toString() ?? '设备登记失败');
      }
      final id = payload['deviceId'] as String;
      await _storage.write(key: 'device_id', value: id);
      return id;
    } finally {
      client.close(force: true);
    }
  }

  Future<Map<String, String>> signedHeaders({
    required String method,
    required String path,
    required List<int> body,
    required String accessToken,
    String idempotencyKey = '',
  }) async {
    final id = await deviceId;
    if (id == null) throw StateError('设备尚未登记');
    final timestamp =
        (DateTime.now().millisecondsSinceEpoch / 1000).toStringAsFixed(3);
    final nonce = _uuid.v4().replaceAll('-', '');
    final bodyHash = await _channel
        .invokeMethod<String>('sha256', {'bytes': Uint8List.fromList(body)});
    final canonical = [
      method.toUpperCase(),
      path,
      bodyHash,
      timestamp,
      nonce,
      idempotencyKey
    ].join('\n');
    final signature =
        await _channel.invokeMethod<String>('sign', {'message': canonical});
    if (signature == null) throw StateError('设备签名失败');
    return {
      HttpHeaders.authorizationHeader: 'Bearer $accessToken',
      'X-Device-Id': id,
      'X-Timestamp': timestamp,
      'X-Nonce': nonce,
      'X-Device-Signature': signature,
      if (idempotencyKey.isNotEmpty) 'Idempotency-Key': idempotencyKey,
    };
  }
}
