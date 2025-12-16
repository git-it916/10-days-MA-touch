"""PyQt5 Kiwoom OpenAPI demo: login + account/code lookup + TR 일봉 연속 조회 + 계좌/보유종목 + 주문 (lesson 2~7).

핵심 포인트:
- QMainWindow 기반 UI에 로그인/접속 확인/계좌 조회/종목 조회 버튼 배치
- KHOPENAPI ActiveX 컨트롤 생성 (32비트 파이썬 필수)
- CommConnect() 호출 후 QEventLoop로 비동기 로그인 완료 신호 대기
- OnEventConnect 이벤트에서 로그인 성공/실패 처리 후 이벤트 루프 해제
- GetLoginInfo/GetCodeListByMarket/GetMasterCodeName 동기 조회
추가: opt10081 TR로 일봉 데이터 요청/연속조회(입력값 설정 → CommRqData → OnReceiveTrData → GetCommData, prev_next 2 처리, QTimer로 간격 요청)
추가: opw00018 TR로 계좌 잔고/보유 종목 조회 (싱글+멀티 데이터 처리)
추가: SendOrder로 지정가/시장가 매수·매도, OnReceiveChejanData/OnReceiveMsg 이벤트 처리
"""

import sys
import datetime as dt
from typing import Optional

import pandas as pd
from PyQt5.QtCore import QEventLoop, QTimer
from PyQt5.QtWidgets import QApplication, QMainWindow, QPushButton
from PyQt5.QAxContainer import QAxWidget


class LoginForm(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Login Form")
        self.resize(800, 600)

        # Kiwoom OpenAPI ActiveX 컨트롤 생성 (32비트 파이썬 필수)
        self.kiwoom = QAxWidget("KHOPENAPI.KHOpenAPICtrl.1")
        self.kiwoom.OnEventConnect.connect(self.event_connect)
        self.kiwoom.OnReceiveTrData.connect(self.on_receive_tr_data)
        self.kiwoom.OnReceiveChejanData.connect(self.on_receive_chejan_data)
        self.kiwoom.OnReceiveMsg.connect(self.on_receive_msg)
        self.account_num: Optional[str] = None

        # 로그인 버튼
        btn_login = QPushButton("로그인", self)
        btn_login.move(20, 20)
        btn_login.clicked.connect(self.login_slot)

        # 접속 상태 확인 버튼
        btn_state = QPushButton("접속 확인", self)
        btn_state.move(20, 70)
        btn_state.clicked.connect(self.check_state)

        # 계좌 조회 버튼
        btn_accounts = QPushButton("계좌 조회", self)
        btn_accounts.move(20, 120)
        btn_accounts.clicked.connect(self.get_account_info)

        # 종목 코드 조회 버튼 (코스피 예시)
        btn_codes = QPushButton("코스피 종목 조회", self)
        btn_codes.move(20, 170)
        btn_codes.clicked.connect(self.get_kospi_codes)

        # 일봉 TR 요청 버튼 (opt10081)
        btn_daily = QPushButton("일봉 데이터 요청", self)
        btn_daily.move(20, 220)
        btn_daily.clicked.connect(self.req_daily_data)

        # 계좌/보유종목 조회 (opw00018)
        btn_balance = QPushButton("잔고/보유종목 조회", self)
        btn_balance.move(20, 270)
        btn_balance.clicked.connect(self.req_account_balance)

        # 주문 테스트 버튼들 (시장가/지정가 매수/매도)
        btn_mkt_buy = QPushButton("시장가 매수", self)
        btn_mkt_buy.move(200, 20)
        btn_mkt_buy.clicked.connect(self.send_order_market_buy)

        btn_mkt_sell = QPushButton("시장가 매도", self)
        btn_mkt_sell.move(200, 70)
        btn_mkt_sell.clicked.connect(self.send_order_market_sell)

        btn_lmt_buy = QPushButton("지정가 매수", self)
        btn_lmt_buy.move(200, 120)
        btn_lmt_buy.clicked.connect(self.send_order_limit_buy)

        btn_lmt_sell = QPushButton("지정가 매도", self)
        btn_lmt_sell.move(200, 170)
        btn_lmt_sell.clicked.connect(self.send_order_limit_sell)

        # 로그인 대기 이벤트 루프 (로그인 시도 시 생성)
        self.login_loop: Optional[QEventLoop] = None
        # 연속조회 여부 플래그
        self.is_remain_data: bool = False
        # 일봉 누적 데이터
        self.daily_data_df = pd.DataFrame(columns=["date", "open", "high", "low", "close"])
        # TR 재요청 타이머 (조회 제한 보호)
        self.timer = QTimer(self)
        self.timer.setInterval(250)  # 0.25초 간격
        self.timer.timeout.connect(self.request_remain_data)

    def login_slot(self) -> None:
        """로그인 요청 후 이벤트 루프로 완료 신호 대기."""
        self.kiwoom.dynamicCall("CommConnect()")
        self.login_loop = QEventLoop()
        self.login_loop.exec_()

    def event_connect(self, err_code: Optional[int]) -> None:
        """로그인 결과 콜백."""
        if err_code == 0:
            print("로그인 성공")
            account_list = self.kiwoom.dynamicCall("GetLoginInfo(QString)", "ACCNO")
            accounts = [acc for acc in account_list.split(";") if acc]
            if accounts:
                self.account_num = accounts[0]
                print(f"나의 계좌번호: {self.account_num}")
        else:
            print(f"로그인 실패 (err={err_code})")
        if self.login_loop is not None:
            self.login_loop.exit()

    def check_state(self) -> None:
        """접속 상태 확인."""
        state = self.kiwoom.dynamicCall("GetConnectState()")
        if state == 0:
            print("미연결")
        else:
            print("연결됨")

    # === 동기식 조회 함수들 (3편 내용) ===
    def get_account_info(self) -> None:
        """로그인 정보에서 계좌번호 목록을 조회."""
        account_list = self.kiwoom.dynamicCall("GetLoginInfo(QString)", "ACCNO")
        accounts = [acc for acc in account_list.split(";") if acc]
        if not accounts:
            print("계좌번호를 찾지 못했습니다. 로그인 여부와 인증서를 확인하세요.")
            return
        print("계좌번호 목록:")
        for acc in accounts:
            print(" -", acc)

    def get_kospi_codes(self) -> None:
        """코스피 종목 코드 리스트 조회 후 일부 종목명 출력."""
        codes_raw = self.kiwoom.dynamicCall("GetCodeListByMarket(QString)", "0")  # 0: 코스피
        codes = [c for c in codes_raw.split(";") if c]
        print(f"코스피 종목 개수: {len(codes)}")
        preview = codes[:5]
        for code in preview:
            name = self.get_code_name(code)
            print(f"종목코드: {code} | 종목명: {name}")

    def get_code_name(self, code: str) -> str:
        """종목 코드 → 종목명 변환."""
        return self.kiwoom.dynamicCall("GetMasterCodeName(QString)", code)

    # === TR 요청: 일봉 데이터 (opt10081) ===
    def req_daily_data(self) -> None:
        """opt10081 TR로 일봉 데이터 요청 (삼성전자 예시). 연속 조회를 prev_next 2로 이어감."""
        code = "005930"  # 삼성전자 예시
        base_date = dt.datetime.now().strftime("%Y%m%d")  # 오늘 날짜

        # 첫 요청 시 DataFrame을 초기화
        if not self.is_remain_data:
            self.daily_data_df = self.daily_data_df.iloc[0:0]

        # 1) 입력값 설정
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "종목코드", code)
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "기준일자", base_date)
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "수정주가구분", "1")

        # 2) TR 요청 전송: 사용자구분명, TR코드, 연속조회여부(0=단건), 화면번호
        prev_next = 2 if self.is_remain_data else 0
        self.kiwoom.dynamicCall(
            "CommRqData(QString, QString, int, QString)",
            "opt10081_req",
            "opt10081",
            prev_next,
            "0101",
        )
        # 응답을 기다리며 타이머를 켜서 남은 데이터가 있으면 이어서 요청
        if not self.timer.isActive():
            self.timer.start()

    def request_remain_data(self) -> None:
        """남은 데이터가 있을 때만 재요청; 없으면 타이머 정지."""
        if self.is_remain_data:
            self.req_daily_data()
        else:
            self.timer.stop()

    # === TR 요청: 계좌/보유종목 (opw00018) ===
    def req_account_balance(self) -> None:
        """opw00018 TR로 계좌 평가잔고/보유 종목 조회."""
        if not self.account_num:
            print("계좌번호를 찾지 못했습니다. 로그인 후 다시 시도하세요.")
            return

        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "계좌번호", self.account_num)
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "비밀번호", "")
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "비밀번호입력매체구분", "00")
        self.kiwoom.dynamicCall("SetInputValue(QString, QString)", "조회구분", "2")

        self.kiwoom.dynamicCall(
            "CommRqData(QString, QString, int, QString)",
            "opw00018_req",
            "opw00018",
            0,
            "2000",
        )

    def on_receive_tr_data(
        self,
        screen_no,
        rqname,
        trcode,
        recordname,
        prev_next,
        data_len,
        err_code,
        msg1,
        msg2,
    ):
        """TR 응답 수신 콜백."""
        if rqname == "opt10081_req":
            self._handle_opt10081(trcode, rqname, prev_next)
        elif rqname == "opw00018_req":
            self._handle_opw00018(trcode, rqname)

    # === 주문 이벤트: 체결/잔고 알림 ===
    def on_receive_chejan_data(self, gubun, item_cnt, fid_list):
        """주문접수/체결(0), 잔고통보(1) 이벤트."""
        if str(gubun) == "0":
            order_no = self.kiwoom.dynamicCall("GetChejanData(int)", 9203)
            code = self.kiwoom.dynamicCall("GetChejanData(int)", 9001).replace("A", "")
            order_type = self.kiwoom.dynamicCall("GetChejanData(int)", 905)
            order_status = self.kiwoom.dynamicCall("GetChejanData(int)", 913)
            chejan_qty = self.kiwoom.dynamicCall("GetChejanData(int)", 911)
            chejan_price = self.kiwoom.dynamicCall("GetChejanData(int)", 910)
            remain_qty = self.kiwoom.dynamicCall("GetChejanData(int)", 902)

            print(
                f"[체잔] 주문번호:{order_no} 종목:{code} 구분:{order_type} 상태:{order_status} "
                f"체결량:{chejan_qty} 체결가:{chejan_price} 미체결:{remain_qty}"
            )

    def on_receive_msg(self, screen_no, rqname, trcode, msg):
        """주문/시스템 메시지 이벤트."""
        print(f"[메시지] {msg}")

    # === TR 응답 핸들러: opt10081 (일봉 연속 조회) ===
    def _handle_opt10081(self, trcode, rqname, prev_next) -> None:
        if str(prev_next).strip() == "2":
            self.is_remain_data = True
            print("연속 조회: 데이터 있음 (Next=2)")
        else:
            self.is_remain_data = False
            print("연속 조회: 완료 (Next=0)")

        cnt = self.kiwoom.dynamicCall("GetRepeatCnt(QString, QString)", trcode, rqname)
        print(f"[opt10081] 수신 데이터 개수: {cnt}")

        start_idx = len(self.daily_data_df)
        for i in range(cnt):
            date = self._get_comm_data(trcode, rqname, i, "일자")
            open_price = self._get_comm_data_int(trcode, rqname, i, "시가")
            high_price = self._get_comm_data_int(trcode, rqname, i, "고가")
            low_price = self._get_comm_data_int(trcode, rqname, i, "저가")
            close_price = self._get_comm_data_int(trcode, rqname, i, "현재가")

            self.daily_data_df.loc[start_idx + i] = [
                date,
                open_price,
                high_price,
                low_price,
                close_price,
            ]

        print(f"[opt10081] 현재 누적 데이터 수: {len(self.daily_data_df)}")

    # === TR 응답 핸들러: opw00018 (계좌/보유 종목) ===
    def _handle_opw00018(self, trcode, rqname) -> None:
        total_buy_money = self._get_comm_data_int(trcode, rqname, 0, "총매입금액")
        total_profit_loss_rate = self._get_comm_data_float(trcode, rqname, 0, "총수익률(%)")
        estimated_asset = self._get_comm_data_int(trcode, rqname, 0, "추정예탁자산")

        print(f"[opw00018] 총 매입금액: {total_buy_money}")
        print(f"[opw00018] 총 수익률: {total_profit_loss_rate}%")
        print(f"[opw00018] 추정 예탁자산: {estimated_asset}")

        cnt = self.kiwoom.dynamicCall("GetRepeatCnt(QString, QString)", trcode, rqname)
        print(f"[opw00018] 보유 종목 수: {cnt}")

        for i in range(cnt):
            code = self._get_comm_data(trcode, rqname, i, "종목번호").replace("A", "")
            name = self._get_comm_data(trcode, rqname, i, "종목명")
            quantity = self._get_comm_data_int(trcode, rqname, i, "보유수량")
            buy_price = self._get_comm_data_int(trcode, rqname, i, "매입가")
            current_price = self._get_comm_data_int(trcode, rqname, i, "현재가")
            eval_profit = self._get_comm_data_int(trcode, rqname, i, "평가손익")
            yield_rate = self._get_comm_data_float(trcode, rqname, i, "수익률(%)")

            print(
                f"[opw00018] 종목명: {name} | 보유수량: {quantity} | 매입가: {buy_price} "
                f"| 현재가: {current_price} | 평가손익: {eval_profit} | 수익률: {yield_rate}%"
            )

    # === 주문 함수들 (SendOrder) ===
    def send_order_market_buy(self) -> None:
        """시장가 매수 예시."""
        if not self.account_num:
            print("계좌번호가 없습니다. 로그인 후 다시 시도하세요.")
            return
        self.kiwoom.dynamicCall(
            "SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)",
            ["시장가매수", "0101", self.account_num, 1, "005930", 10, 0, "03", ""],
        )

    def send_order_market_sell(self) -> None:
        """시장가 매도 예시."""
        if not self.account_num:
            print("계좌번호가 없습니다. 로그인 후 다시 시도하세요.")
            return
        self.kiwoom.dynamicCall(
            "SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)",
            ["시장가매도", "0101", self.account_num, 2, "005930", 10, 0, "03", ""],
        )

    def send_order_limit_buy(self) -> None:
        """지정가 매수 예시 (가격은 필요에 맞게 수정)."""
        if not self.account_num:
            print("계좌번호가 없습니다. 로그인 후 다시 시도하세요.")
            return
        self.kiwoom.dynamicCall(
            "SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)",
            ["지정가매수", "0101", self.account_num, 1, "005930", 10, 70000, "00", ""],
        )

    def send_order_limit_sell(self) -> None:
        """지정가 매도 예시 (가격은 필요에 맞게 수정)."""
        if not self.account_num:
            print("계좌번호가 없습니다. 로그인 후 다시 시도하세요.")
            return
        self.kiwoom.dynamicCall(
            "SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)",
            ["지정가매도", "0101", self.account_num, 2, "005930", 10, 75000, "00", ""],
        )

    # === 공통 파서 ===
    def _get_comm_data(self, trcode: str, rqname: str, idx: int, field: str) -> str:
        return (
            self.kiwoom.dynamicCall(
                "GetCommData(QString, QString, QString, int, QString)",
                trcode,
                "",
                rqname,
                idx,
                field,
            ).strip()
            or ""
        )

    def _get_comm_data_int(self, trcode: str, rqname: str, idx: int, field: str) -> int:
        raw = self._get_comm_data(trcode, rqname, idx, field).replace(",", "")
        try:
            return int(raw)
        except ValueError:
            return 0

    def _get_comm_data_float(self, trcode: str, rqname: str, idx: int, field: str) -> float:
        raw = self._get_comm_data(trcode, rqname, idx, field).replace(",", "")
        try:
            return float(raw)
        except ValueError:
            return 0.0


def main() -> None:
    app = QApplication(sys.argv)
    window = LoginForm()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
