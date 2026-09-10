class AppConfig {
  const AppConfig._();

  static const apiBaseUrl = String.fromEnvironment('FASTORDER_API_URL');
  static const oidcDiscoveryUrl =
      String.fromEnvironment('FASTORDER_OIDC_DISCOVERY_URL');
  static const oidcClientId =
      String.fromEnvironment('FASTORDER_OIDC_CLIENT_ID');
  static const oidcRedirectUrl = String.fromEnvironment(
    'FASTORDER_OIDC_REDIRECT_URL',
    defaultValue: 'com.fastorder.mobile:/oauthredirect',
  );

  static void validate() {
    if (!apiBaseUrl.startsWith('https://') ||
        !oidcDiscoveryUrl.startsWith('https://') ||
        oidcClientId.isEmpty) {
      throw StateError(
          '必须通过 dart-define 配置 HTTPS API、OIDC discovery URL 和 client ID');
    }
  }
}
