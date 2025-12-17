"""
Kiwoom REST 래퍼.
- 키/시크릿/계좌를 인자로 받아 KiwoomRestClient(kiwoom_api.py)를 감싸는 간단한 클래스.
- is_simulation=True이면 모의투자 엔드포인트(https://mockapi.kiwoom.com)를 사용.
"""

from __future__ import annotations

import getpass
import os
import sys
from typing import Any, Dict, List, Optional

# 동일 디렉터리의 kiwoom_api.py를 import
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

from kiwoom_api import KiwoomRestClient, Candle


class KiwoomREST:
    def __init__(
        self,
        app_key: str,
        secret_key: str,
        account_no: str,
        is_simulation: bool = True,
    ) -> None:
        base_url = "https://mockapi.kiwoom.com" if is_simulation else "https://api.kiwoom.com"
        self.client = KiwoomRestClient(
            base_url=base_url,
            app_key=app_key,
            app_secret=secret_key,
            account_no=account_no,
        )
        self.client.authenticate()

    def get_daily_ohlcv(self, code: str, to_date: Optional[str] = None, count: int = 20) -> List[Candle]:
        """일봉 조회 (ka10005)"""
        return self.client.fetch_daily_candles(code, to_date=to_date, count=count, period="D")

    def get_balance(self, qry_tp: str = "0", dmst_stex_tp: str = "KRX") -> Dict[str, Any]:
        """잔고 조회 (kt00004 계좌평가현황요청)"""
        return self.client.fetch_balance(qry_tp=qry_tp, dmst_stex_tp=dmst_stex_tp)

    def buy_market(self, code: str, qty: int) -> Dict[str, Any]:
        """시장가 매수"""
        return self.client.send_order(code=code, qty=qty, price=0, side="buy", order_type="03")

    def sell_market(self, code: str, qty: int) -> Dict[str, Any]:
        """시장가 매도"""
        return self.client.send_order(code=code, qty=qty, price=0, side="sell", order_type="03")


def _prompt(msg: str, secret: bool = False) -> str:
    return getpass.getpass(msg) if secret else input(msg)


if __name__ == "__main__":
    print("키움 REST 샘플 실행 (모의투자 기본)")
    app_key = _prompt("APP_KEY: ")
    secret_key = _prompt("SECRET_KEY: ", secret=True)
    account_no = _prompt("ACCOUNT_NO(10자리, 하이픈 없이): ")
    is_sim = _prompt("모의투자면 Y, 실전이면 N [Y/N]: ").strip().lower() != "n"

    kiwoom = KiwoomREST(app_key, secret_key, account_no, is_simulation=is_sim)

    # 예시: 일봉 5개 조회
    candles = kiwoom.get_daily_ohlcv("005930", count=5)
    print(f"캔들 {len(candles)}개 조회")
    if candles:
        print(candles[0])

    # 예시: 잔고 조회 (엔드포인트/TR-ID가 맞지 않으면 실패할 수 있으니 예외 무시)
    try:
        balance = kiwoom.get_balance()
        print("잔고 응답 키:", list(balance.keys()))
    except Exception as exc:  # pragma: no cover - 데모용
        print(f"[잔고 조회 실패] 엔드포인트/TR-ID를 문서에 맞게 설정하세요: {exc}")
