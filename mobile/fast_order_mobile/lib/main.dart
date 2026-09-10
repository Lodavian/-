import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'config.dart';
import 'models.dart';
import 'state.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  try {
    AppConfig.validate();
  } on StateError catch (error) {
    runApp(ConfigurationErrorApp(message: error.message.toString()));
    return;
  }
  runApp(const ProviderScope(child: FastOrderApp()));
}

class ConfigurationErrorApp extends StatelessWidget {
  const ConfigurationErrorApp({super.key, required this.message});

  final String message;

  @override
  Widget build(BuildContext context) => MaterialApp(
        debugShowCheckedModeBanner: false,
        title: '极速下单',
        theme: ThemeData(
          useMaterial3: true,
          brightness: Brightness.dark,
          colorScheme: ColorScheme.fromSeed(
            seedColor: const Color(0xFF76E4F7),
            brightness: Brightness.dark,
          ),
          scaffoldBackgroundColor: const Color(0xFF08111D),
        ),
        home: Scaffold(
          body: Container(
            decoration: const BoxDecoration(
              gradient: RadialGradient(
                center: Alignment.topRight,
                radius: 1.3,
                colors: [Color(0xFF174C69), Color(0xFF08111D)],
              ),
            ),
            child: SafeArea(
              child: Center(
                child: Padding(
                  padding: const EdgeInsets.all(24),
                  child: ConstrainedBox(
                    constraints: const BoxConstraints(maxWidth: 420),
                    child: Card(
                      color: const Color(0xCC13263A),
                      child: Padding(
                        padding: const EdgeInsets.all(28),
                        child: Column(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            const Icon(
                              Icons.dns_outlined,
                              size: 54,
                              color: Color(0xFF76E4F7),
                            ),
                            const SizedBox(height: 18),
                            Text(
                              '服务器尚未配置',
                              style: Theme.of(context).textTheme.headlineSmall,
                            ),
                            const SizedBox(height: 12),
                            const Text(
                              '当前安装包仅用于设备兼容性检查。配置 API 和登录服务后，请重新安装正式版本。',
                              textAlign: TextAlign.center,
                            ),
                            const SizedBox(height: 16),
                            Text(
                              message,
                              textAlign: TextAlign.center,
                              style: Theme.of(context).textTheme.bodySmall,
                            ),
                          ],
                        ),
                      ),
                    ),
                  ),
                ),
              ),
            ),
          ),
        ),
      );
}

class FastOrderApp extends StatelessWidget {
  const FastOrderApp({super.key});
  @override
  Widget build(BuildContext context) => MaterialApp(
        debugShowCheckedModeBanner: false,
        title: '极速下单',
        theme: ThemeData(
          useMaterial3: true,
          brightness: Brightness.dark,
          colorScheme: ColorScheme.fromSeed(
              seedColor: const Color(0xFF76E4F7), brightness: Brightness.dark),
          scaffoldBackgroundColor: const Color(0xFF08111D),
          cardTheme: const CardThemeData(
              color: Color(0xCC13263A), elevation: 0, margin: EdgeInsets.zero),
          inputDecorationTheme: const InputDecorationTheme(
              filled: true,
              fillColor: Color(0x99162B40),
              border: OutlineInputBorder()),
        ),
        home: const RootScreen(),
      );
}

class RootScreen extends ConsumerStatefulWidget {
  const RootScreen({super.key});
  @override
  ConsumerState<RootScreen> createState() => _RootScreenState();
}

class _RootScreenState extends ConsumerState<RootScreen>
    with WidgetsBindingObserver {
  int page = 0;
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState lifecycle) {
    final controller = ref.read(appStateProvider.notifier);
    if (lifecycle == AppLifecycleState.resumed) {
      controller.onResume();
    }
    if (lifecycle == AppLifecycleState.paused ||
        lifecycle == AppLifecycleState.detached) {
      controller.onPause();
    }
  }

  @override
  Widget build(BuildContext context) {
    final state = ref.watch(appStateProvider);
    if (state.loading) {
      return const Scaffold(body: Center(child: CircularProgressIndicator()));
    }
    if (state.session == null) return LoginScreen(error: state.error);
    return Scaffold(
      appBar: AppBar(
        title: const Text('极速下单'),
        actions: [
          IconButton(
              onPressed: ref.read(appStateProvider.notifier).logout,
              icon: const Icon(Icons.logout),
              tooltip: '退出登录')
        ],
      ),
      body: Container(
        decoration: const BoxDecoration(
            gradient: LinearGradient(
                colors: [Color(0xFF08111D), Color(0xFF102943)],
                begin: Alignment.topLeft,
                end: Alignment.bottomRight)),
        child: SafeArea(
            child: Column(children: [
          if (state.error != null)
            MaterialBanner(content: Text(state.error!), actions: [
              TextButton(
                  onPressed: ref.read(appStateProvider.notifier).reloadAccounts,
                  child: const Text('重新检查授权'))
            ]),
          AccountPicker(accounts: state.accounts, selected: state.selected),
          Expanded(
              child: IndexedStack(
                  index: page, children: const [OrderTicket(), OrderList()])),
        ])),
      ),
      bottomNavigationBar: NavigationBar(
          selectedIndex: page,
          onDestinationSelected: (value) => setState(() => page = value),
          destinations: const [
            NavigationDestination(
                icon: Icon(Icons.bolt_outlined),
                selectedIcon: Icon(Icons.bolt),
                label: '下单'),
            NavigationDestination(
                icon: Icon(Icons.receipt_long_outlined),
                selectedIcon: Icon(Icons.receipt_long),
                label: '订单'),
          ]),
    );
  }
}

class LoginScreen extends ConsumerWidget {
  const LoginScreen({super.key, this.error});
  final String? error;
  @override
  Widget build(BuildContext context, WidgetRef ref) => Scaffold(
        body: Container(
          decoration: const BoxDecoration(
              gradient: RadialGradient(
                  center: Alignment.topRight,
                  radius: 1.3,
                  colors: [Color(0xFF174C69), Color(0xFF08111D)])),
          child: SafeArea(
              child: Center(
                  child: Padding(
            padding: const EdgeInsets.all(24),
            child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 420),
                child: Card(
                    child: Padding(
                  padding: const EdgeInsets.all(28),
                  child: Column(
                      mainAxisSize: MainAxisSize.min,
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        const Icon(Icons.shield_outlined,
                            size: 54, color: Color(0xFF76E4F7)),
                        const SizedBox(height: 18),
                        Text('机构交易终端',
                            textAlign: TextAlign.center,
                            style: Theme.of(context).textTheme.headlineSmall),
                        const SizedBox(height: 8),
                        const Text('登录后仅可操作管理员分配的自营账户',
                            textAlign: TextAlign.center),
                        if (error != null) ...[
                          const SizedBox(height: 16),
                          Text(error!,
                              style: TextStyle(
                                  color: Theme.of(context).colorScheme.error))
                        ],
                        const SizedBox(height: 24),
                        FilledButton.icon(
                            onPressed:
                                ref.read(appStateProvider.notifier).login,
                            icon: const Icon(Icons.login),
                            label: const Padding(
                                padding: EdgeInsets.symmetric(vertical: 14),
                                child: Text('安全登录'))),
                      ]),
                ))),
          ))),
        ),
      );
}

class AccountPicker extends ConsumerWidget {
  const AccountPicker(
      {super.key, required this.accounts, required this.selected});
  final List<AccountSummary> accounts;
  final AccountSummary? selected;
  @override
  Widget build(BuildContext context, WidgetRef ref) => Padding(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 10),
        child: DropdownButtonFormField<String>(
          initialValue: selected?.accountRef,
          decoration: const InputDecoration(
              labelText: '交易账户', prefixIcon: Icon(Icons.account_balance)),
          items: accounts
              .map((account) => DropdownMenuItem(
                  value: account.accountRef,
                  child: Text(
                      '${account.alias}  ${account.mask}${account.tradeEnabled ? '' : ' · 已暂停'}')))
              .toList(),
          onChanged: (value) {
            if (value == null) return;
            ref.read(appStateProvider.notifier).select(
                accounts.firstWhere((item) => item.accountRef == value));
          },
        ),
      );
}

class OrderTicket extends ConsumerStatefulWidget {
  const OrderTicket({super.key});
  @override
  ConsumerState<OrderTicket> createState() => _OrderTicketState();
}

class _OrderTicketState extends ConsumerState<OrderTicket> {
  final symbol = TextEditingController();
  final quantity = TextEditingController();
  final price = TextEditingController();
  String side = 'BUY';
  String type = 'LIMIT';
  bool busy = false;
  @override
  void dispose() {
    symbol.dispose();
    quantity.dispose();
    price.dispose();
    super.dispose();
  }

  Future<void> send() async {
    final qty = int.tryParse(quantity.text);
    if (qty == null ||
        qty <= 0 ||
        symbol.text.trim().isEmpty ||
        (type == 'LIMIT' && price.text.trim().isEmpty)) {
      ScaffoldMessenger.of(context)
          .showSnackBar(const SnackBar(content: Text('请填写有效股票、整股数量和限价')));
      return;
    }
    final confirmed = await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
              title: const Text('确认提交实盘订单'),
              content: Text(
                  '$side ${symbol.text.trim().toUpperCase()} × $qty\n$type${type == 'LIMIT' ? ' @ ${price.text}' : ''}\nDAY · CORE'),
              actions: [
                TextButton(
                    onPressed: () => Navigator.pop(context, false),
                    child: const Text('返回')),
                FilledButton(
                    onPressed: () => Navigator.pop(context, true),
                    child: const Text('确认提交'))
              ],
            ));
    if (confirmed != true) return;
    setState(() => busy = true);
    try {
      final result = await ref.read(appStateProvider.notifier).submit(
          symbol: symbol.text.trim(),
          side: side,
          type: type,
          quantity: qty,
          price: price.text.trim());
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(
            content: Text('${result.clientOrderId} · ${result.status}')));
      }
    } catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(error.toString())));
      }
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final enabled = ref.watch(appStateProvider).selected?.tradeEnabled == true;
    return ListView(
        padding: const EdgeInsets.fromLTRB(16, 4, 16, 24),
        children: [
          Card(
              child: Padding(
                  padding: const EdgeInsets.all(18),
                  child: Column(children: [
                    SegmentedButton<String>(
                        segments: const [
                          ButtonSegment(value: 'BUY', label: Text('买入')),
                          ButtonSegment(value: 'SELL', label: Text('卖出'))
                        ],
                        selected: {
                          side
                        },
                        onSelectionChanged: (value) =>
                            setState(() => side = value.first)),
                    const SizedBox(height: 16),
                    TextField(
                        controller: symbol,
                        textCapitalization: TextCapitalization.characters,
                        decoration: const InputDecoration(
                            labelText: '美股代码', hintText: 'AAPL')),
                    const SizedBox(height: 12),
                    TextField(
                        controller: quantity,
                        keyboardType: TextInputType.number,
                        decoration: const InputDecoration(labelText: '整股数量')),
                    const SizedBox(height: 12),
                    SegmentedButton<String>(
                        segments: const [
                          ButtonSegment(value: 'LIMIT', label: Text('限价')),
                          ButtonSegment(value: 'MARKET', label: Text('市价'))
                        ],
                        selected: {
                          type
                        },
                        onSelectionChanged: (value) =>
                            setState(() => type = value.first)),
                    if (type == 'LIMIT') ...[
                      const SizedBox(height: 12),
                      TextField(
                          controller: price,
                          keyboardType: const TextInputType.numberWithOptions(
                              decimal: true),
                          decoration:
                              const InputDecoration(labelText: '限价 USD'))
                    ],
                    const SizedBox(height: 16),
                    const Align(
                        alignment: Alignment.centerLeft,
                        child: Text('固定规则：DAY · CORE · 美股整股')),
                    const SizedBox(height: 16),
                    SizedBox(
                        width: double.infinity,
                        child: FilledButton(
                            onPressed: enabled && !busy ? send : null,
                            child: Padding(
                                padding:
                                    const EdgeInsets.symmetric(vertical: 14),
                                child: Text(enabled
                                    ? (busy ? '提交中…' : '提交实盘订单')
                                    : '账户交易已暂停')))),
                  ]))),
        ]);
  }
}

class OrderList extends ConsumerWidget {
  const OrderList({super.key});
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final orders = ref.watch(appStateProvider).orders;
    if (orders.isEmpty) {
      return RefreshIndicator(
          onRefresh: ref.read(appStateProvider.notifier).refreshOrders,
          child: ListView(children: const [
            SizedBox(height: 150),
            Center(child: Text('暂无订单'))
          ]));
    }
    return RefreshIndicator(
      onRefresh: ref.read(appStateProvider.notifier).refreshOrders,
      child: ListView.separated(
        padding: const EdgeInsets.fromLTRB(16, 4, 16, 24),
        itemCount: orders.length,
        separatorBuilder: (_, __) => const SizedBox(height: 10),
        itemBuilder: (context, index) {
          final order = orders[index];
          return Card(
              child: ListTile(
            contentPadding:
                const EdgeInsets.symmetric(horizontal: 18, vertical: 8),
            title: Text('${order.side} ${order.symbol} × ${order.quantity}'),
            subtitle: Text(
                '${order.orderType}${order.limitPrice == null ? '' : ' @ ${order.limitPrice}'}\n${order.clientOrderId}'),
            trailing: Text(order.status, textAlign: TextAlign.end),
            onTap: () => showOrder(context, ref, order),
          ));
        },
      ),
    );
  }

  Future<void> showOrder(
      BuildContext context, WidgetRef ref, OrderView order) async {
    final replaceable = order.orderType == 'LIMIT' &&
        {'ACKNOWLEDGED', 'SUBMITTED', 'OPEN', 'PARTIAL_FILLED', 'REPLACED'}
            .contains(order.status);
    final cancellable = {
      'ACKNOWLEDGED',
      'SUBMITTED',
      'OPEN',
      'PARTIAL_FILLED',
      'REPLACED',
      'CANCEL_REQUESTED'
    }.contains(order.status);
    await showModalBottomSheet<void>(
        context: context,
        showDragHandle: true,
        builder: (context) => SafeArea(
                child: Padding(
              padding: const EdgeInsets.fromLTRB(24, 8, 24, 24),
              child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Text('${order.side} ${order.symbol}',
                        style: Theme.of(context).textTheme.headlineSmall),
                    const SizedBox(height: 8),
                    Text(
                        '${order.quantity} 股 · ${order.orderType}${order.limitPrice == null ? '' : ' @ ${order.limitPrice}'}\n${order.status} · 版本 ${order.version}\n${order.clientOrderId}${order.message.isEmpty ? '' : '\n${order.message}'}'),
                    const SizedBox(height: 18),
                    if (replaceable)
                      OutlinedButton(
                          onPressed: () async {
                            final controller =
                                TextEditingController(text: order.limitPrice);
                            final value = await showDialog<String>(
                                context: context,
                                builder: (dialogContext) => AlertDialog(
                                        title: const Text('修改限价'),
                                        content: TextField(
                                            controller: controller,
                                            keyboardType: const TextInputType
                                                .numberWithOptions(
                                                decimal: true)),
                                        actions: [
                                          TextButton(
                                              onPressed: () =>
                                                  Navigator.pop(dialogContext),
                                              child: const Text('取消')),
                                          FilledButton(
                                              onPressed: () => Navigator.pop(
                                                  dialogContext,
                                                  controller.text),
                                              child: const Text('确认改单'))
                                        ]));
                            controller.dispose();
                            if (value != null && value.isNotEmpty) {
                              await ref
                                  .read(appStateProvider.notifier)
                                  .replace(order, value);
                            }
                            if (context.mounted) Navigator.pop(context);
                          },
                          child: const Text('修改限价')),
                    if (cancellable)
                      FilledButton.tonal(
                          onPressed: () async {
                            await ref
                                .read(appStateProvider.notifier)
                                .cancel(order);
                            if (context.mounted) Navigator.pop(context);
                          },
                          child: const Text('撤销订单')),
                  ]),
            )));
  }
}
