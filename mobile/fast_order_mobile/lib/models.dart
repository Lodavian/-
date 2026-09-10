class AccountSummary {
  const AccountSummary(
      {required this.accountRef,
      required this.alias,
      required this.mask,
      required this.tradeEnabled});
  final String accountRef;
  final String alias;
  final String mask;
  final bool tradeEnabled;

  factory AccountSummary.fromJson(Map<String, dynamic> json) => AccountSummary(
        accountRef: json['accountRef'] as String,
        alias: json['alias'] as String,
        mask: json['accountMask'] as String,
        tradeEnabled: json['tradeEnabled'] == true,
      );
}

class OrderView {
  const OrderView({
    required this.accountRef,
    required this.clientOrderId,
    required this.status,
    required this.symbol,
    required this.side,
    required this.orderType,
    required this.quantity,
    required this.version,
    this.limitPrice,
    this.message = '',
  });
  final String accountRef;
  final String clientOrderId;
  final String status;
  final String symbol;
  final String side;
  final String orderType;
  final int quantity;
  final int version;
  final String? limitPrice;
  final String message;

  factory OrderView.fromJson(Map<String, dynamic> json) => OrderView(
        accountRef: json['accountRef'] as String,
        clientOrderId: json['clientOrderId'] as String,
        status: json['status'] as String,
        symbol: json['symbol'] as String,
        side: json['side'] as String,
        orderType: json['orderType'] as String,
        quantity: (json['quantity'] as num).toInt(),
        version: (json['version'] as num).toInt(),
        limitPrice: json['limitPrice']?.toString(),
        message: json['message']?.toString() ?? '',
      );
}
