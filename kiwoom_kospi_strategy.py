#!/usr/bin/env python3
"""
[KOSPI 전략] 전일 수급 + 09:02 모멘텀 + 10:00 청산

전략 요약
- (필터/진입) 전일 외국인 순매수(+) & 09:00~09:02 수익률(+)이면 KODEX 200(069500) 매수
- (필터/진입) 전일 외국인 순매수(-) & 09:00~09:02 수익률(-)이면 KODEX 인버스(114800) 매수
- (기타) 위 조건 불일치 시 진입하지 않음
- (청산) 10:00 전량 매도

주의/가정
- 이 스크립트는 “로그가 터미널에 무조건 보이게” 하는 것을 우선합니다.
  따라서 `logging.basicConfig(..., handlers=[StreamHandler(sys.stdout)], force=True)`로 설정합니다.
- REST API 스펙은 계정/환경(모의/실계) 및 문서 버전에 따라 필드명이 달라질 수 있습니다.
  (예: 현재가 키가 `cur_prc`/`stck_prpr` 등으로 바뀌는 경우)
- 본 예시는 전략 흐름(수급 확인 → 09:02 모멘텀 → 주문 → 10:00 청산)에 집중합니다.
  “정확한 보유수량 기반 청산”을 하려면 잔고/보유수량 API에서 종목별 잔고를 읽어 매도 수량을 결정해야 합니다.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
import time
from typing import Dict, List, Optional, Any

import requests


FIXED_INVEST_KRW = 3_000_000  # 300만원


def configure_logging() -> None:
    """
    로그 출력 “강화” 설정.

    요구사항: “로그가 터미널에 무조건 보이게”
    - StreamHandler를 sys.stdout으로 고정(표준 출력)
    - force=True로 기존 로깅 설정이 있더라도 덮어씀
    """
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )

    # requests/urllib3가 너무 시끄럽게 로그를 내는 환경이 있어, 필요 시 레벨을 낮춥니다.
    logging.getLogger("urllib3").setLevel(logging.WARNING)


class APIError(RuntimeError):
    """
    REST API 호출이 실패했을 때(HTTP 오류/응답 파싱 실패 등) 사용합니다.
    - “어디서/무엇이/왜 실패했는지” 로그로 드러나게 하는 목적입니다.
    """


def _pick_first(mapping: Dict[str, Any], keys: List[str], default: Any = None) -> Any:
    """
    여러 후보 키 중, 값이 “비어있지 않은” 첫 번째 값을 반환합니다.
    - API 응답 키가 환경/버전에 따라 다를 때 방어적으로 사용합니다.
    """
    for key in keys:
        if key in mapping and mapping[key] not in (None, "", []):
            return mapping[key]
    return default


def _to_int_abs(val: Any) -> int:
    """
    '12,345' / '+123' / '-123' 같은 문자열을 안전하게 int로 변환합니다.
    - 현재가처럼 음수로 내려오는 케이스(부호 포함)를 대비해 abs 처리합니다.
    """
    try:
        s = str(val).strip().replace(",", "").replace("+", "")
        return abs(int(float(s)))
    except Exception:
        return 0


def _to_price_index(val: Any) -> float:
    """
    지수/가격이 '100분의 1' 단위(예: 260500)로 내려오는 경우가 있어 100으로 나눕니다.
    - 키움 분봉(ka20005) 예시에서 `cur_prc`가 100배인 형태를 자주 봅니다.
    """
    try:
        s = str(val).strip().replace(",", "").replace("+", "")
        return abs(float(s)) / 100.0
    except Exception:
        return 0.0


def _truncate(text: str, limit: int = 300) -> str:
    """
    너무 긴 응답/문자열을 로그에 그대로 찍지 않도록 자르는 유틸.
    - limit을 넘어가면 뒤에 '...'을 붙입니다.
    """
    text = text or ""
    return (text[:limit] + "...") if len(text) > limit else text


class KiwoomREST:
    """
    Kiwoom REST API 최소 클라이언트.

    이 클래스는 “전략 실행에 필요한 것만” 구현합니다.
    - 토큰 발급(oauth2/token)
    - 전일 외국인 순매수(ka10051)
    - KOSPI 1분봉(지수 분봉, ka20005)
    - 예수금(kt00004)
    - 현재가(ka10001)
    - 주문(kt10000/kt10001)
    """

    def __init__(self, app_key: str, secret_key: str, account_no: str, base_url: str) -> None:
        self.app_key = app_key
        self.secret_key = secret_key
        self.account_no = account_no  # 일부 API는 계좌번호를 body에 요구할 수 있습니다(문서 확인 필요).
        self.base_url = base_url.rstrip("/")
        self.token: Optional[str] = None
        self.session = requests.Session()

    def _post_json(
        self,
        *,
        path: str,
        api_id: str,
        body: Dict[str, Any],
        timeout: float,
        max_retries: int = 2,
        retry_sleep: float = 1.5,
    ) -> Dict[str, Any]:
        """
        공통 POST(JSON) 요청 래퍼.

        “중간중간 잘 실행되는지 보이도록” 하기 위해 다음을 촘촘히 로그로 남깁니다.
        - 요청 시작/끝(경로, api-id)
        - HTTP 상태코드
        - 소요 시간(ms)
        - 오류 시 응답 텍스트 일부
        - 429(레이트리밋) 발생 시 짧게 재시도

        주의:
        - secret/app_key/token 등 민감정보는 로그에 출력하지 않습니다.
        """
        url = f"{self.base_url}{path}"
        headers = self.get_headers(api_id)

        last_exc: Optional[Exception] = None
        for attempt in range(1, max_retries + 2):
            start = time.perf_counter()
            logging.info("API 요청 시작: %s %s (attempt %d)", api_id, path, attempt)
            try:
                resp = self.session.post(url, headers=headers, json=body, timeout=timeout)
                elapsed_ms = int((time.perf_counter() - start) * 1000)
                logging.info("API 응답 수신: %s %s -> HTTP %s (%dms)", api_id, path, resp.status_code, elapsed_ms)

                # 429는 레이트리밋이므로 재시도하는 편이 실용적입니다.
                if resp.status_code == 429 and attempt < (max_retries + 1):
                    logging.warning("API 429(레이트리밋): %s %s -> %ss 후 재시도", api_id, path, retry_sleep)
                    time.sleep(retry_sleep)
                    continue

                if resp.status_code >= 400:
                    raise APIError(f"HTTP {resp.status_code}: {_truncate(resp.text)}")

                try:
                    data = resp.json()
                except Exception as exc:
                    raise APIError(f"JSON 파싱 실패: {exc} / body={_truncate(resp.text)}") from exc

                # 응답이 너무 커질 수 있어 키만 요약으로 남깁니다.
                if isinstance(data, dict):
                    logging.info("API 응답 키: %s", list(data.keys()))
                return data
            except Exception as exc:
                last_exc = exc
                logging.warning("API 요청 실패: %s %s (%s)", api_id, path, exc)
                if attempt < (max_retries + 1):
                    time.sleep(retry_sleep)
                    continue
                break

        raise APIError(f"API 호출 실패(최종): {api_id} {path} / last_error={last_exc}")

    def authenticate(self) -> None:
        """
        OAuth2 토큰 발급.

        실패 시:
        - 에러 로그를 남기고 프로그램을 종료합니다(SystemExit).
        """
        logging.info("토큰 발급 시도 중...")
        token_url = f"{self.base_url}/oauth2/token"
        headers = {"Content-Type": "application/json;charset=UTF-8"}
        payload = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "secretkey": self.secret_key,
        }

        start = time.perf_counter()
        try:
            resp = self.session.post(token_url, headers=headers, json=payload, timeout=10)
        except Exception as exc:
            logging.error("인증 중 네트워크/요청 에러: %s", exc)
            raise SystemExit(1)

        elapsed_ms = int((time.perf_counter() - start) * 1000)
        logging.info("토큰 응답 수신: HTTP %s (%dms)", resp.status_code, elapsed_ms)
        if resp.status_code != 200:
            logging.error("인증 실패: %s", _truncate(resp.text))
            raise SystemExit(1)

        data = resp.json()
        token = data.get("access_token") or data.get("token")
        if not token:
            logging.error("토큰이 응답에 없습니다: %s", str(data)[:200])
            raise SystemExit(1)

        self.token = token
        # 토큰은 민감정보이므로 로그에 출력하지 않습니다.
        logging.info("토큰 발급 성공!")

    def get_headers(self, api_id: str) -> Dict[str, str]:
        """
        모든 REST API 호출에 공통으로 들어가는 헤더를 구성합니다.
        - 토큰이 없으면 자동 발급합니다.
        """
        if not self.token:
            self.authenticate()
        return {
            "Authorization": f"Bearer {self.token}",
            "AppKey": self.app_key,
            "AppSecret": self.secret_key,
            "api-id": api_id,
            "Content-Type": "application/json; charset=utf-8",
        }

    def fetch_foreigner_net_buy(self, date_str: str) -> Optional[int]:
        """
        전일 외국인 순매수(금액) 조회.

        - API: ka10051 (업종별투자자순매수)
        - 반환: 외국인 순매수 금액(원) 또는 None(조회 실패)
        """
        body = {
            "mrkt_tp": "0",       # 0: 코스피 (문서 기준)
            "amt_qty_tp": "0",    # 0: 금액, 1: 수량
            "base_dt": date_str,  # YYYYMMDD
            "stex_tp": "3",       # 3: KRX (문서 기준)
        }

        try:
            data = self._post_json(path="/api/dostk/sect", api_id="ka10051", body=body, timeout=5)
        except Exception as exc:
            logging.warning("수급 조회 에러(%s): %s", date_str, exc)
            return None

        items = data.get("inds_netprps") or data.get("rec") or []
        if not items:
            return None

        for item in items:
            # 코스피 지수 코드가 응답에 섞여오는 경우가 있어, "001"을 포함하는 항목을 찾습니다.
            if "001" in str(item.get("inds_cd", "")):
                # 필드명은 문서/환경에 따라 바뀔 수 있어 후보 키를 둡니다.
                val = _pick_first(item, ["frgnr_netprps", "frgnr_netprps_amt", "frgn_netprps"], "0")
                s = str(val).replace(",", "")
                try:
                    return int(float(s))
                except Exception:
                    return None
        return None

    def fetch_kospi_index_1m(self) -> List[Dict[str, Any]]:
        """
        KOSPI 지수 1분봉 조회.

        - API: ka20005 (업종분봉)
        - 반환: 분봉 리스트(딕셔너리 목록). 실패하면 빈 리스트.
        """
        body = {"inds_cd": "001", "tic_scope": "1"}  # 001: KOSPI, 1: 1분봉

        try:
            data = self._post_json(path="/api/dostk/chart", api_id="ka20005", body=body, timeout=5)
        except Exception as exc:
            logging.warning("지수 분봉 조회 에러: %s", exc)
            return []

        items = data.get("inds_min_pole_qry") or data.get("rec") or []
        logging.info("지수 분봉 수신: %d건", len(items))
        return items

    def fetch_deposit(self) -> int:
        """
        예수금(주문가능금액) 조회.

        - API: kt00004
        - 반환: 주문 가능 금액(원). 실패하면 0.
        """
        body = {"qry_tp": "0", "dmst_stex_tp": "KRX"}

        try:
            data = self._post_json(path="/api/dostk/acnt", api_id="kt00004", body=body, timeout=5)
        except Exception:
            return 0

        # 문서/응답에 따라 키가 바뀌는 경우가 있어 후보 키를 둡니다.
        val = _pick_first(
            data,
            ["ord_alowa", "ord_alow_amt", "100ord_alow_amt", "entr", "d2_entra", "prsm_dpst_aset_amt"],
            "0",
        )
        return _to_int_abs(val)

    def get_current_price(self, code: str) -> int:
        """
        현재가 조회.

        - API: ka10001 (주식기본정보)
        - 반환: 현재가(원). 실패하면 0.
        """
        body = {"stk_cd": code}

        try:
            data = self._post_json(path="/api/dostk/mrkcond", api_id="ka10001", body=body, timeout=5)
        except Exception:
            return 0

        return _to_int_abs(_pick_first(data, ["cur_prc", "stck_prpr", "prpr"], "0"))

    def send_order(self, side: str, code: str, qty: int) -> None:
        """
        시장가 주문(매수/매도).

        - API: kt10000(매수) / kt10001(매도)
        - `side`: "buy" 또는 "sell"
        """
        api_id = "kt10000" if side == "buy" else "kt10001"

        # 주의: 실제 주문 API는 계좌번호/비밀번호/매체구분 등 추가 필드가 필요한 경우가 있습니다.
        #       모의환경에서는 단순화돼 있을 수 있으므로, 문서에 맞게 body를 보강하세요.
        body = {
            "trde_tp": "03",          # 03: 시장가 (문서 기준)
            "ord_qty": str(qty),
            "ord_uv": "0",            # 시장가는 가격 0
            "stk_cd": code,
            "dmst_stex_tp": "KRX",
            "ord_dv": "00",           # 00: 보통
        }

        try:
            res = self._post_json(path="/api/dostk/ordr", api_id=api_id, body=body, timeout=5)
        except Exception as exc:
            logging.error("주문 중 에러: %s", exc)
            return

        if res.get("return_code") == 0:
            logging.info(" -> [주문성공] %s %s %s주", side.upper(), code, qty)
        else:
            logging.error(" -> [주문실패] %s", res.get("return_msg"))


def wait_until(target_hhmm: str) -> None:
    """
    특정 시각(HHMM)까지 1초 단위로 대기합니다.
    - 예: target_hhmm="0902"이면 09:02 도달 시 루프 종료
    """
    logging.info("[%s] 까지 대기 시작...", target_hhmm)
    last_announce_at = 0.0
    while True:
        now = dt.datetime.now().strftime("%H%M")
        if now >= target_hhmm:
            logging.info("시간 도달! (%s)", now)
            return
        # 너무 조용하면 “멈춘 것처럼 보이는” 문제가 있어, 일정 주기로 진행 로그를 남깁니다.
        # (기본 30초마다 1번)
        if time.time() - last_announce_at >= 30:
            logging.info("대기 중... (현재 %s / 목표 %s)", now, target_hhmm)
            last_announce_at = time.time()
        time.sleep(1)


def _find_recent_foreigner_net(client: KiwoomREST, lookback_days: int = 10) -> Optional[int]:
    """
    최근 N일을 뒤져 “조회 가능한 전일 수급”을 찾아 반환합니다.
    - 주말/공휴일에는 데이터가 없을 수 있어, 단순히 어제(-1)만 조회하면 실패할 수 있습니다.
    """
    for i in range(1, lookback_days + 1):
        d = (dt.datetime.now() - dt.timedelta(days=i)).strftime("%Y%m%d")
        logging.info(" -> 수급 조회 시도: %s", d)
        net = client.fetch_foreigner_net_buy(d)
        if net is not None:
            logging.info(" -> %s 외국인 순매수: %s원", d, f"{net:,}")
            return net
    return None


def _extract_0900_0902_prices(
    candles: List[Dict[str, Any]],
    *,
    test_mode: bool,
) -> tuple[Optional[float], Optional[float]]:
    """
    지수 1분봉 리스트에서 09:00/09:02의 가격을 뽑습니다.

    - 일반적으로 `cntr_tm`가 YYYYMMDDHHMMSS 형태로 내려오므로, 앞 8자리 날짜 + 4자리 HHMM을 파싱합니다.
    - 환경에 따라 키가 다를 수 있어 후보 키를 둡니다.
    """
    p_0900: Optional[float] = None
    p_0902: Optional[float] = None

    today_dt = dt.datetime.now().strftime("%Y%m%d")

    for c in candles:
        # cntr_tm 예: 20250501090000 -> HHMM=0900
        tm_full = str(_pick_first(c, ["cntr_tm", "trd_tm", "time", "dt"], ""))

        # 테스트 모드가 아니면 “오늘 데이터만” 대상으로 필터링합니다.
        # (API가 과거/다른 날짜 데이터를 섞어 줄 때 방어)
        if (not test_mode) and tm_full and (not tm_full.startswith(today_dt)):
            continue

        digits = "".join(ch for ch in tm_full if ch.isdigit())
        hhmm = digits[8:12] if len(digits) >= 12 else digits[:4].zfill(4)

        # 가격 키 후보: cur_prc가 흔하지만, 다른 키로 내려오는 경우도 있습니다.
        prc_raw = _pick_first(c, ["cur_prc", "close_pric", "stck_prpr", "prpr"], "0")
        prc = _to_price_index(prc_raw)

        if hhmm == "0900":
            p_0900 = prc
        elif hhmm == "0902":
            p_0902 = prc

    if not test_mode:
        # 디버깅 편의: 타임스탬프가 잘 파싱되고 있는지 힌트를 줍니다.
        # (09:00/09:02가 안 잡힐 때, 실제로 어떤 HHMM이 있는지 확인 가능)
        times = []
        for c in candles[:80]:
            tm_full = str(_pick_first(c, ["cntr_tm", "trd_tm", "time", "dt"], ""))
            digits = "".join(ch for ch in tm_full if ch.isdigit())
            hhmm = digits[8:12] if len(digits) >= 12 else digits[:4].zfill(4)
            if hhmm and hhmm.isdigit():
                times.append(hhmm)
        if times:
            uniq = sorted(set(times))
            logging.info("분봉 HHMM 샘플(최대 12개): %s", uniq[:12])

    # [테스트] 데이터가 없으면 강제 주입 (테스트용)
    if test_mode and ((not p_0900) or (not p_0902)):
        logging.info("(테스트 모드) 캔들 데이터가 없어 임의 값 사용")
        p_0900 = 2600.00
        p_0902 = 2605.00  # 상승 가정

    return p_0900, p_0902


def run(client: KiwoomREST, test_mode: bool = False) -> None:
    """
    전략 실행 메인 루틴.

    흐름
    1) 전일 수급 확인
    2) 09:02까지 대기(테스트 모드면 스킵)
    3) 09:00/09:02 지수 가격으로 모멘텀 판단
    4) 예수금의 98%로 시장가 매수
    5) 10:00까지 대기 후 전량 매도(단순화: 매수 수량과 동일 수량 매도)
    """
    logging.info("=== 전략 프로그램 시작 ===")
    logging.info("모드: %s", "TEST" if test_mode else "LIVE")

    # 1. 전일 외국인 수급 확인
    logging.info("1. 외국인 수급 확인 중...")

    # [테스트 모드]
    # - 장중/장 마감 후에도 실행 흐름을 테스트할 수 있도록,
    #   최근 N일을 뒤져 조회 가능한 날짜를 찾아옵니다.
    foreigner_net = _find_recent_foreigner_net(client, lookback_days=10)
    if foreigner_net is None:
        logging.error("데이터 조회 실패로 종료합니다.")
        return

    foreigner_dir = 1 if foreigner_net > 0 else -1 if foreigner_net < 0 else 0
    if foreigner_dir == 0 and not test_mode:
        logging.info("!! 전일 수급 0 -> 전략 조건 불만족. 종료합니다.")
        return
    if foreigner_dir == 0 and test_mode:
        logging.info("!! (테스트 모드) 전일 수급 0이지만 강제로 진행합니다.")
    elif foreigner_dir > 0:
        logging.info("!! 전일 수급 양수(+) -> 롱 조건만 허용")
    else:
        logging.info("!! 전일 수급 음수(-) -> 숏 조건만 허용")

    # 2. 09:02 대기
    if not test_mode:
        logging.info("2. 09:02 진입 시각까지 대기...")
        wait_until("0902")
        # 분봉 데이터가 09:02 직후 API에서 바로 보이지 않는 경우가 있어, 약간 텀을 둡니다.
        time.sleep(3)
    else:
        logging.info("(테스트 모드) 시간 대기 없이 즉시 진행")

    # 3. 09:02 캔들 조회
    logging.info("2. 09:00/09:02 지수 분봉 조회 중...")
    candles = client.fetch_kospi_index_1m()
    if not candles:
        logging.warning("지수 분봉이 비어 있습니다. (응답 필드/시간/레이트리밋 확인 필요)")
    p_0900, p_0902 = _extract_0900_0902_prices(candles, test_mode=test_mode)

    if not p_0900 or not p_0902:
        logging.error("09:00/09:02 데이터 없음. (0900=%s, 0902=%s) 종료.", p_0900, p_0902)
        return

    ret = (p_0902 - p_0900) / p_0900 if p_0900 else 0.0
    logging.info(" -> 09:00: %.2f, 09:02: %.2f (수익률: %.4f)", p_0900, p_0902, ret)

    if ret > 0:
        ret_dir = 1
    elif ret < 0:
        ret_dir = -1
    else:
        logging.info(" -> 09:00~09:02 수익률 0. 방향 없음으로 종료합니다.")
        return

    if not test_mode and foreigner_dir != ret_dir:
        logging.info(
            "!! 수급 방향(%s)과 09:00~09:02 방향(%s) 불일치 -> 진입하지 않습니다.",
            "양수(+)" if foreigner_dir > 0 else "음수(-)",
            "상승(+)" if ret_dir > 0 else "하락(-)",
        )
        return
    if test_mode and foreigner_dir not in (0, ret_dir):
        logging.info("!! (테스트 모드) 수급/모멘텀 불일치지만 강제로 진행합니다.")

    # 09:02 모멘텀 기준으로 진입 종목 결정
    target_code = "069500" if ret_dir > 0 else "114800"  # 200 vs 인버스
    logging.info(" -> 진입 종목: %s (%s)", target_code, ("매수(Long)" if ret_dir > 0 else "매수(Short/Inverse)"))

    # 4. 매수(예수금 기반)
    logging.info("3. 예수금/현재가 조회 후 주문...")
    cash = client.fetch_deposit()
    logging.info(" -> 현재 예수금: %s원", f"{cash:,}")

    buy_qty = 0  # 10:00 청산 때 재사용(단, 실제 운용에서는 보유수량을 다시 조회하는 것이 안전)
    if cash <= 0:
        logging.error(" -> 예수금 0원으로 주문 불가")
        return

    invest = FIXED_INVEST_KRW
    if cash < invest:
        logging.error(" -> 예수금 부족으로 주문 불가 (예수금=%s원, 필요=%s원)", f"{cash:,}", f"{invest:,}")
        return

    logging.info(" -> 투입금 고정: %s원", f"{invest:,}")
    curr_price = client.get_current_price(target_code)
    if curr_price <= 0:
        logging.error(" -> 현재가 조회 실패로 주문 불가")
        return

    buy_qty = int(invest / curr_price)
    if buy_qty <= 0:
        logging.info(" -> 잔고 부족으로 주문 불가 (수량 0)")
        return

    logging.info(" -> 주문 실행: %s주 (투입=%s원, 현재가=%s원)", f"{buy_qty:,}", f"{invest:,}", f"{curr_price:,}")
    client.send_order("buy", target_code, buy_qty)

    # 5. 청산 대기
    if not test_mode:
        wait_until("1000")
        logging.info("10:00 도달. 전량 청산합니다.")
        # 매도 로직:
        # - 이 예시는 “매수한 수량 = 매도할 수량”으로 단순화합니다.
        # - 실제로는 잔고조회에서 종목별 보유수량을 가져와 매도해야 체결/부분체결에도 안전합니다.
        if buy_qty > 0:
            client.send_order("sell", target_code, buy_qty)
    else:
        logging.info("(테스트 모드) 청산 주문 시뮬레이션 완료")

    logging.info("=== 프로그램 종료 ===")


def main() -> None:
    configure_logging()

    parser = argparse.ArgumentParser()
    parser.add_argument("--app-key", required=True)
    parser.add_argument("--secret-key", required=True)
    parser.add_argument("--account", required=True)
    parser.add_argument("--base-url", default="https://mockapi.kiwoom.com")
    parser.add_argument("--test", action="store_true", help="시간 대기 없이 로직 강제 실행")

    args = parser.parse_args()

    # 10자리 계좌번호 체크
    if len(args.account) != 10:
        logging.warning("계좌번호가 10자리가 아닙니다. (예: 8116773911)")

    client = KiwoomREST(args.app_key, args.secret_key, args.account, args.base_url)
    run(client, test_mode=args.test)


if __name__ == "__main__":
    main()
