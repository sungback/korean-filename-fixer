"""
main.py
진입점 — GUI 실행
"""

from gui import App


def main():
    # App 클래스(tkinter 창)를 만든다
    app = App()

    # 창의 X 버튼(닫기)을 누르면 app.on_close()가 실행되도록 연결
    # 이걸 안 하면 X 버튼을 눌러도 감시 스레드가 백그라운드에서 계속 살아있을 수 있다
    app.protocol("WM_DELETE_WINDOW", app.on_close)

    # tkinter 이벤트 루프 시작 — 창이 닫힐 때까지 여기서 프로그램이 멈춰서 대기한다
    app.mainloop()


if __name__ == "__main__":
    main()
