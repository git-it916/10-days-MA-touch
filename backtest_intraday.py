# backtest_intraday.py
from __future__ import annotations

import datetime as dt
import itertools
from pathlib import Path
from typing import Iterable, Optional, Tuple, Dict, Any

import numpy as np
import pandas as pd
import requests

COST_ROUNDTRIP = 0.0005  # 0.05% 슬리피지+수수료


def _to_float(val: Any, scale: float = 1.0, abs_value: bool = False) -> float:
    try:
        num = float(str(val).replace(",", "")) / scale
        return abs(num) if abs_value else num
    except Exception:
        return 0.0


def _parse_datetime(dt_str: str, tm_str: str) -> Optional[pd.Timestamp]:
    digits_date = "".join(ch for ch in dt_str if ch.isdigit())
    digits_tm = "".join(ch for ch in tm_str if ch.isdigit())
    if len(digits_tm) >= 12:
        # 이미 YYYYMMDDHHMMSS 형태일 가능성
        s = digits_tm[:14]
    elif len(digits_date) >= 8 and len(digits_tm) >= 4:
        s = digits_date[:8] + digits_tm[:6].ljust(6, "0")
    else:
        return None
    try:
        return pd.to_datetime(s, format="%Y%m%d%H%M%S")
    except Exception:
        return None


def fetch_kospi_min_1m_from_api(
    app_key: str,
    secret_key: str,
    base_url: str = "https://mockapi.kiwoom.com",
    inds_cd: str = "001",  # 001: KOSPI, 101: KOSDAQ, 201: KOSPI200
    session: Optional[requests.Session] = None,
    token: Optional[str] = None,
) -> pd.DataFrame:
    """
    키움 REST (ka20005) 업종분봉조회로 KOSPI 1분봉을 불러온다.
    - 가격 필드는 100배 정수로 오므로 100으로 나눔.
    """
    session = session or requests.Session()
    if (args.app_key and args.secret_key) and not token:
        token_url = f"{base_url.rstrip('/')}/oauth2/token"
        payload = {"grant_type": "client_credentials", "appkey": app_key, "secretkey": secret_key}
        resp = session.post(token_url, json=payload, headers={"Content-Type": "application/json;charset=UTF-8"}, timeout=10)
        if resp.status_code >= 400:
            raise SystemExit(f"토큰 발급 실패: {resp.status_code} {resp.text}")
        token = resp.json().get("access_token") or resp.json().get("token")
        if not token:
            raise SystemExit(f"토큰이 응답에 없습니다: {resp.text[:200]}")

    url = f"{base_url.rstrip('/')}/api/dostk/chart"
    headers = {
        "Authorization": f"Bearer {token}",
        "AppKey": app_key,
        "AppSecret": secret_key,
        "api-id": "ka20005",
        "Content-Type": "application/json; charset=utf-8",
    }
    body = {"inds_cd": inds_cd, "tic_scope": "1"}  # 1분봉
    resp = session.post(url, headers=headers, json=body, timeout=10)
    if (args.app_key and args.secret_key) and resp.status_code >= 400:
        raise SystemExit(f"업종분봉 조회 실패: {resp.status_code} {resp.text}")
    data = resp.json()
    items = (
        data.get("inds_min_pole_qry")
        or data.get("inds_dt_pole_qry")
        or data.get("inds_dt_pole_qty")
        or data.get("rec")
        or []
    )
    if not items:
        raise SystemExit(f"분봉 데이터가 비어 있습니다: {data}")

    rows = []
    for it in items:
        dt_str = str(it.get("dt") or "")
        tm_str = str(it.get("cntr_tm") or "")
        ts = _parse_datetime(dt_str, tm_str)
        if ts is None:
            continue
        rows.append(
            {
                "datetime": ts,
                "date": ts.date(),
                "time": ts.strftime("%H:%M"),
                "open": _to_float(it.get("open_pric"), scale=100),
                "high": _to_float(it.get("high_pric"), scale=100),
                "low": _to_float(it.get("low_pric"), scale=100),
                "close": _to_float(it.get("cur_prc"), scale=100, abs_value=True),
                "volume": _to_float(it.get("trde_qty")),
                "symbol": inds_cd,
            }
        )
    if not rows:
        raise SystemExit("파싱된 행이 없습니다.")
    df = pd.DataFrame(rows).sort_values("datetime").reset_index(drop=True)
    return df


def fetch_foreign_net_prev_day(
    app_key: str,
    secret_key: str,
    base_dt: str,
    base_url: str = "https://mockapi.kiwoom.com",
    mkt_tp: str = "0",      # 0: 코스피, 1: 코스닥
    amt_qty_tp: str = "0",  # 0: 금액, 1: 수량
    stex_tp: str = "3",     # 3: KRX합산 (문서 예제)
    inds_cd: str = "001",   # 001: KOSPI 지수 코드
    session: Optional[requests.Session] = None,
    token: Optional[str] = None,
    max_retries: int = 3,
    retry_sleep: float = 2.0,
) -> Optional[float]:
    """
    전일 외국인 순매수 금액 (업종별투자자순매수요청 ka10051)
    """
    session = session or requests.Session()
    if (args.app_key and args.secret_key) and not token:
        token_url = f"{base_url.rstrip('/')}/oauth2/token"
        payload = {"grant_type": "client_credentials", "appkey": app_key, "secretkey": secret_key}
        resp = session.post(token_url, json=payload, headers={"Content-Type": "application/json;charset=UTF-8"}, timeout=10)
        if resp.status_code >= 400:
            raise SystemExit(f"토큰 발급 실패: {resp.status_code} {resp.text}")
        token = resp.json().get("access_token") or resp.json().get("token")
        if not token:
            raise SystemExit(f"토큰이 응답에 없습니다: {resp.text[:200]}")

    url = f"{base_url.rstrip('/')}/api/dostk/sect"
    headers = {
        "Authorization": f"Bearer {token}",
        "AppKey": app_key,
        "AppSecret": secret_key,
        "api-id": "ka10051",
        "Content-Type": "application/json; charset=utf-8",
    }
    body = {
        "mkt_tp": mkt_tp,
        "amt_qty_tp": amt_qty_tp,
        "base_dt": base_dt,
        "stex_tp": stex_tp,
    }
    for attempt in range(max_retries):
        resp = session.post(url, headers=headers, json=body, timeout=10)
        if resp.status_code == 429 and attempt < max_retries - 1:
            time.sleep(retry_sleep)
            continue
        if resp.status_code >= 400:
            raise SystemExit(f"외국인 수급 조회 실패: {resp.status_code} {resp.text}")
        break
    data = resp.json()
    items = data.get("inds_netprps") or data.get("rec") or []
    for it in items:
        if str(it.get("inds_cd", "")).startswith(inds_cd):
            val = _to_float(it.get("frgnr_netprps"))
            return val
    return None


def load_intraday_1m(
    csv_path: str = "data/intraday_1m.csv",
    symbol: str = "069500",
    app_key: Optional[str] = None,
    secret_key: Optional[str] = None,
    base_url: str = "https://mockapi.kiwoom.com",
    inds_cd: str = "001",
    session: Optional[requests.Session] = None,
    token: Optional[str] = None,
    start_date: Optional[str] = None,  # YYYYMMDD or YYYY-MM-DD
    end_date: Optional[str] = None,
    time_start: str = "",
    time_end: str = "",
    force_refresh: bool = False,
    keep_times: Optional[Iterable[str]] = None,
) -> pd.DataFrame:
    """
    1분봉 로드.
    - CSV가 있으면 사용.
    - 없으면 키움 REST(ka20005)로 KOSPI 지수 분봉을 조회해 사용.
    """
    path = Path(csv_path)
    need_fetch = force_refresh or (not path.exists())
    df: Optional[pd.DataFrame] = None

    if not need_fetch:
        df = pd.read_csv(path)
        if "datetime" not in df.columns:
            dt_col = next((c for c in df.columns if c.lower() in ("datetime", "date", "time")), None)
            if dt_col:
                df = df.rename(columns={dt_col: "datetime"})
            else:
                raise SystemExit("datetime 컬럼을 찾을 수 없습니다.")
        df["datetime"] = pd.to_datetime(df["datetime"])
        df["date"] = df["datetime"].dt.date
        df["time"] = df["datetime"].dt.strftime("%H:%M")
        if "symbol" not in df.columns:
            df["symbol"] = symbol
        if "close" in df.columns:
            df["close"] = df["close"].astype(float).abs()
        df = df[["datetime", "date", "time", "open", "high", "low", "close", "symbol"]]
        # 필요한 시간(09:00, 10:00) 중 하나라도 없으면 다시 fetch
        sample_dates = df["date"].unique()
        missing_time = False
        if time_start and time_end:
            for t in (time_start, time_end):
                if df.loc[df["time"] == t].empty:
                    missing_time = True
                    break
        if missing_time:
            print(f"로컬 CSV에 필요한 시간대({time_start}~{time_end}) 데이터가 없어 API 재수집을 시도합니다.")
            need_fetch = True

    if need_fetch:
        if not app_key or not secret_key:
            raise SystemExit("CSV가 없고 API 키가 없습니다. --app-key / --secret-key 또는 CSV 파일을 제공하세요.")
        df = fetch_kospi_min_1m_from_api(
            app_key=app_key,
            secret_key=secret_key,
            base_url=base_url,
            inds_cd=inds_cd,
            session=session,
            token=token,
        )
        df = df[["datetime", "date", "time", "open", "high", "low", "close", "symbol"]]
        save_dir = Path("database") / "intraday_data"
        save_dir.mkdir(parents=True, exist_ok=True)
        date_str = dt.datetime.now().strftime("%Y%m%d")
        save_path = save_dir / f"{date_str}_intra.csv"
        df.to_csv(save_path, index=False, encoding="utf-8")
        print(f"수집 데이터 저장: {save_path}")

    # 날짜 필터링
    if start_date or end_date:
        def _norm(s: str) -> str:
            digits = "".join(ch for ch in s if ch.isdigit())
            if len(digits) == 8:
                return f"{digits[:4]}-{digits[4:6]}-{digits[6:]}"
            return s
        start_dt = pd.to_datetime(_norm(start_date)) if start_date else None
        end_dt = pd.to_datetime(_norm(end_date)) if end_date else None
    else:
        # 기본: 데이터의 연도를 기준으로 그 해 12월 전체
        year = pd.to_datetime(df["date"]).dt.year.min()
        start_dt = pd.to_datetime(f"{year}-12-01")
        end_dt = pd.to_datetime(f"{year}-12-31")

    if start_dt is not None:
        df = df[pd.to_datetime(df["date"]) >= start_dt]
    if end_dt is not None:
        df = df[pd.to_datetime(df["date"]) <= end_dt]

    # 시간 필터링(필요 구간만)
    if time_start and time_end:
        df = df[(df["time"] >= time_start) & (df["time"] <= time_end)]
    elif time_start:
        df = df[df["time"] >= time_start]
    elif time_end:
        df = df[df["time"] <= time_end]

    # 특정 시각만 유지 (예: 09:00~09:05, 10:00)
    if keep_times:
        keep_set = set(keep_times)
        df = df[df["time"].isin(keep_set)]

    return df

def _time_str(base: Tuple[int, int], minutes: int) -> str:
    h, m = base
    t = (dt.datetime(2000, 1, 1, h, m) + dt.timedelta(minutes=minutes)).time()
    return f"{t:%H:%M}"

def _add_minutes(hhmm: str, minutes: int) -> str:
    hhmm = hhmm.strip()
    try:
        h, m = hhmm.split(":")
        base_dt = dt.datetime(2000, 1, 1, int(h), int(m))
    except Exception:
        raise SystemExit(f"잘못된 시각 형식입니다: {hhmm!r} (예: 09:00)")
    return (base_dt + dt.timedelta(minutes=minutes)).strftime("%H:%M")

def _pick_price(day: pd.DataFrame, target: str) -> Optional[float]:
    exact = day.loc[day["time"] == target, "close"]
    if not exact.empty:
        return float(exact.iloc[0])
    before = day.loc[day["time"] < target, "close"]
    if not before.empty:
        return float(before.iloc[-1])
    return None

def simulate_day(
    day: pd.DataFrame,
    foreign_net: Optional[float],
    cost: float,
    entry_time: str = "09:02",
    exit_time: str = "10:00",
) -> Optional[float]:
    """
    전일 외국인 순매수 > 0일 때만 참여.
    09:00 시가 vs 09:02 종가로 방향 결정, 10:00 종가에 청산.
    """
    if foreign_net is None or foreign_net <= 0:
        return None

    day = day[(day["time"] >= "09:00") & (day["time"] <= exit_time)]
    if day.empty:
        return None

    open_0900 = _pick_price(day, "09:00")
    ref_close = _pick_price(day, entry_time)
    exit_px = _pick_price(day, exit_time)
    if open_0900 is None or ref_close is None or exit_px is None:
        return None

    direction = 1 if ref_close > open_0900 else -1
    gross_ret = direction * (exit_px - open_0900) / open_0900
    net_ret = gross_ret - cost
    return net_ret

def run_grid_search(
    df: pd.DataFrame,
    n_values: Iterable[int] = range(2, 3, 1),  # 고정: 09:02
    m_values: Iterable[int] = range(60, 61, 1),  # 고정: 10:00 (09:00+60)
    cost: float = COST_ROUNDTRIP,
    foreign_net_map: Optional[Dict[Any, float]] = None,
) -> pd.DataFrame:
    results = []
    by_date = df.groupby("date")

    for n, m in itertools.product(n_values, m_values):
        daily_rets = []
        for _, day in by_date:
            foreign_net = None
            if foreign_net_map is not None:
                foreign_net = foreign_net_map.get(day["date"].iloc[0])
            r = simulate_day(
                day,
                foreign_net=foreign_net,
                cost=cost,
                entry_time=_time_str((9, 0), n),
                exit_time=_time_str((9, 0), m),
            )
            if r is not None:
                daily_rets.append(r)
        if not daily_rets:
            continue
        rets = pd.Series(daily_rets)
        cum_return = float((1 + rets).prod() - 1)
        vol = rets.std(ddof=1)
        sharpe = float((rets.mean() / vol) * np.sqrt(252)) if vol > 0 else 0.0
        curve = (1 + rets).cumprod()
        dd = (curve / curve.cummax()) - 1
        mdd = float(dd.min())
        results.append(
            {
                "n": n,
                "m": m,
                "trades": len(rets),
                "cum_return": cum_return,
                "sharpe": sharpe,
                "mdd": mdd,
            }
        )

    if not results:
        return pd.DataFrame(columns=["n", "m", "trades", "cum_return", "sharpe", "mdd"])
    return pd.DataFrame(results).sort_values(["m", "n"]).reset_index(drop=True)

def simulate_day_v2(
    day: pd.DataFrame,
    foreign_net: Optional[float],
    cost: float,
    base_time: str,
    n: int,
    m: int,
    *,
    auto_base_time: bool = True,
    foreign_filter: bool = True,
) -> Optional[float]:
    if foreign_filter and (foreign_net is None or foreign_net <= 0):
        return None
    if day.empty:
        return None

    base_time_used = base_time
    if auto_base_time and day.loc[day["time"] <= base_time].empty:
        base_time_used = str(day.iloc[0]["time"])

    entry_time = _add_minutes(base_time_used, n)
    exit_time = _add_minutes(base_time_used, m)

    day_upto_exit = day[day["time"] <= exit_time]
    if day_upto_exit.empty:
        return None

    open_base = _pick_price(day_upto_exit, base_time_used)
    ref_close = _pick_price(day_upto_exit, entry_time)
    exit_px = _pick_price(day_upto_exit, exit_time)
    if open_base is None or ref_close is None or exit_px is None:
        return None

    direction = 1 if ref_close > open_base else -1
    gross_ret = direction * (exit_px - open_base) / open_base
    return gross_ret - cost

def run_grid_search_v2(
    df: pd.DataFrame,
    n_values: Iterable[int],
    m_values: Iterable[int],
    *,
    cost: float = COST_ROUNDTRIP,
    foreign_net_map: Optional[Dict[Any, float]] = None,
    base_time: str = "09:00",
    auto_base_time: bool = True,
    foreign_filter: bool = True,
) -> pd.DataFrame:
    results = []
    by_date = df.groupby("date")

    for n, m in itertools.product(n_values, m_values):
        daily_rets = []
        for _, day in by_date:
            foreign_net = None
            if foreign_net_map is not None:
                foreign_net = foreign_net_map.get(day["date"].iloc[0])
            r = simulate_day_v2(
                day,
                foreign_net=foreign_net,
                cost=cost,
                base_time=base_time,
                n=n,
                m=m,
                auto_base_time=auto_base_time,
                foreign_filter=foreign_filter,
            )
            if r is not None:
                daily_rets.append(r)
        if not daily_rets:
            continue

        rets = pd.Series(daily_rets)
        cum_return = float((1 + rets).prod() - 1)
        vol = rets.std(ddof=1)
        sharpe = float((rets.mean() / vol) * np.sqrt(252)) if vol > 0 else 0.0
        curve = (1 + rets).cumprod()
        dd = (curve / curve.cummax()) - 1
        mdd = float(dd.min())
        results.append(
            {
                "n": n,
                "m": m,
                "trades": len(rets),
                "cum_return": cum_return,
                "sharpe": sharpe,
                "mdd": mdd,
            }
        )

    if not results:
        return pd.DataFrame(columns=["n", "m", "trades", "cum_return", "sharpe", "mdd"])
    return pd.DataFrame(results).sort_values(["m", "n"]).reset_index(drop=True)

def plot_heatmap(df: pd.DataFrame, out_path: str = "grid_heatmap.png") -> None:
    try:
        import seaborn as sns
        import matplotlib.pyplot as plt
    except ImportError:
        raise SystemExit("Heatmap을 그리려면 seaborn/matplotlib가 필요합니다. `python -m pip install seaborn matplotlib`")
    pivot = df.pivot(index="m", columns="n", values="cum_return")
    plt.figure(figsize=(8, 5))
    sns.heatmap(pivot, annot=True, fmt=".2%", cmap="RdYlGn", cbar_kws={"format": "%.0f%%"})
    plt.title("Total Return Heatmap (cum_return)")
    plt.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"Heatmap saved: {out_path}")

if __name__ == "__main__":
    import argparse
    import time

    parser = argparse.ArgumentParser(description="KOSPI 1분봉 모멘텀 전략 그리드 서치")
    parser.add_argument("--csv", default="data/intraday_1m.csv", help="CSV 경로 (없으면 API 호출)")
    parser.add_argument("--app-key", default=None, help="키움 APP KEY (CSV 없을 때 필수)")
    parser.add_argument("--secret-key", default=None, help="키움 SECRET KEY (CSV 없을 때 필수)")
    parser.add_argument("--base-url", default="https://mockapi.kiwoom.com", help="키움 REST base URL")
    parser.add_argument("--inds-cd", default="001", help="업종코드 (001 KOSPI, 101 KOSDAQ, 201 KOSPI200)")
    parser.add_argument("--n-start", type=int, default=2, help="진입 시점(분) 기본 2 -> 09:02")
    parser.add_argument("--n-end", type=int, default=2)
    parser.add_argument("--n-step", type=int, default=1)
    parser.add_argument("--m-start", type=int, default=60, help="청산 시점(분) 기본 60 -> 10:00")
    parser.add_argument("--m-end", type=int, default=60)
    parser.add_argument("--m-step", type=int, default=1)
    parser.add_argument("--base-time", default="09:00", help="기준 시각(HH:MM). n/m은 이 시각 기준 분 오프셋")
    parser.add_argument(
        "--auto-base-time",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="데이터에 base-time 이전 구간이 없으면(예: 장중 일부만 수집) 첫 캔들을 기준 시각으로 자동 보정",
    )
    parser.add_argument(
        "--foreign-filter",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="전일 외국인 순매수 > 0 조건 적용 여부",
    )
    parser.add_argument("--retry-sleep", type=float, default=2.0, help="429 발생 시 재시도 전 대기(초)")
    parser.add_argument("--retry-max", type=int, default=3, help="429 재시도 횟수")
    parser.add_argument("--start-date", default=None, help="YYYYMMDD, 미지정 시 데이터 연도 12월 1일부터 필터")
    parser.add_argument("--end-date", default=None, help="YYYYMMDD, 미지정 시 데이터 연도 12월 31일까지 필터")
    parser.add_argument("--time-start", default="", help="시작 시간 필터 (예: 09:00). 비우면 적용 안 함")
    parser.add_argument("--time-end", default="", help="종료 시간 필터 (예: 10:00). 비우면 적용 안 함")
    parser.add_argument("--force-refresh", action="store_true", help="로컬 CSV 무시하고 API 강제 수집")
    parser.add_argument(
        "--keep-times",
        default="",
        help="콤마로 구분한 시각 목록만 유지 (기본: 09:00~09:05,10:00)",
    )
    parser.add_argument("--heatmap", action="store_true", help="히트맵 PNG 저장")
    args = parser.parse_args()

    n_values = range(args.n_start, args.n_end + 1, args.n_step)
    m_values = range(args.m_start, args.m_end + 1, args.m_step)

    if args.foreign_filter and (not args.app_key or not args.secret_key):
        raise SystemExit("이 전략은 외국인 순매수 조건을 쓰므로 --app-key, --secret-key가 필요합니다.")

    # 세션/토큰을 한 번만 발급해 재사용 (429 완화)
    session = requests.Session()
    token = None
    token_url = f"{args.base_url.rstrip('/')}/oauth2/token"
    payload = {"grant_type": "client_credentials", "appkey": args.app_key or "", "secretkey": args.secret_key or ""}
    resp = session.post(token_url, json=payload, headers={"Content-Type": "application/json;charset=UTF-8"}, timeout=10)
    if resp.status_code == 429:
        time.sleep(2)
        resp = session.post(token_url, json=payload, headers={"Content-Type": "application/json;charset=UTF-8"}, timeout=10)
    if (args.app_key and args.secret_key) and resp.status_code >= 400:
        raise SystemExit(f"토큰 발급 실패: {resp.status_code} {resp.text}")
    token = (resp.json().get("access_token") or resp.json().get("token")) if (args.app_key and args.secret_key) else None
    if (args.app_key and args.secret_key) and not token:
        raise SystemExit(f"토큰이 응답에 없습니다: {resp.text[:200]}")

    data = load_intraday_1m(
        csv_path=args.csv,
        symbol="KOSPI",
        app_key=args.app_key,
        secret_key=args.secret_key,
        base_url=args.base_url,
        inds_cd=args.inds_cd,
        session=session,
        token=token,
        start_date=args.start_date,
        end_date=args.end_date,
        time_start=args.time_start,
        time_end=args.time_end,
        force_refresh=args.force_refresh,
        keep_times=[t.strip() for t in args.keep_times.split(",") if t.strip()],
    )

    # 전일 외국인 순매수 지도 생성
    foreign_net_map: Optional[Dict[Any, float]] = None
    if args.foreign_filter:
        foreign_net_map = {}
        for d in sorted(data["date"].unique()):
            prev_day = (pd.to_datetime(d) - pd.Timedelta(days=1)).strftime("%Y%m%d")
            val = fetch_foreign_net_prev_day(
                app_key=args.app_key,
                secret_key=args.secret_key,
                base_dt=prev_day,
                base_url=args.base_url,
                mkt_tp="0",
                amt_qty_tp="0",
                stex_tp="3",
                inds_cd=args.inds_cd,
                session=session,
                token=token,
                max_retries=args.retry_max,
                retry_sleep=args.retry_sleep,
            )
            foreign_net_map[d] = val

    grid = run_grid_search_v2(
        data,
        n_values=n_values,
        m_values=m_values,
        foreign_net_map=foreign_net_map,
        base_time=args.base_time,
        auto_base_time=args.auto_base_time,
        foreign_filter=args.foreign_filter,
    )
    print(grid)
    if args.heatmap:
        plot_heatmap(grid)
