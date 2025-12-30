# KOSPI-SP 베타 Z-Score 전략 (REST 자동매매 예제)

이 레포는 `kospi_sp_beta.py` 하나로 동작하는 **KOSPI 지수 기반 퀀트 전략 예제**입니다.  
엑셀로 준비한 과거 데이터 + 오늘의 외부 지표(S&P500, VIX, 환율)를 합쳐 Z-Score를 계산하고, 조건이 맞으면 KOSPI ETF를 매수합니다.

> 참고: 클래스 이름은 `KiwoomREST`지만, 실제 호출 경로는 `/api/dostk/...` 형태(KIS 스타일)입니다.  
> 사용하려는 증권사/모의서버에 맞게 `--base-url`과 필요한 헤더/경로를 반드시 확인하세요.

---

## 1) 파일 구성

- `kospi_sp_beta.py` : 전략 실행 스크립트
- `requirements.txt` : 실행에 필요한 패키지 목록

---

## 2) 준비물

- Windows + Python 3.x (32bit 권장, `requirements.txt` 참고)
- 가상환경(예: `venv32`)
  - 생성: `py -3.11-32 -m venv venv32`
  - 활성화: `.\venv32\Scripts\Activate.ps1`
- 패키지 설치: `python -m pip install -r requirements.txt`

---

## 3) 엑셀 데이터 준비

스크립트는 아래 컬럼을 가진 엑셀 파일을 읽습니다.

- `공통날짜`
- `kospi_t`
- `SPX_t-1`
- `VIX_t-1`
- `FX_t`

엑셀 경로는 코드 상단의 `EXCEL_PATH`에서 지정합니다.

```python
EXCEL_PATH = r"C:\Users\...\kospi_sp500_filtered_longterm.xlsx"
```

---

## 4) 실행 방법

```powershell
.\venv32\Scripts\python.exe kospi_sp_beta.py `
  --app-key "APP_KEY" `
  --secret-key "SECRET_KEY" `
  --account "1234567890" `
```

- 실행 중에 아래 값을 직접 입력해야 합니다.
  - 어제 S&P500 종가
  - 어제 VIX 종가
  - 오늘 원/달러 환율

---

## 5) 전략 로직 요약

1. 과거 엑셀 데이터 + 오늘 입력값을 합쳐 수익률/베타/잔차 계산
2. 60일 롤링 베타로 잔차(residual) 계산 후 Z-Score 산출
3. 리스크 필터:
   - VIX 분위수(`VIX_Q`)  
   - 환율 쇼크 분위수(`FX_Q`)
4. 신호 결정:
   - `z <= -ENTRY` → Long
   - `z >= ENTRY` → Short(인버스)
   - `|z| <= EXIT` → 중립(청산)
   - 리스크 필터 위반/손절 기준 → 강제 청산

---

## 6) 주문 흐름(중요)

- 현재 보유 포지션을 **사용자에게 직접 질문**합니다.
- 기존 포지션 청산은 **자동화되어 있지 않으며** HTS에서 수동 매도하도록 안내합니다.
- 신규 진입 시에만 시장가 매수 주문을 전송합니다.
- 주문 전 `y/n`으로 최종 확인을 받습니다.

---

## 7) 자주 막히는 포인트

- 엑셀 로드 실패 → `openpyxl` 설치 여부 확인
- KOSPI 지수 조회 실패 → API 권한/경로/헤더 확인 또는 수동 입력
- 주문 실패 → 계좌번호/추가 인증/필수 필드가 누락되었는지 확인

---

## 8) 설정값(코드 상단에서 조정)

- `EXCEL_PATH` : 엑셀 위치
- `FIXED_INVEST_KRW` : 1회 진입 금액
- `ENTRY`, `EXIT`, `VIX_Q`, `FX_Q`, `STOP_LOSS_MULT`
- `CODE_LONG`, `CODE_SHORT`

---

## 9) 면책

이 코드는 학습/실험용 예제입니다. 실거래에 사용할 경우, API 스펙과 리스크 관리 로직을 충분히 검증하세요.
