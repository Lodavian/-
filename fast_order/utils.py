from __future__ import annotations

import json
import re
from typing import Any


SYMBOL_PATTERN = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
INSTRUMENT_QUERY_PATTERN = re.compile(r"^[A-Z0-9.\-& ]{1,40}$")
SENSITIVE_KEYS = {
    "app_key",
    "app_secret",
    "client_secret",
    "access_token",
    "refresh_token",
    "token",
    "authorization",
    "signature",
    "x-app-key",
    "x-access-token",
}
SENSITIVE_TEXT_PATTERN = re.compile(
    r"(?i)\b(authorization|x-access-token|access_token|refresh_token|token|"
    r"x-app-key|app_key|app_secret|client_secret|signature)\b"
    r"(\s*[:=]\s*)(?:bearer\s+)?[\"']?[^,\s}\"']+"
)


def normalize_symbol(value: Any) -> str:
    symbol = str(value or "").strip().upper()
    if not SYMBOL_PATTERN.fullmatch(symbol):
        raise ValueError("股票代码必须是 1-10 位美股代码")
    return symbol


def normalize_instrument_query(value: Any) -> str:
    query = " ".join(str(value or "").strip().upper().split())
    if not INSTRUMENT_QUERY_PATTERN.fullmatch(query):
        raise ValueError("搜索内容只能包含股票代码、公司名、空格及 . - &")
    return query


def search_instruments(rows: list[dict[str, Any]], query: str, limit: int = 8) -> list[dict[str, str]]:
    needle = normalize_instrument_query(query)
    matches: list[tuple[tuple[int, int, str, str], dict[str, str]]] = []
    for row in rows:
        symbol = str(row.get("symbol") or "").strip().upper()
        name = str(row.get("name") or "").strip()
        name_upper = name.upper()
        if not symbol or not name:
            continue
        if symbol == needle:
            match_rank = 0
        elif symbol.startswith(needle):
            match_rank = 1
        elif name_upper.startswith(needle):
            match_rank = 2
        elif needle in symbol:
            match_rank = 3
        elif needle in name_upper:
            match_rank = 4
        else:
            continue
        status = str(row.get("status") or "NT").strip().upper()
        item = {
            "symbol": symbol,
            "name": name,
            "exchange": str(row.get("exchange_code") or "").strip().upper(),
            "status": status,
            "subCategory": str(row.get("sub_category") or "").strip().upper(),
        }
        status_rank = {"OC": 0, "CO": 1, "NT": 2}.get(status, 3)
        matches.append(((match_rank, status_rank, symbol, name_upper), item))
    matches.sort(key=lambda pair: pair[0])
    return [item for _, item in matches[: max(1, min(limit, 12))]]


def response_json(response: Any) -> Any:
    if response is None:
        return None
    if isinstance(response, (dict, list, str, int, float, bool)):
        return response
    json_method = getattr(response, "json", None)
    if callable(json_method):
        try:
            return json_method()
        except Exception:
            pass
    return str(response)


def response_ok(response: Any) -> bool:
    status = int(getattr(response, "status_code", 200) or 200)
    payload = response_json(response)
    return status < 400 and not (isinstance(payload, dict) and (payload.get("error") or payload.get("error_code")))


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): ("***" if str(key).lower() in SENSITIVE_KEYS else redact(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def safe_json(value: Any) -> str:
    return json.dumps(redact(value), ensure_ascii=False, default=str, separators=(",", ":"))


def redact_text(value: Any, limit: int = 240) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ")
    return SENSITIVE_TEXT_PATTERN.sub(r"\1\2***", text)[:limit]


def rows_from(value: Any) -> list[dict[str, Any]]:
    value = response_json(value)
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in ("data", "result", "items", "accounts", "positions", "orders", "list"):
            nested = value.get(key)
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, dict)]
    return []


def mask_account_id(account_id: str) -> str:
    if len(account_id) <= 6:
        return "***"
    return f"{account_id[:3]}***{account_id[-3:]}"


def select_account_id(accounts: list[dict[str, Any]], configured: str, hint: str = "") -> str:
    ids = [str(item.get("account_id") or item.get("id") or "") for item in accounts]
    ids = [item for item in ids if item]
    if configured:
        if configured not in ids:
            raise ValueError("配置的 Account ID 不在券商API返回的账户列表中")
        return configured
    if hint:
        matches = [account_id for account_id in ids if mask_account_id(account_id).upper() == hint.upper()]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise ValueError("配置的账户掩码提示不匹配券商API返回的账户")
        raise ValueError("账户掩码提示匹配多个账户，请配置完整 Account ID")
    if len(ids) == 1:
        return ids[0]
    if not ids:
        raise RuntimeError("券商API未返回可用交易账户")
    raise ValueError("凭据返回多个账户，请在 .env 配置账户提示或 Account ID")


def protobuf_to_dict(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): protobuf_to_dict(item) for key, item in value.items()}
    if isinstance(value, list):
        return [protobuf_to_dict(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "DESCRIPTOR"):
        try:
            from google.protobuf.json_format import MessageToDict

            return MessageToDict(value, preserving_proto_field_name=True)
        except Exception:
            pass
    if isinstance(value, tuple):
        return [protobuf_to_dict(item) for item in value]
    if hasattr(value, "__dict__"):
        return {key: protobuf_to_dict(item) for key, item in vars(value).items() if not key.startswith("_")}
    return str(value)


def available_position(rows: list[dict[str, Any]], symbol: str) -> float:
    target = symbol.upper()
    for row in rows:
        if find_symbol(row) != target:
            continue
        available = find_number(
            row,
            {"available_quantity", "available_qty", "sellable_quantity", "can_sell_quantity"},
        )
        if available is None:
            available = find_number(row, {"quantity", "qty", "position_quantity"})
        return max(0.0, available or 0.0)
    return 0.0


def find_number(value: Any, keys: set[str]) -> float | None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in keys:
                try:
                    return float(str(item).replace(",", ""))
                except (TypeError, ValueError):
                    pass
        for item in value.values():
            found = find_number(item, keys)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = find_number(item, keys)
            if found is not None:
                return found
    return None


def find_symbol(value: Any) -> str:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in {"symbol", "ticker"} and item:
                return str(item).upper()
        for item in value.values():
            found = find_symbol(item)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = find_symbol(item)
            if found:
                return found
    return ""
