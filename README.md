# Kiwoom REST 모멘텀 데모 (32bit)

## 개요
- 09:00 시가 대비 09:20 종가 수익률을 기준으로 KODEX 200(069500) 또는 KODEX 인버스(114800)를 매수하는 예제.
- 1분봉 데이터를 읽어 09:00~09:20 구간의 분봉 가격/수익률을 `database/YYYYMMDD/minute_returns.parquet`(없으면 CSV 백업)로 누적 저장해 그리드 서치 등에 활용.

## 준비물
- Windows + 32bit Python 3.11 (예: `py -3.11-32`)
- venv: `py -3.11-32 -m venv venv32`
- 활성화: `.\venv32\Scripts\Activate.ps1`
- 패키지 설치: `python -m pip install --upgrade pip` 후 `python -m pip install -r requirements.txt`
  - parquet 엔진이 필요하면 `python -m pip install pyarrow`(권장) 또는 `fastparquet` 추가 설치
- VS Code/PyCharm 인터프리터: `.\venv32\Scripts\python.exe`로 지정

## 주요 스크립트
- `kiwoom_kospi_strategy.py`
  - 사용법:
    ```
    python kiwoom_kospi_strategy.py \
      --app-key <APP_KEY> \
      --secret-key <SECRET_KEY> \
      --account <계좌번호10자리> \
      [--base-url https://mockapi.kiwoom.com]  # 실전은 https://api.kiwoom.com
    ```
  - 동작:
    - ka10080으로 1분봉 조회 → 09:00/09:20 가격 파싱 → 수익률 계산 → Long(069500) 또는 Short 대체(114800) 결정
    - 현금 3%로 시장가 주문(kt10000/현금매수)
    - 분봉 로그를 `database/YYYYMMDD/minute_returns.parquet`에 누적(`date,time,minute_offset,code,price,ret_from_start`)
    - parquet 저장 실패 시 동일 위치에 `minute_returns.csv`로 백업
- `kiwoom_api.py`
  - 토큰 발급, 일봉 조회, 잔고 조회, 주문 예제. 인자/환경변수로 엔드포인트와 키를 지정.

## 문제 해결 팁
- pip 오류 시 `python -m pip ...` 형태로 실행하면 잘못된 `pip.exe` 경로 문제를 피할 수 있음.
- venv를 이동/복사했다면 새로 생성하는 것이 안전: `Remove-Item venv32 -Recurse -Force` → 재생성.
- 503/429 등 API 에러는 재시도하거나 호출 간 대기 시간을 늘려 확인.
