import 'dart:convert';

import 'package:dio/dio.dart';
import 'package:uuid/uuid.dart';

import '../models.dart';
import 'device_signer.dart';

class ApiFailure implements Exception {
  const ApiFailure(this.message, {this.networkUncertain = false});
  final String message;
  final bool networkUncertain;
  @override
  String toString() => message;
}

class FastOrderApi {
  FastOrderApi(this.baseUrl, this.signer)
      : _dio = Dio(BaseOptions(
            baseUrl: baseUrl,
            connectTimeout: const Duration(seconds: 8),
            receiveTimeout: const Duration(seconds: 20)));
  final String baseUrl;
  final DeviceSigner signer;
  final Dio _dio;
  static const _uuid = Uuid();
  String accessToken = '';

  Future<Response<dynamic>> _request(String method, String path,
      {Map<String, dynamic>? payload,
      String idempotencyKey = '',
      Map<String, dynamic>? query}) async {
    final text = payload == null ? '' : jsonEncode(payload);
    final bytes = utf8.encode(text);
    final headers = await signer.signedHeaders(
        method: method,
        path: path,
        body: bytes,
        accessToken: accessToken,
        idempotencyKey: idempotencyKey);
    try {
      return await _dio.request<dynamic>(path,
          data: payload == null ? null : text,
          queryParameters: query,
          options: Options(
              method: method,
              headers: {...headers, 'Content-Type': 'application/json'}));
    } on DioException catch (error) {
      final responseData = error.response?.data;
      String? message;
      if (responseData is Map) {
        final detail = responseData['error'];
        if (detail is Map) message = detail['message']?.toString();
      }
      throw ApiFailure(message ?? '网络请求失败；不会自动重发交易命令',
          networkUncertain: error.response == null);
    }
  }

  Future<Response<dynamic>> _mutation(
      String method, String path, Map<String, dynamic> payload) async {
    final requestId = _uuid.v4();
    try {
      return await _request(method, path,
          payload: payload, idempotencyKey: requestId);
    } on ApiFailure catch (original) {
      if (!original.networkUncertain) rethrow;
      final accountRef = payload['accountRef']?.toString() ?? '';
      for (var attempt = 0; attempt < 3; attempt++) {
        await Future<void>.delayed(Duration(milliseconds: 400 * (attempt + 1)));
        try {
          return await _request('GET', '/v1/requests/$requestId',
              query: {'accountRef': accountRef});
        } on ApiFailure {
          // Read-only polling may repeat; the original mutation is never resent.
        }
      }
      throw ApiFailure('交易结果暂未确认，请刷新订单列表；原请求不会自动重发', networkUncertain: true);
    }
  }

  Future<List<AccountSummary>> accounts() async {
    final response = await _request('GET', '/v1/accounts');
    return ((response.data['accounts'] ?? []) as List)
        .map((item) => AccountSummary.fromJson(Map<String, dynamic>.from(item)))
        .toList();
  }

  Future<bool> selectSymbol(String accountRef, String symbol) async {
    final response = await _request('POST', '/v1/symbols/select',
        payload: {'accountRef': accountRef, 'symbol': symbol});
    return response.data['status'] == 'READY';
  }

  Future<OrderView> submit(
      {required String accountRef,
      required String symbol,
      required String side,
      required String orderType,
      required int quantity,
      String? limitPrice}) async {
    final response = await _mutation('POST', '/v1/orders', {
      'accountRef': accountRef,
      'symbol': symbol,
      'side': side,
      'orderType': orderType,
      'quantity': quantity,
      'limitPrice': orderType == 'LIMIT' ? limitPrice : null,
    });
    return OrderView.fromJson(
        Map<String, dynamic>.from(response.data['order']));
  }

  Future<List<OrderView>> orders({String? accountRef}) async {
    final response = await _request('GET', '/v1/orders',
        query: {if (accountRef != null) 'accountRef': accountRef});
    return ((response.data['orders'] ?? []) as List)
        .map((item) => OrderView.fromJson(Map<String, dynamic>.from(item)))
        .toList();
  }

  Future<OrderView> detail(String accountRef, String clientOrderId) async {
    final response = await _request('GET', '/v1/orders/$clientOrderId',
        query: {'accountRef': accountRef});
    return OrderView.fromJson(Map<String, dynamic>.from(response.data));
  }

  Future<OrderView> replace(
      String accountRef, String clientOrderId, String limitPrice) async {
    final response = await _mutation('PATCH', '/v1/orders/$clientOrderId',
        {'accountRef': accountRef, 'limitPrice': limitPrice});
    return OrderView.fromJson(
        Map<String, dynamic>.from(response.data['order']));
  }

  Future<OrderView> cancel(String accountRef, String clientOrderId) async {
    final response = await _mutation(
        'POST', '/v1/orders/$clientOrderId/cancel', {'accountRef': accountRef});
    return OrderView.fromJson(
        Map<String, dynamic>.from(response.data['order']));
  }
}
