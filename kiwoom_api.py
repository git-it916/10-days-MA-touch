#!/usr/bin/env python3
"""
Kiwoom REST API client (token, 시세/일봉 조회, 계좌잔고, 주문) 샘플.

주의: 실제 엔드포인트/필드명은 키움 REST 문서에 맞춰 env 또는 인자로 설정해야 합니다.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests


def _load_env(name: str, default: str = "") -> str:
    return os.getenv(name, default)


def _safe_json(resp: requests.Response) -> Dict[str, Any]:
    try:
        return resp.json()
    except Exception:
        raise RuntimeError(f"Invalid JSON response: {resp.text[:400]}")


@dataclass
class Candle:
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: Optional[float] = None


class KiwoomRestClient:
    """
    키움 REST API 기본 클라이언트.
    - 토큰 발급
    - 일봉/시세 조회
    - 계좌잔고 조회
    - 주문 (매수/매도)
    """

    def __init__(
        self,
        base_url: str,
        app_key: str,
        app_secret: str,
        account_no: str,
        token_path: str = "/oauth2/token",
        candle_path: str = "/uapi/domestic-stock/v1/quotations/inquire-daily-price",
        balance_path: str = "/uapi/domestic-stock/v1/trading/inquire-balance",
        order_path: str = "/uapi/domestic-stock/v1/trading/order-cash",
        tr_id_quote: str = "FHKST01010400",
        tr_id_balance: str = "TTTC8434R",
        tr_id_buy: str = "TTTC0802U",
        tr_id_sell: str = "TTTC0801U",
        timeout: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.app_key = app_key
        self.app_secret = app_secret
        self.account_no = account_no
        self.token_path = token_path
        self.candle_path = candle_path
        self.balance_path = balance_path
        self.order_path = order_path
        self.tr_id_quote = tr_id_quote
        self.tr_id_balance = tr_id_balance
        self.tr_id_buy = tr_id_buy
        self.tr_id_sell = tr_id_sell
        self.timeout = timeout
        self.session = requests.Session()
        self._token: Optional[str] = None

    def authenticate(self) -> str:
        url = self._url(self.token_path)
        # 키움 REST 가이드 기준: grant_type/client_credentials, appkey, secretkey
        payload = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "secretkey": self.app_secret,
        }
        headers = {"Content-Type": "application/json;charset=UTF-8"}
        resp = self.session.post(url, json=payload, headers=headers, timeout=self.timeout)
        if resp.status_code >= 400:
            raise RuntimeError(f"Token request failed: {resp.status_code} {resp.text}")
        data = _safe_json(resp)
        token = (
            data.get("access_token")
            or data.get("token")
            or data.get("access_token_token")
            or data.get("data", {}).get("access_token")
        )
        if not token:
            raise RuntimeError(f"Token not found in response: {data}")
        self._token = token
        return token

    def _auth_headers(self, tr_id: Optional[str] = None) -> Dict[str, str]:
        if not self._token:
            self.authenticate()
        headers = {
            "authorization": f"Bearer {self._token}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
        }
        if tr_id:
            headers["tr_id"] = tr_id
        return headers

    def _url(self, path: str) -> str:
        if path.startswith("http"):
            return path
        return f"{self.base_url}{path}"

    # 시세/일봉 조회 (REST 기준 샘플)
    def fetch_daily_candles(self, code: str, to_date: Optional[str] = None, count: int = 20) -> List[Candle]:
        url = self._url(self.candle_path)
        if to_date is None:
            to_date = dt.datetime.now().strftime("%Y%m%d")
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",  # 코스피/코스닥
            "FID_INPUT_ISCD": code,         # 종목코드
            "FID_INPUT_DATE_1": to_date,    # 기준일자
            "FID_PERIOD_DIV_CODE": "D",     # 일봉
            "FID_ORG_ADJ_PRC": "1",         # 수정주가
        }
        headers = self._auth_headers(self.tr_id_quote)
        resp = self.session.get(url, params=params, headers=headers, timeout=self.timeout)
        if resp.status_code >= 400:
            raise RuntimeError(f"Candle request failed: {resp.status_code} {resp.text}")
        data = _safe_json(resp)
        items = data.get("output") or data.get("output1") or []
        candles: List[Candle] = []
        for item in items[:count]:
            try:
                candles.append(
                    Candle(
                        date=str(item.get("stck_bsop_date") or item.get("basDt")),
                        open=float(item.get("stck_oprc") or item.get("opnprc") or 0),
                        high=float(item.get("stck_hgpr") or item.get("hgpr") or 0),
                        low=float(item.get("stck_lwpr") or item.get("lwpr") or 0),
                        close=float(item.get("stck_clpr") or item.get("clpr") or 0),
                        volume=float(item.get("acml_vol") or item.get("trqu") or 0),
                    )
                )
            except Exception:
                continue
        return candles

    # 잔고 조회
    def fetch_balance(self) -> Dict[str, Any]:
        url = self._url(self.balance_path)
        params = {
            "CANO": self.account_no[:-2],
            "ACNT_PRDT_CD": self.account_no[-2:],
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "00",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        headers = self._auth_headers(self.tr_id_balance)
        resp = self.session.get(url, params=params, headers=headers, timeout=self.timeout)
        if resp.status_code >= 400:
            raise RuntimeError(f"Balance request failed: {resp.status_code} {resp.text}")
        return _safe_json(resp)

    # 주문 (현금매수/매도)
    def send_order(
        self,
        code: str,
        qty: int,
        price: int,
        side: str = "buy",  # "buy" or "sell"
        order_type: str = "00",  # 00 지정가, 03 시장가
    ) -> Dict[str, Any]:
        url = self._url(self.order_path)
        tr_id = self.tr_id_buy if side == "buy" else self.tr_id_sell
        headers = self._auth_headers(tr_id)
        payload = {
            "CANO": self.account_no[:-2],
            "ACNT_PRDT_CD": self.account_no[-2:],
            "PDNO": code,
            "ORD_DVSN": order_type,
            "ORD_QTY": str(qty),
            "ORD_UNPR": str(price),
        }
        resp = self.session.post(url, json=payload, headers=headers, timeout=self.timeout)
        if resp.status_code >= 400:
            raise RuntimeError(f"Order failed: {resp.status_code} {resp.text}")
        return _safe_json(resp)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Kiwoom REST API sample client.")
    p.add_argument("--base-url", default=_load_env("KIWOOM_BASE_URL", "https://api.kiwoom.com"))
    p.add_argument("--token-path", default=_load_env("KIWOOM_TOKEN_PATH", "/oauth2/token"))
    p.add_argument("--candle-path", default=_load_env("KIWOOM_CANDLE_PATH", "/uapi/domestic-stock/v1/quotations/inquire-daily-price"))
    p.add_argument("--balance-path", default=_load_env("KIWOOM_BALANCE_PATH", "/uapi/domestic-stock/v1/trading/inquire-balance"))
    p.add_argument("--order-path", default=_load_env("KIWOOM_ORDER_PATH", "/uapi/domestic-stock/v1/trading/order-cash"))
    p.add_argument("--tr-id-quote", default=_load_env("KIWOOM_TR_ID_QUOTE", "FHKST01010400"))
    p.add_argument("--tr-id-balance", default=_load_env("KIWOOM_TR_ID_BALANCE", "TTTC8434R"))
    p.add_argument("--tr-id-buy", default=_load_env("KIWOOM_TR_ID_BUY", "TTTC0802U"))
    p.add_argument("--tr-id-sell", default=_load_env("KIWOOM_TR_ID_SELL", "TTTC0801U"))
    p.add_argument("--app-key", default=_load_env("KIWOOM_APP_KEY"), required=False)
    p.add_argument("--app-secret", default=_load_env("KIWOOM_APP_SECRET"), required=False)
    p.add_argument("--account", default=_load_env("KIWOOM_ACCOUNT"), required=False, help="계좌번호 10자리 (예: 12345678-01 형태면 하이픈 제거)")
    p.add_argument("--symbol", default="005930")
    p.add_argument("--to-date", default=None, help="YYYYMMDD (미지정 시 오늘)")
    p.add_argument("--count", type=int, default=20)
    p.add_argument("--order-side", choices=["buy", "sell"], default=None)
    p.add_argument("--order-qty", type=int, default=1)
    p.add_argument("--order-price", type=int, default=0)
    p.add_argument("--order-type", default="03", help="00 지정가, 03 시장가")
    return p


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    args = build_arg_parser().parse_args()

    if not args.app_key or not args.app_secret or not args.account:
        raise SystemExit("환경변수 KIWOOM_APP_KEY / KIWOOM_APP_SECRET / KIWOOM_ACCOUNT를 설정하거나 인자로 전달하세요.")

    client = KiwoomRestClient(
        base_url=args.base_url,
        app_key=args.app_key,
        app_secret=args.app_secret,
        account_no=args.account,
        token_path=args.token_path,
        candle_path=args.candle_path,
        balance_path=args.balance_path,
        order_path=args.order_path,
        tr_id_quote=args.tr_id_quote,
        tr_id_balance=args.tr_id_balance,
        tr_id_buy=args.tr_id_buy,
        tr_id_sell=args.tr_id_sell,
    )

    # 토큰 발급
    token = client.authenticate()
    logging.info("Token issued: %s...", token[:10])

    # 일봉 조회
    candles = client.fetch_daily_candles(args.symbol, to_date=args.to_date, count=args.count)
    logging.info("Fetched %d candles for %s", len(candles), args.symbol)
    if candles:
        logging.info("Latest candle: %s", candles[0])

    # 잔고 조회
    balance = client.fetch_balance()
    logging.info("Balance response keys: %s", list(balance.keys()))

    # 주문 예시 (옵션 지정 시)
    if args.order_side:
        resp = client.send_order(
            code=args.symbol,
            qty=args.order_qty,
            price=args.order_price,
            side=args.order_side,
            order_type=args.order_type,
        )
        logging.info("Order response: %s", json.dumps(resp, ensure_ascii=False))


if __name__ == "__main__":
    main()
