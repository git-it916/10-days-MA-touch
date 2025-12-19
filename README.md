# Kiwoom KOSPI Strategy (REST, Windows 32bit)

이 레포는 키움 REST API로 **KOSPI 지수 분봉(ka20005)** 과 **전일 외국인 수급(ka10051)** 을 활용해, 아침 모멘텀 기반으로 ETF를 매수하고 10:00에 청산하는 예제를 포함합니다.

이 문서는 `kiwoom_kospi_strategy.py`만 꼼꼼히 설명합니다.

---

## 1) 준비물

- Windows + 32bit Python 3.x 권장
- 가상환경(예: `venv32`)
  - 생성: `py -3.11-32 -m venv venv32`
  - 활성화: `.\venv32\Scripts\Activate.ps1`
- 패키지 설치: `python -m pip install -r requirements.txt`

가능하면 실행은 아래처럼 **가상환경 파이썬 경로를 고정**해서 사용하세요.
- `.\venv32\Scripts\python.exe kiwoom_kospi_strategy.py ...`

---

## 2) `kiwoom_kospi_strategy.py` 전략 개요

전략 이름: **전일 수급 + 09:02 모멘텀 + 10:00 청산**

1. (필터) “전일 외국인 순매수”가 **0보다 큰 날만** 진입
2. (진입 시그널) KOSPI 지수의 `09:00` 대비 `09:02` 수익률로 방향 결정
   - 상승(+) → `069500` (KODEX 200) 매수
   - 하락/0 → `114800` (KODEX 인버스) 매수
3. (청산) `10:00`에 전량 매도

주의:
- 현재 코드는 흐름을 단순화하기 위해 “매수 수량 = 매도 수량”으로 청산합니다.
- 실제 운용에서는 잔고조회로 종목별 **실보유수량**을 다시 확인해서 청산하는 방식이 안전합니다(부분체결/미체결 대응).

---

## 3) 사용 API (요약)

`kiwoom_kospi_strategy.py`는 아래를 호출합니다(환경/문서 버전에 따라 응답 키는 달라질 수 있음).

- `POST /oauth2/token` : 토큰 발급
- `ka10051` (`/api/dostk/sect`) : 전일 외국인 순매수(금액)
- `ka20005` (`/api/dostk/chart`) : KOSPI 지수 1분봉
- `kt00004` (`/api/dostk/acnt`) : 예수금(주문가능금액)
- `ka10001` (`/api/dostk/mrkcond`) : 현재가 조회(주문 수량 계산용)
- `kt10000` / `kt10001` (`/api/dostk/ordr`) : 시장가 매수/매도

---

## 4) 실행 방법

### (A) 모의투자(mockapi)

```powershell
.\venv32\Scripts\python.exe kiwoom_kospi_strategy.py `
  --app-key "APP_KEY" `
  --secret-key "SECRET_KEY" `
  --account "8116773911" `
  --base-url "https://mockapi.kiwoom.com"
```

### (B) 실서버(운영)

```powershell
.\venv32\Scripts\python.exe kiwoom_kospi_strategy.py `
  --app-key "APP_KEY" `
  --secret-key "SECRET_KEY" `
  --account "8116773911" `
  --base-url "https://api.kiwoom.com"
```

### (C) 테스트 모드(시간 대기 없이 로직만 실행)

```powershell
.\venv32\Scripts\python.exe kiwoom_kospi_strategy.py `
  --app-key "APP_KEY" `
  --secret-key "SECRET_KEY" `
  --account "8116773911" `
  --test
```

---

## 5) 로그(출력) 특징

이 스크립트는 “실행이 진행 중인지”를 확실히 알 수 있게 로그를 촘촘히 찍습니다.

- 모든 로그를 표준출력(`stdout`)으로 출력(터미널에서 항상 보이게)
- API 호출마다:
  - 요청 시작/응답 수신(HTTP 코드, 소요시간 ms)
  - 응답 키 목록 요약
  - 429(레이트리밋) 재시도 로그
- 대기(`wait_until`) 중에도 일정 주기로 “대기 중…” 출력

보안 주의:
- 코드에서는 토큰을 로그에 출력하지 않습니다.
- 하지만 커맨드라인에 `--app-key/--secret-key`를 직접 넣으면 PowerShell 히스토리에 남을 수 있습니다.

---

## 6) 흔한 문제/체크 포인트

- 전일 수급이 0 또는 음수로 나와서 종료
  - 전략의 필터가 정상 동작한 것입니다.
  - 흐름만 확인하고 싶으면 `--test`로 실행하세요.
- 분봉에서 09:00/09:02가 안 잡힘
  - 응답의 시간 키 포맷이 달라 파싱이 실패했을 수 있습니다.
  - 스크립트 로그의 “HHMM 샘플”로 어떤 시간이 들어오는지 먼저 확인하세요.
- 주문 API 실패
  - 실서버는 계좌번호/추가 인증/필수 필드가 더 필요할 수 있습니다. 문서 기준으로 주문 body를 보강해야 합니다.

