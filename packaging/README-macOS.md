# FastOrder 1.0.0 macOS arm64 internal build

This lane creates an ad-hoc signed, non-notarized `FastOrder.app` for local use
on Apple Silicon. It embeds the unchanged LIVE `config/production.json` policy.
It is not a public distribution artifact.

## Prerequisites

- macOS 15 or newer on Apple Silicon (`arm64`)
- native arm64 CPython 3.11.9
- Xcode Command Line Tools (`xcode-select --install`)
- internet access to obtain the exact pinned macOS wheels and paho-mqtt sdist
- a clean Git working tree

Set `FAST_ORDER_PYTHON` if `python3.11` is not CPython 3.11.9, then run:

```bash
bash packaging/build_macos.sh --clean
```

The verified result is `build/macos-arm64/FastOrder.app`. Double-clicking the
app opens Terminal and the default browser. Use `Ctrl+C` in that Terminal to
run the normal aiohttp, streaming, event-client, and SQLite cleanup path.

Runtime data is created only below
`~/Library/Application Support/FastOrder/`. The build and smoke test must not
copy Windows credentials, profiles, broker tokens, logs, or order databases.

Because this lane uses only an ad-hoc signature, build it on the Mac where it
will be used. Developer ID signing, notarization, DMG creation, Intel support,
and public distribution are intentionally out of scope.

## Manual acceptance

After the automated build succeeds:

1. Double-click `FastOrder.app`; verify Terminal and the default browser open.
2. Double-click it again; verify the existing page opens and no second service remains.
3. Bind the intended Webull OpenAPI account and verify credentials appear under
   service `极速下单.WebullOpenAPI` in the login Keychain.
4. Do not place an order as part of packaging QA. Confirm LIVE remains locked
   until the existing trading switch and risk gates are satisfied.
5. Press `Ctrl+C` and verify the process exits cleanly.
