# FastOrder Android

Flutter 的业务与网络层可继续用于 iPhone/macOS；第一阶段只验收 Android API 26+。客户端不保存券商账户号或券商凭据，设备私钥在 Android Keystore 内生成且不可导出。交易网络超时不会自动重发。

## 初始化与构建

在安装 Flutter、Android SDK 和 JDK 17 的构建机执行 `./tool/bootstrap_android.sh`，该命令补全标准 Flutter/Gradle wrapper 文件。然后创建正式签名密钥，并把以下内容写入被 Git 忽略的 `android/key.properties`：

```properties
storeFile=/absolute/private/path/fastorder-release.jks
storePassword=...
keyAlias=fastorder
keyPassword=...
```

构建内部 LIVE APK：

```sh
flutter build apk --release \
  --dart-define=FASTORDER_API_URL=https://api.example.com \
  --dart-define=FASTORDER_OIDC_DISCOVERY_URL=https://auth.example.com/.well-known/openid-configuration \
  --dart-define=FASTORDER_OIDC_CLIENT_ID=fastorder-mobile
```

输出为 `build/app/outputs/flutter-apk/app-release.apk`。必须将 `com.fastorder.mobile:/oauthredirect` 配入 Casdoor 应用。签名私钥、密码和生成的 APK 均不得提交仓库。
