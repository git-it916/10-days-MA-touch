# Kiwoom OpenAPI 32-bit 환경 세팅 & 로그인/기초 조회 데모
키움증권 OpenAPI는 32비트에서만 동작합니다. 이 저장소는 32비트 conda 환경을 만들고, PyQt5로 로그인 창을 띄운 뒤 계좌번호/종목 코드를 조회하는 예제를 제공합니다.

## 준비물
- Windows (키움 OpenAPI는 Windows 전용)
- Anaconda 설치 (이메일 등록 필요)
- PyCharm Community 설치
- 키움증권 OpenAPI+가 PC에 설치되어 있어야 합니다 (키움 HTS에서 제공)

## 설치/환경 구성
1) **프로젝트 생성**: PyCharm에서 `trading_system` 새 프로젝트 생성 (기본 conda 인터프리터).  
2) **32비트 가상환경 생성 (필수)**  
   ```bat
   set CONDA_FORCE_32BIT=1
   conda create -n py310_32 python=3.10
   ```  
   새 프롬프트를 열면 위 `set`을 다시 실행해야 32비트가 유지됩니다.
3) **PyCharm에 32비트 인터프리터 연결**  
   `File > Settings > Project: trading_system > Python Interpreter` → `Add Interpreter > Conda Environment > Use existing` → `py310_32`의 python.exe 선택.  
4) **필수 라이브러리 설치**  
   ```bash
   pip install -r requirements.txt
   ```
   포함 패키지: `PyQt5`, `pywin32`, `pandas`(추가 분석용).

## 데모 실행 (login.py)
```bash
python login.py
```
- 창 크기: 800x600  
- 버튼:
  - `로그인`: `CommConnect()` 호출 → QEventLoop로 로그인 완료 신호 대기 → OnEventConnect에서 성공/실패 출력.
  - `접속 확인`: `GetConnectState()` 값(0/1) 출력.
  - `계좌 조회`: `GetLoginInfo("ACCNO")`로 계좌번호 리스트 조회, 세미콜론 구분을 리스트로 변환해 출력.
  - `코스피 종목 조회`: `GetCodeListByMarket("0")`로 코스피 종목 코드 목록 조회 후 일부 코드의 종목명을 `GetMasterCodeName()`으로 출력.
  - `일봉 데이터 요청`: TR `opt10081`로 삼성전자(005930) 일봉 요청. `SetInputValue` → `CommRqData` → `OnReceiveTrData` → `GetCommData`로 일자/시가/고가/저가/종가를 DataFrame에 누적. `prev_next`가 2이면 `QTimer`로 0.25초 간격 재요청해 상장 이후 전체 데이터를 연속 조회.
  - `잔고/보유종목 조회`: TR `opw00018`로 계좌 평가 잔고와 보유 종목을 조회. 싱글 데이터(총매입금액, 총수익률, 추정예탁자산)와 멀티 데이터(보유 종목 코드/수량/매입가/현재가/평가손익/수익률)를 함께 파싱.
  - `시장가/지정가 매수·매도`: `SendOrder` 예제를 버튼으로 제공. 주문 결과는 `OnReceiveMsg`(주문 메시지)와 `OnReceiveChejanData`(주문접수/체결) 이벤트에서 확인.
- 최초 실행 시 버전 패치 팝업이 뜨면 프로그램이 종료될 수 있습니다. 패치 완료 후 다시 실행하세요.

> 참고: `login.py`의 `req_daily_data`에서 종목코드/기준일자를 원하는 값으로 수정하면 됩니다. 기준일자는 `YYYYMMDD` 문자열입니다.

## 코드 개요 (login.py)
- **비동기 로그인**: `CommConnect()` → `QEventLoop` 대기 → `OnEventConnect`에서 err_code 확인 후 이벤트 루프 종료.
- **동기 조회**: `GetLoginInfo`, `GetCodeListByMarket`, `GetMasterCodeName`는 즉시 결과를 반환하므로 별도 이벤트 루프 없이 호출/출력.

## 문제 해결 팁
- 64비트 파이썬으로 실행하면 ActiveX를 찾지 못합니다. 반드시 32비트 conda 환경(`py310_32`)을 사용하세요.
- 키움 OpenAPI+가 설치되지 않았다면 KHOpenAPI 컨트롤 생성이 실패합니다. HTS 설치 상태를 확인하세요.
- PyCharm 인터프리터 변경 후 인덱싱이 끝날 때까지 기다렸다가 실행하세요.
- 계좌 TR(opw00018)을 사용할 때는 키움 HTS의 “계좌 비밀번호 저장” 기능을 활용하면 코드에서 비밀번호를 비워도 조회가 됩니다(모의투자는 0000).

## 다음 단계
- 계좌잔고/주문(TR)처럼 이벤트 루프가 필요한 통신을 추가하거나, 시세 수집/주문 로직을 확장하면서 자동매매를 완성할 수 있습니다.
