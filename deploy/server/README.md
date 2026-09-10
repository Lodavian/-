# FastOrder institutional server

This deployment is a single-node LIVE control terminal for an approved institutional proprietary-account setup. It never submits an automatic test order.

## Host prerequisites

- Hong Kong Linux x86_64 host with Docker Engine, Compose v2, OpenSSL, three DNS records, and inbound TCP 80/443.
- Copy `.env.example` to `.env`, replace all domains and limits, and pin a currently supported Casdoor image by reviewed immutable digest. Review the Casdoor/GitHub security advisories immediately before every rollout; do not use a floating tag.
- Make scripts executable: `chmod 700 prepare-secrets.sh postgres-init.sh casdoor-entrypoint.sh`.

## Bootstrap

1. Run `sudo ./prepare-secrets.sh`. Secrets are written below the ignored `secrets/` directory with private modes. Do not copy them into an image or Git.
2. Start identity infrastructure first: `docker compose up -d postgres casdoor caddy`.
3. Open the authentication domain and create organization `fastorder`, application `fastorder-mobile`, the OIDC redirect URLs, and the `fastorder-admin` role. Disable signup, social providers, password recovery, and all unused flows. Require passwords of at least 12 characters and configure login throttling plus consecutive-failure lockout. Allow the API and admin origins for OIDC/PKCE.
   Disable SAML and resource upload features for this deployment. Caddy additionally blocks the public upload-resource route.
4. Put the generated client ID in `.env`; put its secret in `secrets/fastorder/casdoor_client_secret`, then run `sudo chown 10001:10001` and `sudo chmod 600` on that file.
5. Confirm Casdoor exposes its signing keys at the configured `FASTORDER_CASDOOR_JWKS_URL`. If the deployed Casdoor version uses a different discovery endpoint, update that environment variable before starting FastOrder.
6. Build and start the terminal: `docker compose up -d --build fastorder`.
7. Verify `/livez` is 200 and `/readyz` reports the broker event channel. A restart always writes the global trade gate back to OFF.
8. Use the admin domain to create users, register discovered Webull account IDs, bind each account to exactly one user, then enable account, user, and finally global trading.

## Operations

- A trade-control interruption blocks new and replacement orders but leaves order reads and cancellation available.
- Disabling a user/device or removing an account binding revokes all access rather than acting as a trade-only interruption.
- PostgreSQL failure blocks all mutations. Casdoor failure blocks new login/refresh; locally verifiable unexpired JWTs continue to use the in-process trade-control snapshot.
- Back up PostgreSQL and the encrypted account master key together. Back up the Webull token volume as sensitive data.
- Measure `control_gate_ms` on the host under expected concurrency before enabling more than the first LIVE account; required p99 is 10 ms or less.

## LIVE acceptance

Use one account and the smallest configured limits. An authorized administrator manually performs one limit order, a price-only replacement, cancellation, and restart reconciliation. No CI, health check, migration, or smoke-test command contains a real order operation.
