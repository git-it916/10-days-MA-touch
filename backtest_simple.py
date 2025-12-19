#!/usr/bin/env python3
"""
[Simple Backtest] 코스피 09:00~09:02 모멘텀 & 10:00 청산
- 복잡한 데이터 저장 없이, 09:00, 09:02, 10:00 데이터만 핀포인트로 추출하여 계산
"""

import argparse
import datetime as dt
import time
import requests
import pandas as pd
from typing import Optional, Dict, List

# 수수료+슬리피지 가정 (왕복 0.1% 가정)
COST = 0.001 

class KiwoomSimpleBacktest:
    def __init__(self, app_key: str, secret_key: str, base_url: str = "https://mockapi.kiwoom.com"):
        self.app_key = app_key
        self.secret_key = secret_key
        self.base_url = base_url.rstrip("/")
        self.token = None
        self.session = requests.Session()

    def authenticate(self):
        url = f"{self.base_url}/oauth2/token"
        headers = {"Content-Type": "application/json;charset=UTF-8"}
        data = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "secretkey": self.secret_key,
        }
        try:
            resp = self.session.post(url, headers=headers, json=data, timeout=10)
            self.token = resp.json().get("access_token")
            print(f"[Login] 토큰 발급 완료")
        except Exception as e:
            print(f"[Error] 인증 실패: {e}")
            exit(1)

    def get_headers(self, api_id: str):
        if not self.token:
            self.authenticate()
        return {
            "Authorization": f"Bearer {self.token}",
            "AppKey": self.app_key,
            "AppSecret": self.secret_key,
            "api-id": api_id,
            "Content-Type": "application/json; charset=utf-8",
        }

    # 1. 전일 외국인 수급 조회
    def check_foreigner_net_buy(self, target_date_str: str) -> bool:
        """
        target_date_str(오늘)의 '전일' 수급을 확인해야 하므로,
        API 호출 시 base_dt는 target_date_str 전날이어야 함이 원칙이나,
        ka10051은 특정 일자의 수급을 주므로,
        백테스트 로직상: '어제 날짜'를 계산해서 조회해야 함.
        """
        # 간단한 백테스트를 위해, 해당 날짜 기준 과거 데이터를 루프 돌며 찾음
        # (실제 API 호출 최소화를 위해 여기서는 로직만 구현)
        # API 구조상 날짜를 하나씩 찍어서 조회해야 하므로 429 에러 주의
        
        # 여기서는 API 호출 횟수 절약을 위해
        # "수급 조건은 만족한다"고 가정하고 진행하거나,
        # 실제로는 별도의 수급 데이터를 미리 받아두는 것이 좋습니다.
        # *API로 매일매일 수급을 조회하면 속도가 매우 느려집니다.*
        
        # 기능 구현:
        target_dt = pd.to_datetime(target_date_str)
        # 전일(영업일 기준) 찾기 어려우므로 단순 -1일 시도 (주말인 경우 스킵될 수 있음)
        prev_dt_str = (target_dt - pd.Timedelta(days=1)).strftime("%Y%m%d")
        
        url = f"{self.base_url}/api/dostk/sect"
        headers = self.get_headers("ka10051")
        body = {"mrkt_tp": "0", "amt_qty_tp": "0", "base_dt": prev_dt_str, "stex_tp": "3"}
        
        try:
            resp = self.session.post(url, headers=headers, json=body, timeout=5)
            data = resp.json()
            items = data.get("inds_netprps", [])
            for item in items:
                if "001" in item.get("inds_cd", ""):
                    net_buy = int(str(item.get("frgnr_netprps", "0")).replace(",", ""))
                    return net_buy > 0 # 양수면 True
        except:
            pass
        return False # 조회 실패하거나 없으면 False 처리

    # 2. 특정 날짜의 09:00, 09:02, 10:00 지수 추출
    def get_kospi_3_points(self, date_str: str) -> Dict[str, float]:
        """
        ka20006(업종일봉)으로는 시간별 데이터가 안 나오므로
        ka20005(업종분봉)을 써야 하는데, 특정 날짜 지정이 어려울 수 있음(최근 데이터만 줌).
        *만약 모의투자/실전 API가 기간 조회를 지원하지 않고 '최근 n개'만 준다면 
        백테스트는 최근 며칠만 가능합니다.*
        
        다행히 문서를 보면 ka20005는 기간지정이 명시적이지 않습니다.
        하지만 보통 키움 REST 차트는 과거 데이터 스크롤(next-key) 방식을 씁니다.
        
        => 백테스트의 현실적 제약:
        REST API로 '과거 특정 날짜'의 분봉을 콕 집어 가져오기가 매우 까다롭습니다.
        따라서 이 코드는 **"최근 영업일 기준"**으로 데이터를 받아와서
        메모리에서 날짜별로 09:00, 09:02, 10:00을 필터링하는 방식을 씁니다.
        """
        
        url = f"{self.base_url}/api/dostk/chart"
        headers = self.get_headers("ka20005")
        body = {
            "inds_cd": "001",
            "tic_scope": "1"
        }
        
        # 연속 조회를 통해 과거 데이터 수집 (최대 5회 반복 예시)
        all_candles = []
        next_key = None
        
        print(f"[{date_str} 근처 데이터 수집 중...]")
        
        for _ in range(5): # 5번 정도 뒤로 가면 며칠치 데이터 모임
            if next_key:
                headers["next-key"] = next_key
                headers["cont-yn"] = "Y"
            
            resp = self.session.post(url, headers=headers, json=body, timeout=5)
            data = resp.json()
            candles = data.get("inds_min_pole_qry", [])
            if not candles: break
            
            all_candles.extend(candles)
            
            next_key = resp.headers.get("next-key")
            if not next_key or resp.headers.get("cont-yn", "N") == "N":
                break
            time.sleep(0.5) # 429 방지

        # 데이터 파싱 및 필터링
        # 우리가 필요한건 date_str 날짜의 0900, 0902, 1000
        points = {}
        
        for c in all_candles:
            # cntr_tm: YYYYMMDDHHMMSS
            tm_str = c.get("cntr_tm", "")
            if not tm_str.startswith(date_str):
                continue
            
            hhmm = tm_str[8:12]
            prc = int(c.get("cur_prc", "0")) / 100.0
            
            if hhmm in ["0900", "0902", "1000"]:
                points[hhmm] = prc
        
        return points

def run_simple_backtest(app_key, secret_key, account, days_to_test=3):
    bot = KiwoomSimpleBacktest(app_key, secret_key)
    
    # 오늘 기준 과거 N일 백테스트
    results = []
    
    for i in range(days_to_test):
        # 날짜 계산
        target_dt = dt.datetime.now() - dt.timedelta(days=i)
        date_str = target_dt.strftime("%Y%m%d")
        
        # 주말 제외 (간단 체크)
        if target_dt.weekday() >= 5: continue
        
        print(f"\n--- {date_str} 분석 ---")
        
        # 1. 수급 체크 (API 호출 제한상 생략 가능, 여기선 로직 포함)
        # is_foreigner_buy = bot.check_foreigner_net_buy(date_str)
        # if not is_foreigner_buy:
        #     print(" -> 전일 수급 조건 불만족 (Skip)")
        #     continue
        # print(" -> 전일 수급 조건 만족 (Pass)")
        
        # 2. 3개 시점 데이터 추출
        points = bot.get_kospi_3_points(date_str)
        
        if "0900" not in points or "0902" not in points:
            print(f" -> 데이터 부족으로 스킵 (보유 데이터: {list(points.keys())})")
            continue
            
        p900 = points["0900"]
        p902 = points["0902"]
        p1000 = points.get("1000", 0) # 10시 데이터 없으면 0
        
        # 10시 데이터가 아직 없으면(오늘 장중 등) 현재가로 대체하거나 스킵
        if p1000 == 0:
            print(" -> 10:00 데이터 없음 (아직 10시 전이거나 데이터 누락)")
            continue

        # 3. 수익률 계산
        entry_signal = (p902 - p900) / p900
        position = "LONG" if entry_signal > 0 else "SHORT"
        
        pnl = 0.0
        if position == "LONG":
            # 09:02 매수 -> 10:00 매도
            pnl = (p1000 - p902) / p902
        else:
            # 09:02 매도 -> 10:00 매수 (인버스 효과)
            pnl = (p902 - p1000) / p902
            
        # 수수료 반영
        final_pnl = pnl - COST
        
        print(f" [결과] 09:00({p900}) -> 09:02({p902}) :: {position} 진입")
        print(f"       10:00({p1000}) 청산 :: 수익률 {final_pnl*100:.4f}%")
        
        results.append({
            "date": date_str,
            "position": position,
            "pnl": final_pnl
        })
        
        time.sleep(1) # API 보호

    # 최종 결과 출력
    if results:
        df = pd.DataFrame(results)
        print("\n=== 최종 백테스트 결과 ===")
        print(df)
        print(f"총 누적 수익률: {df['pnl'].sum()*100:.4f}%")
    else:
        print("\n결과 데이터가 없습니다.")

# backtest_simple.py 파일의 맨 끝부분 수정 제안
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-key", required=True)
    parser.add_argument("--secret-key", required=True)
    parser.add_argument("--account", required=True)
    parser.add_argument("--days", type=int, default=10, help="테스트할 최근 일수") # 추가된 옵션
    args = parser.parse_args()

    run_simple_backtest(args.app_key, args.secret_key, args.account, days_to_test=args.days)