#!/usr/bin/env python3
"""
backtest_simple.py

“전일 외국인 수급 + 09:02 모멘텀 + 10:00 청산” 전략을 아주 단순하게(backtest 용도로) 점검하는 스크립트입니다.

왜 이 파일이 필요한가?
- `kiwoom_kospi_strategy.py`는 “실시간 실행(대기→진입→청산)” 중심입니다.
- 백테스트/리서치 단계에서는:
  - 과거 분봉 데이터(CSV)를 읽어서
  - 날짜별로 09:00/09:02/10:00 가격을 뽑고
  - 전략 수익률을 계산해
  - 결과를 표로 저장/요약
  하는 형태가 더 편합니다.

데이터 가정
- 기본 입력은 `database/intraday_data/*.csv` 형태의 1분봉 CSV입니다.
- 컬럼 예시(프로젝트 내 파일 기준):
  - datetime,date,time,open,high,low,close,symbol

중요: “인버스(114800) 수익률”을 정확히 재현하려면 ETF 가격 데이터가 필요합니다.
이 스크립트는 단순화를 위해 “지수의 반대 수익률”로 인버스를 근사합니다.
즉,
- LONG(지수):  (P10:00 - P09:02) / P09:02
- INVERSE(근사): -(P10:00 - P09:02) / P09:02
을 사용합니다.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

from kiwoom_kospi_strategy import KiwoomREST, configure_logging
from backtest_intraday import fetch_kospi_min_1m_from_api


def _parse_yyyymmdd(s: str) -> dt.date:
    """YYYYMMDD 문자열을 date로 파싱합니다."""
    return dt.datetime.strptime(s, "%Y%m%d").date()


def _format_yyyymmdd(d: dt.date) -> str:
    """date를 YYYYMMDD 문자열로 포맷합니다."""
    return d.strftime("%Y%m%d")


def _find_prev_foreign_net(
    client: KiwoomREST,
    base_date: dt.date,
    *,
    lookback_days: int = 10,
    cache: Optional[Dict[str, Optional[int]]] = None,
) -> Optional[int]:
    """
    base_date(백테스트 대상 날짜)의 “전일(또는 직전 영업일)” 외국인 순매수 값을 찾습니다.

    - 주말/공휴일은 데이터가 없을 수 있어, 최대 lookback_days만큼 뒤로 가며 조회합니다.
    - API 호출이 많아질 수 있으므로 cache로 결과를 저장합니다.
    """
    cache = cache if cache is not None else {}

    for offset in range(1, lookback_days + 1):
        d = base_date - dt.timedelta(days=offset)
        d_str = _format_yyyymmdd(d)

        if d_str in cache:
            logging.info(" -> 수급 캐시 사용: %s -> %s", d_str, cache[d_str])
            if cache[d_str] is not None:
                return cache[d_str]
            continue

        logging.info(" -> 전일 수급 조회 시도: %s (base=%s, offset=%d)", d_str, _format_yyyymmdd(base_date), offset)
        val = client.fetch_foreigner_net_buy(d_str)
        cache[d_str] = val
        if val is not None:
            logging.info(" -> 전일 수급 확정: %s -> %s원", d_str, f"{val:,}")
            return val

    logging.warning(" -> 전일 수급을 찾지 못했습니다(base=%s, lookback=%d)", _format_yyyymmdd(base_date), lookback_days)
    return None


def _safe_float(val: Any) -> float:
    """CSV 문자열/숫자를 안전하게 float로 바꿉니다."""
    try:
        return float(str(val).replace(",", ""))
    except Exception:
        return float("nan")


def _pick_close(df_day: pd.DataFrame, hhmm: str) -> Optional[float]:
    """
    특정 날짜의 1분봉 DataFrame에서 HH:MM 시각의 close를 반환합니다.

    - 입력 CSV에는 time이 '09:02'처럼 들어있으므로, hhmm='0902'를 '09:02'로 변환해 매칭합니다.
    - 해당 분이 없으면 None.
    """
    target = f"{hhmm[:2]}:{hhmm[2:]}"
    row = df_day.loc[df_day["time"] == target]
    if row.empty:
        return None
    # 같은 시각이 중복될 일이 거의 없지만, 혹시 모르니 마지막 값을 사용합니다.
    return _safe_float(row.iloc[-1]["close"])


def backtest(
    df_1m: pd.DataFrame,
    *,
    foreign_filter: bool,
    client: Optional[KiwoomREST],
    lookback_days: int,
    cost_roundtrip: float,
    print_daily: bool,
) -> pd.DataFrame:
    """
    단순 백테스트를 수행하고 결과(DataFrame)를 반환합니다.
    """
    # datetime 파싱/정렬은 백테스트의 기본 안전장치입니다.
    df = df_1m.copy()
    if "datetime" in df.columns:
        df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
    if "time" not in df.columns:
        # time 컬럼이 없으면 datetime에서 만들어 줍니다.
        if "datetime" not in df.columns:
            raise ValueError("입력 데이터에 'time'도 'datetime'도 없습니다.")
        df["time"] = df["datetime"].dt.strftime("%H:%M")

    df = df.dropna(subset=["date"])
    df = df.sort_values(["date", "time"]).reset_index(drop=True)

    results: list[dict[str, Any]] = []
    foreign_cache: Dict[str, Optional[int]] = {}

    for d in sorted(df["date"].unique()):
        day = df[df["date"] == d]
        if day.empty:
            continue

        logging.info("=== 날짜 처리 시작: %s (rows=%d) ===", _format_yyyymmdd(d), len(day))

        # 1) 전일 수급 필터(선택)
        prev_foreign_net: Optional[int] = None
        if foreign_filter:
            if client is None:
                raise ValueError("foreign_filter=True인데 client가 없습니다.")
            prev_foreign_net = _find_prev_foreign_net(
                client,
                d,
                lookback_days=lookback_days,
                cache=foreign_cache,
            )
            if prev_foreign_net is None:
                logging.info(" -> 수급 데이터 없음: 스킵")
                results.append(
                    {
                        "date": d,
                        "skip": True,
                        "skip_reason": "no_foreign_data",
                        "prev_foreign_net": None,
                    }
                )
                continue
            if prev_foreign_net <= 0:
                logging.info(" -> 전일 수급 음수/0: 스킵 (%s원)", f"{prev_foreign_net:,}")
                results.append(
                    {
                        "date": d,
                        "skip": True,
                        "skip_reason": "foreign_non_positive",
                        "prev_foreign_net": prev_foreign_net,
                    }
                )
                continue

        # 2) 09:00/09:02/10:00 가격 추출
        p_0900 = _pick_close(day, "0900")
        p_0902 = _pick_close(day, "0902")
        p_1000 = _pick_close(day, "1000")

        if p_0900 is None or p_0902 is None or p_1000 is None:
            logging.warning(" -> 가격 누락: 0900=%s 0902=%s 1000=%s (스킵)", p_0900, p_0902, p_1000)
            results.append(
                {
                    "date": d,
                    "skip": True,
                    "skip_reason": "missing_prices",
                    "prev_foreign_net": prev_foreign_net,
                    "p_0900": p_0900,
                    "p_0902": p_0902,
                    "p_1000": p_1000,
                }
            )
            continue

        # 3) 09:02 모멘텀 계산(09:00 대비)
        signal_ret = (p_0902 - p_0900) / p_0900 if p_0900 else 0.0
        direction = 1 if signal_ret > 0 else -1  # +1: LONG, -1: INVERSE(근사)

        # 4) 09:02 진입 → 10:00 청산 수익률(인버스는 -수익률로 근사)
        gross_ret = direction * ((p_1000 - p_0902) / p_0902 if p_0902 else 0.0)

        # 5) 거래비용 반영(왕복 비용을 단순히 수익률에서 차감)
        net_ret = gross_ret - cost_roundtrip

        if print_daily:
            logging.info(
                " -> signal(09:00→09:02)=%.4f, dir=%s, ret(09:02→10:00)=%.4f, net=%.4f",
                signal_ret,
                "LONG" if direction > 0 else "INVERSE(approx)",
                gross_ret,
                net_ret,
            )

        results.append(
            {
                "date": d,
                "skip": False,
                "skip_reason": "",
                "prev_foreign_net": prev_foreign_net,
                "p_0900": p_0900,
                "p_0902": p_0902,
                "p_1000": p_1000,
                "signal_ret_0900_0902": signal_ret,
                "direction": direction,
                "gross_ret_0902_1000": gross_ret,
                "net_ret_0902_1000": net_ret,
            }
        )

    return pd.DataFrame(results)


def _summarize_intraday_df(df: pd.DataFrame) -> str:
    """
    API/CSV로 얻은 분봉 DataFrame의 범위를 한 줄 요약 문자열로 반환합니다.
    - 출력 예: rows=900 dates=2025-12-15..2025-12-18 times=09:00..15:30
    """
    if df.empty:
        return "rows=0"
    date_min = str(df["date"].min()) if "date" in df.columns else "?"
    date_max = str(df["date"].max()) if "date" in df.columns else "?"
    time_min = str(df["time"].min()) if "time" in df.columns else "?"
    time_max = str(df["time"].max()) if "time" in df.columns else "?"
    return f"rows={len(df)} dates={date_min}..{date_max} times={time_min}..{time_max}"


def _merge_and_save_csv(df_new: pd.DataFrame, out_path: Path) -> None:
    """
    새로 가져온 분봉(df_new)을 out_path CSV에 병합 저장합니다.
    - 기존 파일이 있으면 concat + (date,time,symbol) 기준 중복 제거
    - 없으면 그대로 저장
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        df_old = pd.read_csv(out_path)
        df = pd.concat([df_old, df_new], ignore_index=True)
        subset = [c for c in ["date", "time", "symbol"] if c in df.columns]
        if subset:
            df = df.drop_duplicates(subset=subset, keep="last")
        df.to_csv(out_path, index=False, encoding="utf-8")
    else:
        df_new.to_csv(out_path, index=False, encoding="utf-8")


def main() -> None:
    configure_logging()

    parser = argparse.ArgumentParser(description="전일수급 + 09:02 모멘텀 + 10:00 청산 (단순 백테스트)")

    # 기존 실행 습관과 맞추기 위해 키 인자를 그대로 받습니다.
    # (CSV 기반만 돌릴 수도 있으므로 필수는 아님)
    parser.add_argument("--app-key", default="")
    parser.add_argument("--secret-key", default="")
    parser.add_argument("--account", default="")
    parser.add_argument("--base-url", default="https://mockapi.kiwoom.com")

    # (옵션) Kiwoom API에서 “한 번 호출로 얻을 수 있는 최대” 분봉 윈도우를 가져옵니다.
    # - ka20005는 body에 날짜 범위를 받지 않아서, “요청자가 임의로 과거 범위를 지정”하는 방식이 아닙니다.
    # - 따라서 이 옵션은 “서버가 내려주는 최대 윈도우(최근 N일/최근 N분)”를 그대로 가져와 저장/백테스트합니다.
    parser.add_argument("--fetch-api", action="store_true", help="API(ka20005)에서 분봉을 가져와 사용")
    parser.add_argument("--inds-cd", default="001", help="지수 코드(기본 001=KOSPI, 201=KOSPI200 등)")
    parser.add_argument(
        "--save-fetched",
        default="",
        help="(선택) API로 가져온 분봉을 이 경로에 저장. 기본은 저장 안 함.",
    )
    parser.add_argument(
        "--merge-fetched",
        action="store_true",
        help="--save-fetched 지정 시, 기존 CSV가 있으면 병합(append+dedup) 저장",
    )

    parser.add_argument(
        "--csv",
        default=str(Path("database") / "intraday_data" / "20251218_intra.csv"),
        help="입력 1분봉 CSV 경로",
    )
    parser.add_argument("--start-date", default="", help="YYYYMMDD (비우면 전체)")
    parser.add_argument("--end-date", default="", help="YYYYMMDD (비우면 전체)")

    # 수급 필터 on/off를 명시적으로 제어할 수 있게 합니다.
    parser.add_argument("--foreign-filter", action="store_true", help="전일 외국인 순매수(+) 필터 적용")
    parser.add_argument("--no-foreign-filter", action="store_true", help="수급 필터 미적용(항상 진행)")
    parser.add_argument("--foreign-lookback-days", type=int, default=10, help="전일 수급 탐색 최대 일수")

    parser.add_argument("--cost-roundtrip", type=float, default=0.0005, help="왕복 거래비용(단순 차감)")
    parser.add_argument("--print-daily", action="store_true", help="날짜별 결과 로그 출력")
    parser.add_argument("--out", default="", help="결과 CSV 저장 경로(선택)")

    args = parser.parse_args()

    # 1) 데이터 준비: (A) API fetch 또는 (B) 로컬 CSV
    if args.fetch_api:
        if not (args.app_key and args.secret_key):
            raise SystemExit("--fetch-api를 사용하려면 --app-key/--secret-key가 필요합니다.")

        logging.info("API 분봉 fetch 시작(최대 윈도우): base_url=%s inds_cd=%s", args.base_url, args.inds_cd)
        df = fetch_kospi_min_1m_from_api(
            app_key=args.app_key,
            secret_key=args.secret_key,
            base_url=args.base_url,
            inds_cd=args.inds_cd,
        )
        logging.info("API fetch 완료: %s", _summarize_intraday_df(df))

        if args.save_fetched:
            out_path = Path(args.save_fetched)
            if args.merge_fetched:
                _merge_and_save_csv(df, out_path)
                logging.info("API 데이터 병합 저장: %s", out_path)
            else:
                out_path.parent.mkdir(parents=True, exist_ok=True)
                df.to_csv(out_path, index=False, encoding="utf-8")
                logging.info("API 데이터 저장: %s", out_path)
    else:
        csv_path = Path(args.csv)
        if not csv_path.exists():
            raise SystemExit(f"CSV 파일을 찾을 수 없습니다: {csv_path}")

        logging.info("입력 CSV 로드: %s", csv_path)
        df = pd.read_csv(csv_path)
        logging.info("CSV 로드 완료: %s", _summarize_intraday_df(df))

    # 날짜 필터(선택)
    if args.start_date:
        start_d = _parse_yyyymmdd(args.start_date)
        df_date = pd.to_datetime(df["date"], errors="coerce").dt.date
        before = len(df)
        df = df[df_date >= start_d]
        logging.info("start-date 필터: %s -> %d -> %d", args.start_date, before, len(df))
    if args.end_date:
        end_d = _parse_yyyymmdd(args.end_date)
        df_date = pd.to_datetime(df["date"], errors="coerce").dt.date
        before = len(df)
        df = df[df_date <= end_d]
        logging.info("end-date 필터: %s -> %d -> %d", args.end_date, before, len(df))

    # foreign_filter 기본값:
    # - mockapi면 기본 OFF(데이터가 0이거나 의미가 없을 수 있음)
    # - 실계/실서버면 기본 ON을 권장
    foreign_filter = args.foreign_filter
    if args.no_foreign_filter:
        foreign_filter = False
    if (not args.foreign_filter) and (not args.no_foreign_filter):
        foreign_filter = ("mockapi.kiwoom.com" not in (args.base_url or ""))

    client: Optional[KiwoomREST] = None
    if foreign_filter:
        if not (args.app_key and args.secret_key):
            raise SystemExit("--foreign-filter를 사용하려면 --app-key/--secret-key가 필요합니다.")
        client = KiwoomREST(args.app_key, args.secret_key, args.account or "", args.base_url)
        # 토큰은 내부에서 자동 발급되지만, “진짜 실행 중”임을 보이기 위해 여기서 선발급합니다.
        client.authenticate()

    logging.info("백테스트 시작: foreign_filter=%s", foreign_filter)
    res = backtest(
        df,
        foreign_filter=foreign_filter,
        client=client,
        lookback_days=args.foreign_lookback_days,
        cost_roundtrip=args.cost_roundtrip,
        print_daily=args.print_daily,
    )

    if res.empty:
        logging.warning("결과가 비었습니다(필터/데이터/시간대 확인).")
        return

    # 요약 출력(스킵 제외)
    traded = res[res["skip"] == False].copy()  # noqa: E712 (pandas 비교)
    skipped = res[res["skip"] == True].copy()  # noqa: E712

    logging.info("=== 요약 ===")
    logging.info("총 일수: %d", len(res))
    logging.info("거래 일수: %d", len(traded))
    logging.info("스킵 일수: %d", len(skipped))

    if len(traded) > 0:
        avg = traded["net_ret_0902_1000"].mean()
        win = (traded["net_ret_0902_1000"] > 0).mean()
        cum = (1.0 + traded["net_ret_0902_1000"]).prod() - 1.0
        logging.info("평균 일수익률(net): %.4f", avg)
        logging.info("승률(net>0): %.2f%%", win * 100.0)
        logging.info("누적수익률(단순 복리): %.4f", cum)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        res.to_csv(out_path, index=False, encoding="utf-8")
        logging.info("결과 저장: %s", out_path)


if __name__ == "__main__":
    main()
