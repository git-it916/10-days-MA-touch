"""
Kiwoom OpenAPI(QAxWidget) wrapper for login, TR 조회, 주문, 실시간 체결.

주요 구현
- 1편/2편: 로그인 이벤트 루프, 계좌/종목 조회
- 3편/4편/5편: OPT10081 일봉 조회 + 연속조회(비동기 0.25s 간격)
- 6편: OPW00018 예수금/보유종목 조회
- 7편: SendOrder 래핑, OnReceiveMsg, OnReceiveChejanData 처리
"""

from __future__ import annotations

import os
import sys
from typing import Dict, List, Optional, Tuple

import pandas as pd
from PyQt5 import QtCore, QtWidgets
from PyQt5.QAxContainer import QAxWidget
from PyQt5.QtCore import QLibraryInfo


def _to_int(val: str) -> int:
    try:
        return int(val.strip().replace(",", ""))
    except Exception:
        return 0


def _to_float(val: str) -> float:
    try:
        return float(val.strip().replace(",", ""))
    except Exception:
        return 0.0


class Kiwoom(QtWidgets.QMainWindow):
    """키움 OpenAPI 핵심 기능을 메서드로 묶은 클래스."""

    def __init__(self) -> None:
        super().__init__()
        # 1편/2편: QAxWidget 생성 및 시그널 슬롯 연결
        self.ocx = QAxWidget()
        self.ocx.setControl("KHOPENAPI.KHOpenAPICtrl.1")
        if self.ocx.isNull():
            raise RuntimeError(
                "KHOPENAPI.KHOpenAPICtrl.1 COM 객체를 로드하지 못했습니다. "
                "32비트 Kiwoom OpenAPI+ (KOA Studio)를 설치하거나 SysWoW64\\regsvr32로 재등록하세요."
            )
        self.ocx.OnEventConnect.connect(self._on_event_connect)
        self.ocx.OnReceiveTrData.connect(self._on_receive_tr_data)
        self.ocx.OnReceiveMsg.connect(self._on_receive_msg)
        self.ocx.OnReceiveChejanData.connect(self._on_receive_chejan_data)

        self.login_loop: Optional[QtCore.QEventLoop] = None
        self.tr_loop: Optional[QtCore.QEventLoop] = None
        self.acc_no: str = ""

        # 내부 상태 (TR별 결과 저장)
        self._opt10081_buffer: List[Dict[str, object]] = []
        self._opt10081_args: Tuple[str, str, str] = ("", "", "1")
        self._opw00018_result: Optional[Dict[str, object]] = None

    # 1편/2편: 로그인 및 계좌/종목 정보
    def login(self) -> None:
        self.login_loop = QtCore.QEventLoop()
        self.ocx.dynamicCall("CommConnect()")
        self.login_loop.exec_()
        self.acc_no = self.get_account_number()
        print(f"[로그인 완료] 계좌번호: {self.acc_no}")

    def _on_event_connect(self, err_code: int) -> None:
        msg = "성공" if err_code == 0 else f"실패(err={err_code})"
        print(f"[OnEventConnect] {msg}")
        if self.login_loop and self.login_loop.isRunning():
            self.login_loop.quit()

    def get_account_number(self) -> str:
        accs = self.ocx.dynamicCall('GetLoginInfo("ACCNO")')
        if not accs:
            return ""
        return accs.split(";")[0]

    def get_code_list_by_market(self, market: str = "0") -> List[str]:
        # market: "0" 코스피, "10" 코스닥
        codes = self.ocx.dynamicCall('GetCodeListByMarket(QString)', market)
        return [c for c in codes.split(";") if c]

    def get_master_code_name(self, code: str) -> str:
        return self.ocx.dynamicCall('GetMasterCodeName(QString)', code)

    # 3편/4편/5편: OPT10081 일봉 조회 + 연속조회
    def request_opt10081(self, code: str, base_date: str, adjusted: str = "1") -> pd.DataFrame:
        """
        일봉 데이터 조회. base_date: YYYYMMDD, adjusted: 1(수정주가).
        연속조회(prev_next=2) 시 0.25초 텀을 QTimer로 둠.
        """
        self._opt10081_buffer = []
        self._opt10081_args = (code, base_date, adjusted)
        self._send_opt10081(prev_next="0")

        self.tr_loop = QtCore.QEventLoop()
        self.tr_loop.exec_()

        df = pd.DataFrame(self._opt10081_buffer)
        # 최신일자가 위로 오도록 정렬
        df.sort_values("date", ascending=False, inplace=True)
        df.reset_index(drop=True, inplace=True)
        return df

    def _send_opt10081(self, prev_next: str) -> None:
        code, base_date, adjusted = self._opt10081_args
        self.ocx.dynamicCall('SetInputValue(QString, QString)', "종목코드", code)
        self.ocx.dynamicCall('SetInputValue(QString, QString)', "기준일자", base_date)
        self.ocx.dynamicCall('SetInputValue(QString, QString)', "수정주가구분", adjusted)
        self.ocx.dynamicCall(
            'CommRqData(QString, QString, int, QString)',
            "opt10081_req",
            "opt10081",
            int(prev_next),
            "0101",
        )

    def _handle_opt10081(self, rqname: str, trcode: str, prev_next: str) -> None:
        cnt = self.ocx.dynamicCall('GetRepeatCnt(QString, QString)', trcode, rqname)
        for i in range(cnt):
            date = self.ocx.dynamicCall('GetCommData(QString, QString, int, QString)', trcode, rqname, i, "일자").strip()
            open_ = _to_int(self.ocx.dynamicCall('GetCommData(QString, QString, int, QString)', trcode, rqname, i, "시가"))
            high = _to_int(self.ocx.dynamicCall('GetCommData(QString, QString, int, QString)', trcode, rqname, i, "고가"))
            low = _to_int(self.ocx.dynamicCall('GetCommData(QString, QString, int, QString)', trcode, rqname, i, "저가"))
            close = _to_int(self.ocx.dynamicCall('GetCommData(QString, QString, int, QString)', trcode, rqname, i, "현재가"))
            volume = _to_int(self.ocx.dynamicCall('GetCommData(QString, QString, int, QString)', trcode, rqname, i, "거래량"))
            self._opt10081_buffer.append(
                {
                    "date": date,
                    "open": open_,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": volume,
                }
            )

        if prev_next == "2":
            # 0.25초 텀을 두고 다음 페이지 요청 (QTimer 사용)
            QtCore.QTimer.singleShot(250, lambda: self._send_opt10081(prev_next="2"))
        else:
            if self.tr_loop and self.tr_loop.isRunning():
                self.tr_loop.quit()

    # 6편: OPW00018 예수금/보유종목 조회
    def request_opw00018(self, passwd: str = "", product_type: str = "2") -> Dict[str, object]:
        """
        예수금 및 보유 종목 조회. passwd는 공백("") 처리 시 저장된 비밀번호 사용.
        product_type: 1(합산), 2(개별종목)
        """
        acc_no = self.acc_no or self.get_account_number()
        self._opw00018_result = None

        self.ocx.dynamicCall('SetInputValue(QString, QString)', "계좌번호", acc_no)
        self.ocx.dynamicCall('SetInputValue(QString, QString)', "비밀번호", passwd)
        self.ocx.dynamicCall('SetInputValue(QString, QString)', "비밀번호입력매체구분", "00")
        self.ocx.dynamicCall('SetInputValue(QString, QString)', "조회구분", product_type)
        self.ocx.dynamicCall(
            'CommRqData(QString, QString, int, QString)',
            "opw00018_req",
            "opw00018",
            0,
            "0102",
        )

        self.tr_loop = QtCore.QEventLoop()
        self.tr_loop.exec_()
        return self._opw00018_result or {}

    def _handle_opw00018(self, rqname: str, trcode: str) -> None:
        total_purchase = _to_int(self.ocx.dynamicCall('GetCommData(QString, QString, int, QString)', trcode, rqname, 0, "총매입금액"))
        total_profit_rate = _to_float(self.ocx.dynamicCall('GetCommData(QString, QString, int, QString)', trcode, rqname, 0, "총수익률(%)"))
        est_deposit = _to_int(self.ocx.dynamicCall('GetCommData(QString, QString, int, QString)', trcode, rqname, 0, "추정예탁자산"))

        holdings: List[Dict[str, object]] = []
        cnt = self.ocx.dynamicCall('GetRepeatCnt(QString, QString)', trcode, rqname)
        for i in range(cnt):
            name = self.ocx.dynamicCall('GetCommData(QString, QString, int, QString)', trcode, rqname, i, "종목명").strip()
            qty = _to_int(self.ocx.dynamicCall('GetCommData(QString, QString, int, QString)', trcode, rqname, i, "보유수량"))
            buy_price = _to_int(self.ocx.dynamicCall('GetCommData(QString, QString, int, QString)', trcode, rqname, i, "매입가"))
            profit_rate = _to_float(self.ocx.dynamicCall('GetCommData(QString, QString, int, QString)', trcode, rqname, i, "수익률(%)"))
            cur_price = _to_int(self.ocx.dynamicCall('GetCommData(QString, QString, int, QString)', trcode, rqname, i, "현재가"))
            holdings.append(
                {
                    "name": name,
                    "qty": qty,
                    "buy_price": buy_price,
                    "profit_rate": profit_rate,
                    "cur_price": cur_price,
                }
            )

        self._opw00018_result = {
            "total_purchase": total_purchase,
            "total_profit_rate": total_profit_rate,
            "estimated_deposit": est_deposit,
            "holdings": holdings,
        }
        print(f"[OPW00018] 예수금: {est_deposit:,}, 총매입: {total_purchase:,}, 총수익률: {total_profit_rate:.2f}%")
        for h in holdings:
            print(f"  - {h['name']} | 수량 {h['qty']:,} | 매입가 {h['buy_price']:,} | 현재가 {h['cur_price']:,} | 수익률 {h['profit_rate']:.2f}%")
        if self.tr_loop and self.tr_loop.isRunning():
            self.tr_loop.quit()

    # 7편: 주문, 메시지, 체결
    def send_order(
        self,
        rqname: str,
        screen_no: str,
        acc_no: str,
        order_type: int,
        code: str,
        qty: int,
        price: int,
        hoga: str,
        org_order_no: str = "",
    ) -> int:
        """
        order_type: 1(매수), 2(매도) 등.
        hoga: "00" 지정가, "03" 시장가. 시장가일 때 price=0.
        """
        ret = self.ocx.dynamicCall(
            'SendOrder(QString, QString, QString, int, QString, int, int, QString, QString)',
            rqname,
            screen_no,
            acc_no,
            order_type,
            code,
            qty,
            price,
            hoga,
            org_order_no,
        )
        print(f"[SendOrder] ret={ret} (0이면 성공)")
        return ret

    def _on_receive_msg(self, screen_no: str, rqname: str, trcode: str, msg: str) -> None:
        print(f"[OnReceiveMsg] screen={screen_no}, rqname={rqname}, trcode={trcode}, msg={msg}")

    def _on_receive_chejan_data(self, gubun: str, item_cnt: int, fid_list: str) -> None:
        # gubun '0' = 주문 체결 통보
        if gubun != "0":
            return
        order_no = self.ocx.dynamicCall('GetChejanData(int)', 9203).strip()
        name = self.ocx.dynamicCall('GetChejanData(int)', 302).strip()
        order_state = self.ocx.dynamicCall('GetChejanData(int)', 913).strip()  # 접수/체결 등
        exec_price = _to_int(self.ocx.dynamicCall('GetChejanData(int)', 910))
        exec_qty = _to_int(self.ocx.dynamicCall('GetChejanData(int)', 911))
        remain_qty = _to_int(self.ocx.dynamicCall('GetChejanData(int)', 902))
        print(
            f"[Chejan] 주문번호 {order_no}, 종목 {name}, 상태 {order_state}, "
            f"체결가 {exec_price:,}, 체결량 {exec_qty}, 미체결 {remain_qty}"
        )

    # OnReceiveTrData 라우팅
    def _on_receive_tr_data(
        self,
        screen_no: str,
        rqname: str,
        trcode: str,
        record_name: str,
        prev_next: str,
        data_len: str,
        err_code: str,
        msg1: str,
        msg2: str,
    ) -> None:
        if rqname == "opt10081_req":
            self._handle_opt10081(rqname, trcode, prev_next)
        elif rqname == "opw00018_req":
            self._handle_opw00018(rqname, trcode)


def main() -> None:
    # 일부 환경(특히 OneDrive/비ASCII 경로)에서 Qt 플랫폼 플러그인 경로 인식 실패를 방지
    plugins_path = QLibraryInfo.location(QLibraryInfo.PluginsPath)
    if plugins_path and not os.environ.get("QT_QPA_PLATFORM_PLUGIN_PATH"):
        os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = plugins_path

    app = QtWidgets.QApplication(sys.argv)
    kiwoom = Kiwoom()
    kiwoom.login()

    # 데모: 계좌/종목, 일봉 조회, 예수금 조회
    print("[계좌] ", kiwoom.acc_no)
    kospi_codes = kiwoom.get_code_list_by_market("0")
    print(f"[코스피 종목수] {len(kospi_codes)}")

    # 필요한 경우 아래 주석 해제 후 테스트
    # df = kiwoom.request_opt10081(code="005930", base_date="20241230", adjusted="1")
    # print(df.head())
    # kiwoom.request_opw00018()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
