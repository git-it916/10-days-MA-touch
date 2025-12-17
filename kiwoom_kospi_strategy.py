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
import time
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
        # 가격이 음수 문자열로 올 때도 절댓값 처리
        return abs(float(s))
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
            msg = data.get("return_msg") or ""
            logging.warning(
                "[ka10080] 분봉 데이터 없음(code=%s). keys=%s msg=%s body_sample=%s",
                code,
                list(data.keys()),
                msg,
                json.dumps(data, ensure_ascii=False)[:400],
            )
            # 한 번 더 대기 후 재시도
            time.sleep(1)
            resp = self.session.post(url, headers=headers, json=body, timeout=self.timeout)
            if resp.status_code == 429:
                logging.warning("ka10080 429 재발생, 추가 2초 대기 후 중단")
                time.sleep(2)
            elif resp.status_code >= 400:
                raise RuntimeError(f"Intraday request failed (retry): {resp.status_code} {resp.text}")
            data = resp.json()
            items = (
                data.get("stk_min_pole_chart_qry")
                or data.get("stk_min_pole_chart_qty")
                or data.get("rec")
                or []
            )
            if not items:
                raise RuntimeError(
                    f"Intraday data empty for {code}. msg={data.get('return_msg','')} sample={json.dumps(data, ensure_ascii=False)[:400]}"
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
            "trde_tp": "03",        # 거래구분: 03 (시장가)
            "ord_qty": str(qty),    # 주문수량
            "ord_uv": "0",          # 주문단가 (시장가는 0)
            "stk_cd": code,         # 종목코드
            "dmst_stex_tp": "KRX",  # 거래소 구분
            "ord_dv": "00",         # 주문 구분 (00: 현금매수)
        }
        resp = self.session.post(url, headers=headers, json=body, timeout=self.timeout)
        if resp.status_code >= 400:
            raise RuntimeError(f"Order failed: {resp.status_code} {resp.text}")
        return resp.json()


# =================================================================== Strategy
def run_strategy(client: KiwoomREST) -> None:
    today = dt.datetime.now().strftime("%Y%m%d")
    logging.info(f"전략 시작: {today} (대상: KODEX 200 vs 인버스)")

    # 1. 1분봉 데이터 가져오기 (KODEX 200) - 안전 대기
    time.sleep(0.5)
    items = client.fetch_intraday_1m("069500", start_date=today, end_date=today)

    def _parse_time(item: Dict[str, Any]) -> str:
        raw = str(
            _pick_first(
                item,
                ["cntr_tm", "trd_tm", "trd_time", "time", "hhmm", "hhmmss", "stk_tm", "stck_bsop_time"],
                default="",
            )
        )
        digits = re.sub(r"\D", "", raw)
        if len(digits) >= 12:
            return digits[8:12]
        if len(digits) >= 4:
            return digits[:4]
        return digits.zfill(4)

    def _parse_price(item: Dict[str, Any]) -> float:
        return _to_float(_pick_first(item, ["cur_prc", "clpr", "close_pric", "stck_prpr", "prpr"], 0))

    if not items:
        logging.error("KODEX 200(069500) 차트 데이터를 가져오지 못했습니다. 장 시작 전/휴장일 가능성.")
        return

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
            "09:00/09:20 데이터 부족 | 09:00=%.1f 09:20=%.1f | 시간예시=%s",
            price_09 or 0,
            price_0920 or 0,
            sorted(list(set(times_seen)))[:10],
        )
        return

    ret = (price_0920 - price_09) / price_09 if price_09 else 0
    logging.info("09:00=%.2f 09:20=%.2f 수익률=%.4f", price_09, price_0920, ret)

    target_code = "069500" if ret > 0 else "114800"
    target_name = "KODEX 200" if ret > 0 else "KODEX 인버스"
    logging.info("매수 대상: %s (%s)", target_code, "LONG" if ret > 0 else "SHORT via inverse")

    # 3. 잔고 조회 (안전 대기)
    time.sleep(1.0)
    bal = client.fetch_balance()
    cash = client.extract_cash(bal)
    if cash <= 0:
        logging.error("주문 가능 현금이 없습니다. cash=%.2f", cash)
        return
    logging.info("현재 보유 현금: %.0f원", cash)

    # 4. 대상 코드 가격 조회 (대기 시간 1.5초로 증가)
    time.sleep(1.5)
    items_target = client.fetch_intraday_1m(target_code, start_date=today, end_date=today)
    last_price = None
    if items_target:
        for it in items_target:
            lp = _parse_price(it)
            if lp > 0:
                last_price = lp
                break
    if not last_price or last_price <= 0:
        logging.error(f"대상 코드({target_code})의 현재가를 가져오지 못했습니다.")
        if not items_target:
            logging.error(" -> 응답 리스트가 비었습니다. (Rate Limit 또는 데이터 없음)")
        else:
            logging.error(f" -> 데이터는 있으나 가격 파싱 실패. 첫 샘플: {items_target[0]}")
        return

    invest_cash = cash * 0.03
    qty = int(invest_cash // last_price)
    if qty <= 0:
        logging.error(
            "매수 가능 수량이 0입니다. (투입금: %.0f=현금의 3%%, 현재가: %.0f)",
            invest_cash,
            last_price,
        )
        return

    logging.info(
        f"주문 실행: {target_name}({target_code}) {qty}주 매수 "
        f"(현금의 3% 투입={invest_cash:.0f}, 예상단가={last_price})"
    )
    time.sleep(0.5)
    resp = client.send_market_buy(target_code, qty)
    logging.info("주문 결과: %s", json.dumps(resp, ensure_ascii=False))


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
