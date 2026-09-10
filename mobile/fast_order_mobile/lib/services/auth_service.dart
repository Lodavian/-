import 'package:flutter_appauth/flutter_appauth.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import '../config.dart';

class AuthSession {
  const AuthSession(this.accessToken, this.refreshToken, this.expiresAt);
  final String accessToken;
  final String? refreshToken;
  final DateTime? expiresAt;
}

class AuthService {
  AuthService({FlutterAppAuth? appAuth, FlutterSecureStorage? storage})
      : _appAuth = appAuth ?? const FlutterAppAuth(),
        _storage = storage ??
            const FlutterSecureStorage(
                aOptions: AndroidOptions(encryptedSharedPreferences: true));

  final FlutterAppAuth _appAuth;
  final FlutterSecureStorage _storage;

  Future<AuthSession?> restore() async {
    final access = await _storage.read(key: 'access_token');
    if (access == null) return null;
    final refresh = await _storage.read(key: 'refresh_token');
    final expiry =
        DateTime.tryParse(await _storage.read(key: 'expires_at') ?? '');
    if (expiry != null &&
        expiry.isBefore(DateTime.now().add(const Duration(seconds: 20)))) {
      if (refresh == null) return null;
      return refreshSession(refresh);
    }
    return AuthSession(access, refresh, expiry);
  }

  Future<AuthSession> login() async {
    final result = await _appAuth.authorizeAndExchangeCode(
      AuthorizationTokenRequest(
        AppConfig.oidcClientId,
        AppConfig.oidcRedirectUrl,
        discoveryUrl: AppConfig.oidcDiscoveryUrl,
        scopes: const ['openid', 'profile'],
        promptValues: const ['login'],
      ),
    );
    if (result.accessToken == null) throw StateError('登录没有返回 access token');
    return _save(result.accessToken!, result.refreshToken,
        result.accessTokenExpirationDateTime);
  }

  Future<AuthSession> refreshSession(String refreshToken) async {
    final result = await _appAuth.token(TokenRequest(
      AppConfig.oidcClientId,
      AppConfig.oidcRedirectUrl,
      discoveryUrl: AppConfig.oidcDiscoveryUrl,
      refreshToken: refreshToken,
      scopes: const ['openid', 'profile'],
    ));
    if (result.accessToken == null) throw StateError('登录已过期，请重新登录');
    return _save(result.accessToken!, result.refreshToken ?? refreshToken,
        result.accessTokenExpirationDateTime);
  }

  Future<AuthSession> _save(
      String access, String? refresh, DateTime? expiry) async {
    await _storage.write(key: 'access_token', value: access);
    if (refresh != null) {
      await _storage.write(key: 'refresh_token', value: refresh);
    }
    if (expiry != null) {
      await _storage.write(
          key: 'expires_at', value: expiry.toUtc().toIso8601String());
    }
    return AuthSession(access, refresh, expiry);
  }

  Future<void> logout() async {
    await _storage.delete(key: 'access_token');
    await _storage.delete(key: 'refresh_token');
    await _storage.delete(key: 'expires_at');
  }
}
