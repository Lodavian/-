import 'dart:async';
import 'dart:convert';

import 'package:web_socket_channel/io.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

import '../models.dart';
import 'device_signer.dart';

class EventService {
  EventService(this.apiBaseUrl, this.signer);
  final String apiBaseUrl;
  final DeviceSigner signer;
  WebSocketChannel? _channel;
  StreamSubscription<dynamic>? _subscription;

  Future<void> connect(String accessToken, void Function(OrderView) onOrder,
      void Function() onDisconnected) async {
    await close();
    const path = '/v1/events';
    final headers = await signer.signedHeaders(
        method: 'GET', path: path, body: const [], accessToken: accessToken);
    final uri = Uri.parse('$apiBaseUrl$path').replace(scheme: 'wss');
    _channel = IOWebSocketChannel.connect(uri,
        headers: headers, pingInterval: const Duration(seconds: 20));
    _subscription = _channel!.stream.listen((raw) {
      final event = jsonDecode(raw as String);
      if (event is Map &&
          event['type'] == 'order.update' &&
          event['order'] is Map) {
        onOrder(OrderView.fromJson(Map<String, dynamic>.from(event['order'])));
      }
    }, onError: (_) => onDisconnected(), onDone: onDisconnected);
  }

  Future<void> close() async {
    await _subscription?.cancel();
    _subscription = null;
    await _channel?.sink.close();
    _channel = null;
  }
}
