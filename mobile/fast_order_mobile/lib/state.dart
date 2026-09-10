import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'config.dart';
import 'models.dart';
import 'services/api_client.dart';
import 'services/auth_service.dart';
import 'services/device_signer.dart';
import 'services/event_service.dart';

final authServiceProvider = Provider((_) => AuthService());
final signerProvider = Provider((_) => DeviceSigner());
final apiProvider = Provider(
    (ref) => FastOrderApi(AppConfig.apiBaseUrl, ref.read(signerProvider)));
final eventProvider = Provider(
    (ref) => EventService(AppConfig.apiBaseUrl, ref.read(signerProvider)));
final appStateProvider =
    StateNotifierProvider<AppController, AppState>((ref) => AppController(ref));

class AppState {
  const AppState(
      {this.loading = true,
      this.session,
      this.accounts = const [],
      this.selected,
      this.orders = const [],
      this.error});
  final bool loading;
  final AuthSession? session;
  final List<AccountSummary> accounts;
  final AccountSummary? selected;
  final List<OrderView> orders;
  final String? error;

  AppState copyWith(
          {bool? loading,
          AuthSession? session,
          bool clearSession = false,
          List<AccountSummary>? accounts,
          AccountSummary? selected,
          bool clearSelected = false,
          List<OrderView>? orders,
          String? error,
          bool clearError = false}) =>
      AppState(
        loading: loading ?? this.loading,
        session: clearSession ? null : session ?? this.session,
        accounts: accounts ?? this.accounts,
        selected: clearSelected ? null : selected ?? this.selected,
        orders: orders ?? this.orders,
        error: clearError ? null : error ?? this.error,
      );
}

class AppController extends StateNotifier<AppState> {
  AppController(this.ref) : super(const AppState()) {
    bootstrap();
  }
  final Ref ref;
  Timer? _refreshTimer;
  Timer? _reconnectTimer;
  bool _foreground = true;
  FastOrderApi get api => ref.read(apiProvider);
  EventService get events => ref.read(eventProvider);

  Future<void> bootstrap() async {
    try {
      final session = await ref.read(authServiceProvider).restore();
      if (session == null) {
        state = const AppState(loading: false);
        return;
      }
      await _activate(session);
    } catch (error) {
      state = AppState(loading: false, error: error.toString());
    }
  }

  Future<void> login() async {
    state = state.copyWith(loading: true, clearError: true);
    try {
      await _activate(await ref.read(authServiceProvider).login());
    } catch (error) {
      state = state.copyWith(loading: false, error: error.toString());
    }
  }

  Future<void> _activate(AuthSession session) async {
    api.accessToken = session.accessToken;
    await ref
        .read(signerProvider)
        .ensureRegistered(AppConfig.apiBaseUrl, session.accessToken);
    state = AppState(loading: false, session: session);
    _scheduleRefresh(session);
    await reloadAccounts();
  }

  void _scheduleRefresh(AuthSession session) {
    _refreshTimer?.cancel();
    if (session.refreshToken == null || session.expiresAt == null) return;
    final delay = session.expiresAt!.difference(DateTime.now()) -
        const Duration(seconds: 45);
    _refreshTimer = Timer(delay.isNegative ? Duration.zero : delay, () async {
      try {
        final refreshed = await ref
            .read(authServiceProvider)
            .refreshSession(session.refreshToken!);
        api.accessToken = refreshed.accessToken;
        state = state.copyWith(session: refreshed, clearError: true);
        _scheduleRefresh(refreshed);
        await connectEvents();
      } catch (error) {
        state = state.copyWith(error: '登录已过期，请退出后重新登录：$error');
        await events.close();
      }
    });
  }

  Future<void> reloadAccounts() async {
    final session = state.session;
    if (session == null) return;
    try {
      final accounts = await api.accounts();
      final selected = accounts.isEmpty ? null : accounts.first;
      state = state.copyWith(
          accounts: accounts,
          selected: selected,
          clearSelected: selected == null,
          clearError: true);
      await refreshOrders();
      await connectEvents();
    } catch (error) {
      state = state.copyWith(
          loading: false, error: '设备等待管理员授权，或授权已被撤销。${error.toString()}');
    }
  }

  Future<void> select(AccountSummary account) async {
    state =
        state.copyWith(selected: account, orders: const [], clearError: true);
    await refreshOrders();
  }

  Future<void> refreshOrders() async {
    if (state.session == null) return;
    try {
      final list = await api.orders(accountRef: state.selected?.accountRef);
      state = state.copyWith(orders: list, loading: false, clearError: true);
    } catch (error) {
      state = state.copyWith(loading: false, error: error.toString());
    }
  }

  Future<OrderView> submit(
      {required String symbol,
      required String side,
      required String type,
      required int quantity,
      String? price}) async {
    final account = state.selected ?? (throw StateError('请先选择账户'));
    final ready = await api.selectSymbol(account.accountRef, symbol);
    if (!ready) throw StateError('行情尚未就绪，禁止下单');
    final order = await api.submit(
        accountRef: account.accountRef,
        symbol: symbol,
        side: side,
        orderType: type,
        quantity: quantity,
        limitPrice: price);
    _merge(order);
    return order;
  }

  Future<void> replace(OrderView order, String price) async =>
      _merge(await api.replace(order.accountRef, order.clientOrderId, price));
  Future<void> cancel(OrderView order) async =>
      _merge(await api.cancel(order.accountRef, order.clientOrderId));

  void _merge(OrderView incoming) {
    OrderView? current;
    for (final item in state.orders) {
      if (item.clientOrderId == incoming.clientOrderId) {
        current = item;
        break;
      }
    }
    if (current != null && current.version >= incoming.version) return;
    state = state.copyWith(orders: [
      incoming,
      ...state.orders
          .where((item) => item.clientOrderId != incoming.clientOrderId)
    ]);
  }

  Future<void> connectEvents() async {
    final session = state.session;
    if (session == null) return;
    await events.connect(session.accessToken, _merge, () {
      if (!_foreground || state.session == null) return;
      _reconnectTimer?.cancel();
      _reconnectTimer = Timer(const Duration(seconds: 2), () async {
        await refreshOrders();
        if (_foreground) await connectEvents();
      });
    });
  }

  Future<void> onResume() async {
    _foreground = true;
    await refreshOrders();
    await connectEvents();
  }

  Future<void> onPause() {
    _foreground = false;
    _reconnectTimer?.cancel();
    return events.close();
  }

  Future<void> logout() async {
    _refreshTimer?.cancel();
    _reconnectTimer?.cancel();
    await events.close();
    await ref.read(authServiceProvider).logout();
    state = const AppState(loading: false);
  }

  @override
  void dispose() {
    _refreshTimer?.cancel();
    _reconnectTimer?.cancel();
    events.close();
    super.dispose();
  }
}
