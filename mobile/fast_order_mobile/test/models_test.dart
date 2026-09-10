import 'package:fast_order_mobile/models.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('account summary parses the server contract', () {
    final account = AccountSummary.fromJson({
      'accountRef': 'account-ref',
      'alias': '机构账户',
      'accountMask': '****1234',
      'tradeEnabled': true,
    });

    expect(account.accountRef, 'account-ref');
    expect(account.alias, '机构账户');
    expect(account.mask, '****1234');
    expect(account.tradeEnabled, isTrue);
  });
}
