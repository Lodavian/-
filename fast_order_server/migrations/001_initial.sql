CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE SCHEMA IF NOT EXISTS fastorder;
REVOKE ALL ON SCHEMA fastorder FROM PUBLIC;
SET search_path TO fastorder;

CREATE TABLE IF NOT EXISTS control_state (
    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    global_trade_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    version BIGINT NOT NULL DEFAULT 1,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by TEXT NOT NULL DEFAULT 'bootstrap',
    reason TEXT NOT NULL DEFAULT '服务启动后默认关闭交易'
);
INSERT INTO control_state(singleton) VALUES(TRUE) ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS app_users (
    user_id TEXT PRIMARY KEY,
    casdoor_name TEXT,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    trade_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE app_users ADD COLUMN IF NOT EXISTS casdoor_name TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS app_users_casdoor_name_unique
    ON app_users(casdoor_name) WHERE casdoor_name IS NOT NULL;

CREATE TABLE IF NOT EXISTS broker_accounts (
    account_ref UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    broker_account_cipher BYTEA NOT NULL,
    account_fingerprint TEXT,
    account_mask TEXT NOT NULL,
    alias TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    trade_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE broker_accounts ADD COLUMN IF NOT EXISTS account_fingerprint TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS broker_accounts_fingerprint_unique
    ON broker_accounts(account_fingerprint) WHERE account_fingerprint IS NOT NULL;

CREATE TABLE IF NOT EXISTS user_account_bindings (
    user_id TEXT NOT NULL REFERENCES app_users(user_id) ON DELETE CASCADE,
    account_ref UUID NOT NULL UNIQUE REFERENCES broker_accounts(account_ref) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY(user_id, account_ref)
);

CREATE TABLE IF NOT EXISTS devices (
    device_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL REFERENCES app_users(user_id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    public_key_pem TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS devices_user_idx ON devices(user_id);

CREATE TABLE IF NOT EXISTS orders (
    account_ref UUID NOT NULL REFERENCES broker_accounts(account_ref),
    client_order_id TEXT NOT NULL,
    origin_request_id UUID NOT NULL,
    user_id TEXT NOT NULL,
    device_id UUID NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    order_type TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    limit_price NUMERIC,
    reference_price NUMERIC NOT NULL,
    estimated_notional NUMERIC NOT NULL,
    status TEXT NOT NULL,
    version BIGINT NOT NULL DEFAULT 1,
    message TEXT NOT NULL DEFAULT '',
    result_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY(account_ref, client_order_id)
);
ALTER TABLE orders ADD COLUMN IF NOT EXISTS origin_request_id UUID;
CREATE INDEX IF NOT EXISTS orders_user_time_idx ON orders(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS orders_account_status_idx ON orders(account_ref, status, updated_at);

CREATE TABLE IF NOT EXISTS idempotency_keys (
    account_ref UUID NOT NULL,
    request_id UUID NOT NULL,
    payload_hash TEXT NOT NULL,
    client_order_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY(account_ref, request_id),
    FOREIGN KEY(account_ref, client_order_id) REFERENCES orders(account_ref, client_order_id)
);

CREATE TABLE IF NOT EXISTS order_events (
    event_id BIGSERIAL PRIMARY KEY,
    account_ref UUID NOT NULL,
    client_order_id TEXT NOT NULL,
    version BIGINT NOT NULL,
    status TEXT NOT NULL,
    payload JSONB NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(account_ref, client_order_id, version),
    FOREIGN KEY(account_ref, client_order_id) REFERENCES orders(account_ref, client_order_id)
);

CREATE TABLE IF NOT EXISTS audit_events (
    audit_id BIGSERIAL PRIMARY KEY,
    actor_user_id TEXT NOT NULL,
    device_id UUID,
    action TEXT NOT NULL,
    account_ref UUID,
    client_order_id TEXT,
    outcome TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS audit_time_idx ON audit_events(occurred_at DESC);

REVOKE ALL ON ALL TABLES IN SCHEMA fastorder FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA fastorder FROM PUBLIC;
