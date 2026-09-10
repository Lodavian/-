package com.fastorder.mobile

import android.os.Bundle
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.MessageDigest
import java.security.Signature
import java.security.spec.ECGenParameterSpec

class MainActivity : FlutterActivity() {
    private val channelName = "com.fastorder.mobile/device_key"
    private val alias = "fastorder-device-signing-v1"

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, channelName).setMethodCallHandler { call, result ->
            try {
                when (call.method) {
                    "ensureKey" -> result.success(publicKeyPem())
                    "sign" -> result.success(sign(call.argument<String>("message") ?: error("message missing")))
                    "sha256" -> result.success(sha256(call.argument<ByteArray>("bytes") ?: byteArrayOf()))
                    else -> result.notImplemented()
                }
            } catch (error: Exception) {
                result.error("KEYSTORE_ERROR", "设备安全密钥操作失败", error.javaClass.simpleName)
            }
        }
    }

    private fun keyStore(): KeyStore = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }

    private fun ensureKey() {
        if (keyStore().containsAlias(alias)) return
        val generator = KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, "AndroidKeyStore")
        generator.initialize(
            KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_SIGN)
                .setAlgorithmParameterSpec(ECGenParameterSpec("secp256r1"))
                .setDigests(KeyProperties.DIGEST_SHA256)
                .setUserAuthenticationRequired(false)
                .build()
        )
        generator.generateKeyPair()
    }

    private fun publicKeyPem(): String {
        ensureKey()
        val encoded = Base64.encodeToString(keyStore().getCertificate(alias).publicKey.encoded, Base64.NO_WRAP)
        return "-----BEGIN PUBLIC KEY-----\n" + encoded.chunked(64).joinToString("\n") + "\n-----END PUBLIC KEY-----\n"
    }

    private fun sign(message: String): String {
        ensureKey()
        val privateKey = keyStore().getKey(alias, null)
        val signer = Signature.getInstance("SHA256withECDSA")
        signer.initSign(privateKey as java.security.PrivateKey)
        signer.update(message.toByteArray(Charsets.UTF_8))
        return Base64.encodeToString(signer.sign(), Base64.URL_SAFE or Base64.NO_WRAP or Base64.NO_PADDING)
    }

    private fun sha256(bytes: ByteArray): String = MessageDigest.getInstance("SHA-256")
        .digest(bytes).joinToString("") { (it.toInt() and 0xff).toString(16).padStart(2, '0') }
}
