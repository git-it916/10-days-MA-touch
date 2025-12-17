#!/usr/bin/env python3
"""
KOSPI 모멘텀 전략 (09:00~09:20) - 키움 REST API (ka10080/kt10000/ka01690)

전략 개요:
- 09:00 시가 vs 09:20 종가 수익률을 기준으로 Long/Short 결정
- Long: KODEX 200 (069500)
- Short: KODEX 인버스 (114800)  # 현물계좌에서는 인버스 매수로 대체
- 보유 현금을 100% 사용해 시장가 주문

필수 입력:
- APP_KEY, SECRET_KEY, ACCOUNT_NO (10자리, 하이픈 제거)
- 모의투자: base_url = https://mockapi.kiwoom.com (기본)
- 실전    : base_url = https://api.kiwoom.com (--base-url로 변경)

주의:
- 키움 REST 문서의 필수 헤더/바디를 최대한 반영했습니다.
- 실제 필드명이 다를 경우 문서에 맞게 key를 조정하세요.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import logging
from typing import Any, Dict, List, Optional

import requests


def _pick_first(mapping: Dict[str, Any], keys: List[str], default: Any = None) -> Any:
    for k in keys:
        if k in mapping and mapping[k] not in (None, "", []):
            return mapping[k]
    return default


def _to_float(val: Any) -> float:
    try:
        s = str(val).replace(",", "").replace("+", "")
        return float(s)
    except Exception:
        return 0.0


class KiwoomREST:
    def __init__(
        self,
        app_key: str,
        secret_key: str,
        account_no: str,
        base_url: str = "https://mockapi.kiwoom.com",
        timeout: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.app_key = app_key
        self.secret_key = secret_key
        self.account_no = account_no
        self.timeout = timeout
        self.session = requests.Session()
        self.token: Optional[str] = None

    # ------------------------------------------------------------------ Auth
    def authenticate(self) -> str:
        url = f"{self.base_url}/oauth2/token"
        headers = {"Content-Type": "application/json;charset=UTF-8"}
        payload = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "secretkey": self.secret_key,
        }
        resp = self.session.post(url, json=payload, headers=headers, timeout=self.timeout)
        if resp.status_code >= 400:
            raise RuntimeError(f"Token request failed: {resp.status_code} {resp.text}")
        data = resp.json()
        token = data.get("access_token") or data.get("token")
        if not token:
            raise RuntimeError(f"Token not found in response: {data}")
        self.token = token
        logging.info("Token issued: %s...", token[:10])
        return token

    def _auth_headers(self, api_id: str) -> Dict[str, str]:
        if not self.token:
            self.authenticate()
        return {
            "Authorization": f"Bearer {self.token}",
            "AppKey": self.app_key,
            "AppSecret": self.secret_key,
            "api-id": api_id,
            "Content-Type": "application/json; charset=utf-8",
        }

    # ---------------------------------------------------------- Data fetch (ka10080)
    def fetch_intraday_1m(self, code: str, start_date: str, end_date: str) -> List[Dict[str, Any]]:
        """
        ka10080 분봉 차트 조회 (1분봉)
        - URL: /api/dostk/chart
        - 필수 Body: stk_cd, tic_scope("1"), upd_stkpc_tp("1")
        """
        url = f"{self.base_url}/api/dostk/chart"
        headers = self._auth_headers("ka10080")
        body = {
            "stk_cd": code,
            "tic_scope": "1",      # 1분봉
            "upd_stkpc_tp": "1",   # 수정주가 반영
        }
        resp = self.session.post(url, headers=headers, json=body, timeout=self.timeout)
        # 429 처리: 짧게 대기 후 1회 재시도
        if resp.status_code == 429:
            logging.warning("ka10080 429 발생, 2초 대기 후 재시도")
            import time

            time.sleep(2)
            resp = self.session.post(url, headers=headers, json=body, timeout=self.timeout)
        if resp.status_code >= 400:
            raise RuntimeError(f"Intraday request failed: {resp.status_code} {resp.text}")
        data = resp.json()
        items = (
            data.get("stk_min_pole_chart_qry")
            or data.get("stk_min_pole_chart_qty")
            or data.get("rec")
            or []
        )
        if not items:
            logging.error(
                "[ka10080] 분봉 응답에 데이터가 없습니다. keys=%s body_sample=%s",
                list(data.keys()),
                json.dumps(data, ensure_ascii=False)[:400],
            )
        return items

    # ------------------------------------------------------------- Balance (ka01690)
    def fetch_balance(self, qry_dt: Optional[str] = None) -> Dict[str, Any]:
        url = f"{self.base_url}/api/dostk/acnt"
        headers = self._auth_headers("kt00004")
        body: Dict[str, Any] = {
            "qry_tp": "0",        # 전체
            "dmst_stex_tp": "KRX",
        }
        resp = self.session.post(url, headers=headers, json=body, timeout=self.timeout)
        if resp.status_code >= 400:
            raise RuntimeError(f"Balance request failed: {resp.status_code} {resp.text}")
        return resp.json()

    def extract_cash(self, balance_resp: Dict[str, Any]) -> float:
        """
        응답에서 사용 가능 현금을 추출. 문서 필드명에 따라 조정 필요.
        시도 키: ord_alowa, ord_alow_amt, 100ord_alow_amt, entr, tot_est_amt, d2_entra, prsm_dpst_aset_amt
        """
        candidates = [
            "ord_alowa",
            "ord_alow_amt",
            "100ord_alow_amt",
            "entr",
            "tot_est_amt",
            "d2_entra",
            "prsm_dpst_aset_amt",
        ]
        val = _pick_first(balance_resp, candidates)
        try:
            return float(str(val).replace(",", ""))
        except Exception:
            return 0.0

    # --------------------------------------------------------------- Order (kt10000)
    def send_market_buy(self, code: str, qty: int) -> Dict[str, Any]:
        url = f"{self.base_url}/api/dostk/ordr"
        headers = self._auth_headers("kt10000")
        body = {
            "ord_qty": str(qty),
            "ord_uv": "0",     # 시장가
            "trde_tp": "03",   # 시장가 코드
            "stk_cd": code,
        }
        resp = self.session.post(url, headers=headers, json=body, timeout=self.timeout)
        if resp.status_code >= 400:
            raise RuntimeError(f"Order failed: {resp.status_code} {resp.text}")
        return resp.json()


# =================================================================== Strategy
def run_strategy(client: KiwoomREST) -> None:
    today = dt.datetime.now().strftime("%Y%m%d")

    # 1. 1분봉 데이터 가져오기 (KODEX 200)
    items = client.fetch_intraday_1m("069500", start_date=today, end_date=today)

    def _parse_time(item: Dict[str, Any]) -> str:
        """
        응답 내 시간 필드를 HHMM으로 정규화.
        가능한 키: cntr_tm(YYYYMMDDHHMMSS), trd_tm, trd_time, time, hhmm, hhmmss 등.
        숫자만 추출 후 앞 12자리에서 HHMM을 추출 (예: 20250307132000 -> 1320, 090000 -> 0900).
        """
        raw = str(
            _pick_first(
                item,
                [
                    "cntr_tm",
                    "trd_tm",
                    "trd_time",
                    "time",
                    "hhmm",
                    "hhmmss",
                    "stk_tm",
                    "stck_bsop_time",
                ],
                default="",
            )
        )
        digits = re.sub(r"\D", "", raw)
        if len(digits) >= 12:
            return digits[8:12]  # YYYYMMDDHHMMSS -> HHMM
        if len(digits) >= 4:
            return digits[:4]
        return digits.zfill(4)

    def _parse_price(item: Dict[str, Any]) -> float:
        return _to_float(_pick_first(item, ["cur_prc", "clpr", "close_pric", "stck_prpr", "prpr"], 0))

    # 필터 09:00, 09:20 데이터 찾기
    price_09 = None
    price_0920 = None
    times_seen = []
    for it in items:
        tm = _parse_time(it)
        if tm:
            times_seen.append(tm)
        if tm == "0900":
            price_09 = _parse_price(it)
        if tm == "0920":
            price_0920 = _parse_price(it)
    if price_09 is None or price_0920 is None:
        logging.error(
            "09:00 또는 09:20 데이터가 없습니다. (장 시작 전/데이터 미수신/시간 필드 불일치) | 예시 시간들=%s",
            sorted(set(times_seen))[:10],
        )
        return

    ret = (price_0920 - price_09) / price_09 if price_09 else 0
    logging.info("09:00=%.2f 09:20=%.2f 수익률=%.4f", price_09, price_0920, ret)

    # 2. 방향 결정
    target_code = "069500" if ret > 0 else "114800"
    logging.info("매수 대상: %s (ret %s)", target_code, "LONG" if ret > 0 else "SHORT via inverse")

    # 3. 잔고 조회 -> 현금 파악
    bal = client.fetch_balance()
    cash = client.extract_cash(bal)
    if cash <= 0:
        logging.error("사용 가능 현금이 없습니다. cash=%.2f", cash)
        return

    # 4. 매수 단가: 대상 코드의 최신 가격을 다시 가져와 계산 (가장 최근 1분 봉)
    items_target = client.fetch_intraday_1m(target_code, start_date=today, end_date=today)
    last_price = None
    if items_target:
        last_price = _parse_price(items_target[0])
        for it in items_target:
            lp = _parse_price(it)
            if lp:
                last_price = lp
                break
    if not last_price or last_price <= 0:
        logging.error("대상 코드의 가격을 가져오지 못했습니다.")
        return

    qty = int(cash // last_price)
    if qty <= 0:
        logging.error("매수 수량이 0입니다. cash=%.2f, price=%.2f", cash, last_price)
        return

    # 5. 시장가 매수
    resp = client.send_market_buy(target_code, qty)
    logging.info("주문 완료: %s", json.dumps(resp, ensure_ascii=False))


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    parser = argparse.ArgumentParser(description="KOSPI 09:00~09:20 모멘텀 전략 (키움 REST)")
    parser.add_argument("--app-key", required=True)
    parser.add_argument("--secret-key", required=True)
    parser.add_argument("--account", required=True, help="계좌번호 10자리, 하이픈 제거")
    parser.add_argument(
        "--base-url",
        default="https://mockapi.kiwoom.com",
        help="모의투자 기본; 실전은 https://api.kiwoom.com",
    )
    args = parser.parse_args()

    client = KiwoomREST(
        app_key=args.app_key,
        secret_key=args.secret_key,
        account_no=args.account,
        base_url=args.base_url,
    )
    client.authenticate()
    run_strategy(client)


if __name__ == "__main__":
    main()
