#!/usr/bin/env python3
"""Watch a ticker and alert when price touches the 10-day moving average via Shinhan OpenAPI."""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import logging
import os
import time
from typing import Any, Dict, Iterable, List, Optional

import requests

try:
    # Optional convenience; the script still works without python-dotenv.
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - best effort import
    load_dotenv = None

if load_dotenv:
    load_dotenv()


def _pick_first(mapping: Dict[str, Any], keys: Iterable[str]) -> Optional[float]:
    """Return the first existing numeric field."""
    for key in keys:
        if key in mapping and mapping[key] not in (None, "", "0.00"):
            try:
                return float(mapping[key])
            except (TypeError, ValueError):
                continue
    return None


def _pick_str(mapping: Dict[str, Any], keys: Iterable[str]) -> str:
    for key in keys:
        if key in mapping and mapping[key]:
            return str(mapping[key])
    return ""


def _parse_extra_headers(raw: Optional[str]) -> Dict[str, str]:
    if not raw:
        return {}
    headers: Dict[str, str] = {}
    for part in raw.split(","):
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        headers[name.strip()] = value.strip()
    return headers


@dataclasses.dataclass
class Candle:
    close: float
    high: float
    low: float
    timestamp: str = ""


@dataclasses.dataclass
class Quote:
    price: float
    high: Optional[float] = None
    low: Optional[float] = None
    raw: Optional[Dict[str, Any]] = None


class ShinhanClient:
    """Small wrapper around Shinhan OpenAPI endpoints needed for price monitoring."""

    def __init__(
        self,
        base_url: str,
        app_key: str,
        app_secret: str,
        token_path: str = "/oauth2/token",
        candle_path: str = "/v1/stock/candles",
        quote_path: str = "/v1/stock/quotes",
        timeout: float = 10.0,
        include_app_headers: bool = True,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.app_key = app_key
        self.app_secret = app_secret
        self.token_path = token_path
        self.candle_path = candle_path
        self.quote_path = quote_path
        self.timeout = timeout
        self.include_app_headers = include_app_headers
        self.extra_headers = extra_headers or {}
        self.session = requests.Session()
        self._token: Optional[str] = None
        self._token_expiry: float = 0.0

    def authenticate(self) -> str:
        """Fetch an access token. Adjust payload/paths if your tenant differs."""
        url = self._url(self.token_path)
        payload = {
            "grant_type": "client_credentials",
            "client_id": self.app_key,
            "client_secret": self.app_secret,
        }
        headers = {"Content-Type": "application/json"}
        headers.update(self.extra_headers)
        resp = self.session.post(url, json=payload, headers=headers, timeout=self.timeout)
        self._raise_for_status(resp, "token request")

        data = self._safe_json(resp)
        access_token = data.get("access_token") or data.get("data", {}).get("access_token")
        expires_in = data.get("expires_in") or data.get("data", {}).get("expires_in") or 0
        if not access_token:
            raise RuntimeError(
                "Could not parse access_token from token response. "
                "Check token_path/payload with Shinhan OpenAPI docs."
            )
        self._token = access_token
        # Keep a small buffer before expiry to avoid 401s.
        self._token_expiry = time.time() + float(expires_in) - 30
        logging.info("Authenticated; token expires in %s seconds", expires_in or "unknown")
        return self._token

    def _ensure_token(self) -> None:
        if not self._token or time.time() >= self._token_expiry:
            self.authenticate()

    def fetch_daily_candles(self, symbol: str, count: int = 20) -> List[Candle]:
        self._ensure_token()
        url = self._url(self.candle_path)
        params = {"symbol": symbol, "count": count, "interval": "D"}
        headers = self._auth_headers()
        resp = self.session.get(url, params=params, headers=headers, timeout=self.timeout)
        self._raise_for_status(resp, "candle request")
        data = self._safe_json(resp)

        raw_list: Any = data.get("output") or data.get("items") or data.get("data") or data
        if isinstance(raw_list, dict):
            for key in ("candles", "list", "response"):
                if key in raw_list:
                    raw_list = raw_list[key]
                    break
        if not isinstance(raw_list, list):
            raise RuntimeError("Could not parse candle list. Update candle_path/parse logic.")

        candles: List[Candle] = []
        for item in raw_list:
            if not isinstance(item, dict):
                continue
            close = _pick_first(
                item,
                ("close", "stck_clpr", "tradePrice", "closingPrice", "prpr", "c", "tp"),
            )
            high = _pick_first(item, ("high", "stck_hgpr", "highPrice", "hipr", "h"))
            low = _pick_first(item, ("low", "stck_lwpr", "lowPrice", "lopr", "l"))
            ts = _pick_str(
                item,
                ("date", "datetime", "dateTime", "stck_bsop_date", "candleTime", "xymd"),
            )
            if close is None:
                continue
            candles.append(
                Candle(close=close, high=high or close, low=low or close, timestamp=ts)
            )

        candles.sort(key=lambda c: c.timestamp or "")
        return candles

    def fetch_quote(self, symbol: str) -> Quote:
        self._ensure_token()
        url = self._url(self.quote_path)
        params = {"symbol": symbol}
        headers = self._auth_headers()
        resp = self.session.get(url, params=params, headers=headers, timeout=self.timeout)
        self._raise_for_status(resp, "quote request")
        data = self._safe_json(resp)
        payload: Any = data.get("output") or data.get("data") or data
        if isinstance(payload, list):
            payload = payload[0] if payload else {}
        if not isinstance(payload, dict):
            payload = {}

        price = _pick_first(payload, ("price", "stck_prpr", "tradePrice", "ltp", "appr_price"))
        high = _pick_first(payload, ("high", "stck_hgpr", "highPrice", "hipr"))
        low = _pick_first(payload, ("low", "stck_lwpr", "lowPrice", "lopr"))
        if price is None:
            raise RuntimeError("Could not parse price from quote response.")
        return Quote(price=price, high=high, low=low, raw=payload)

    def _auth_headers(self) -> Dict[str, str]:
        if not self._token:
            raise RuntimeError("Token not available; call authenticate() first.")
        headers = {
            "Authorization": f"Bearer {self._token}",
        }
        if self.include_app_headers:
            headers["appkey"] = self.app_key
            headers["appsecret"] = self.app_secret
        headers.update(self.extra_headers)
        return headers

    def _url(self, path: str) -> str:
        if path.startswith("http"):
            return path
        return f"{self.base_url}{path}"

    @staticmethod
    def _safe_json(resp: requests.Response) -> Dict[str, Any]:
        try:
            return resp.json()
        except json.JSONDecodeError as exc:  # pragma: no cover - defensive path
            raise RuntimeError(f"Invalid JSON in response: {exc}") from exc

    @staticmethod
    def _raise_for_status(resp: requests.Response, context: str) -> None:
        try:
            resp.raise_for_status()
        except requests.HTTPError as exc:
            raise RuntimeError(f"{context} failed: {exc} | body={resp.text}") from exc


def compute_sma(candles: List[Candle], window: int = 10) -> float:
    if len(candles) < window:
        raise ValueError(f"Need at least {window} candles; got {len(candles)}")
    closes = [c.close for c in candles][-window:]
    return sum(closes) / window


def touched_ma(
    ma_value: float,
    price: float,
    day_low: Optional[float],
    day_high: Optional[float],
    tolerance: float,
    tolerance_pct: float,
) -> bool:
    threshold = tolerance
    if threshold == 0 and tolerance_pct > 0:
        threshold = ma_value * (tolerance_pct / 100)
    if day_low is not None and day_high is not None:
        if (day_low - threshold) <= ma_value <= (day_high + threshold):
            return True
    return abs(price - ma_value) <= threshold


def post_alert(message: str, webhook_url: Optional[str]) -> None:
    logging.info(message)
    if webhook_url:
        try:
            resp = requests.post(webhook_url, json={"text": message}, timeout=5)
            if resp.status_code >= 400:
                logging.warning("Webhook returned %s: %s", resp.status_code, resp.text)
        except Exception as exc:  # pragma: no cover - best effort
            logging.warning("Webhook failed: %s", exc)


def monitor(
    client: ShinhanClient,
    symbol: str,
    poll_interval: float = 30.0,
    ma_window: int = 10,
    lookback: int = 20,
    tolerance: float = 0.0,
    tolerance_pct: float = 0.0,
    webhook_url: Optional[str] = None,
    recalc_minutes: float = 30.0,
    cooldown_seconds: float = 300.0,
    one_shot: bool = False,
) -> None:
    last_alert_ts: float = 0.0
    last_recalc: float = 0.0
    ma_value: Optional[float] = None

    while True:
        now = time.time()
        if ma_value is None or (now - last_recalc) >= recalc_minutes * 60:
            candles = client.fetch_daily_candles(symbol, count=lookback)
            ma_value = compute_sma(candles, window=ma_window)
            last_recalc = now
            logging.info("Recomputed %s-day MA = %.3f using %d candles", ma_window, ma_value, len(candles))

        quote = client.fetch_quote(symbol)
        if ma_value is None:
            raise RuntimeError("MA value not computed.")

        if touched_ma(ma_value, quote.price, quote.low, quote.high, tolerance, tolerance_pct):
            if (now - last_alert_ts) >= cooldown_seconds:
                stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                post_alert(
                    f"[{stamp}] {symbol} touched {ma_window}D MA ({ma_value:.3f}) "
                    f"| price={quote.price:.3f} high={quote.high} low={quote.low}",
                    webhook_url,
                )
                last_alert_ts = now
                if one_shot:
                    return
        if one_shot:
            return
        time.sleep(poll_interval)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Alert when price touches the 10-day MA via Shinhan OpenAPI.")
    parser.add_argument("--symbol", required=True, help="Ticker code (e.g., 005930).")
    parser.add_argument("--poll-interval", type=float, default=float(os.getenv("POLL_INTERVAL", 30)), help="Seconds between quote polls.")
    parser.add_argument("--tolerance", type=float, default=float(os.getenv("MA_TOLERANCE", 0)), help="Absolute tolerance in price units.")
    parser.add_argument("--tolerance-pct", type=float, default=float(os.getenv("MA_TOLERANCE_PCT", 0)), help="Tolerance as percent of MA.")
    parser.add_argument("--lookback", type=int, default=int(os.getenv("LOOKBACK_DAYS", 20)), help="Number of daily candles to request.")
    parser.add_argument("--recalc-minutes", type=float, default=float(os.getenv("RECALC_MINUTES", 30)), help="How often to refetch candles/MA.")
    parser.add_argument("--cooldown-seconds", type=float, default=float(os.getenv("ALERT_COOLDOWN", 300)), help="Minimum seconds between alerts.")
    parser.add_argument("--one-shot", action="store_true", help="Run a single check and exit.")
    parser.add_argument("--base-url", default=os.getenv("SHINHAN_BASE_URL", "https://openapi.shinhan.com"), help="API base URL.")
    parser.add_argument("--token-path", default=os.getenv("SHINHAN_TOKEN_PATH", "/oauth2/token"), help="Token endpoint path.")
    parser.add_argument("--candle-path", default=os.getenv("SHINHAN_CANDLE_PATH", "/v1/stock/candles"), help="Daily candle endpoint path.")
    parser.add_argument("--quote-path", default=os.getenv("SHINHAN_QUOTE_PATH", "/v1/stock/quotes"), help="Quote endpoint path.")
    parser.add_argument("--app-key", default=os.getenv("SHINHAN_APP_KEY"), help="Shinhan OpenAPI app key/client id.")
    parser.add_argument("--app-secret", default=os.getenv("SHINHAN_APP_SECRET"), help="Shinhan OpenAPI app secret/client secret.")
    parser.add_argument("--webhook-url", default=os.getenv("ALERT_WEBHOOK_URL"), help="Optional webhook URL (Slack/Discord).")
    parser.add_argument(
        "--include-app-headers",
        default=os.getenv("INCLUDE_APP_HEADERS", "true").lower() != "false",
        action=argparse.BooleanOptionalAction,
        help="Send appkey/appsecret headers on data calls if the API expects them.",
    )
    parser.add_argument(
        "--extra-headers",
        default=os.getenv("SHINHAN_EXTRA_HEADERS"),
        help="Comma-separated extra headers for all requests (e.g., 'tr_id=H0STCNT0').",
    )
    return parser


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    parser = build_arg_parser()
    args = parser.parse_args()

    if not args.app_key or not args.app_secret:
        raise SystemExit("Set SHINHAN_APP_KEY and SHINHAN_APP_SECRET (or pass --app-key/--app-secret).")

    client = ShinhanClient(
        base_url=args.base_url,
        app_key=args.app_key,
        app_secret=args.app_secret,
        token_path=args.token_path,
        candle_path=args.candle_path,
        quote_path=args.quote_path,
        include_app_headers=args.include_app_headers,
        extra_headers=_parse_extra_headers(args.extra_headers),
    )

    monitor(
        client=client,
        symbol=args.symbol,
        poll_interval=args.poll_interval,
        ma_window=10,
        lookback=args.lookback,
        tolerance=args.tolerance,
        tolerance_pct=args.tolerance_pct,
        webhook_url=args.webhook_url,
        recalc_minutes=args.recalc_minutes,
        cooldown_seconds=args.cooldown_seconds,
        one_shot=args.one_shot,
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logging.info("Stopped by user.")
