"""
main.py
진입점 — 로깅 초기화 후 GUI 실행
"""

from gui import setup_logging, App


def main():
    setup_logging()
    app = App()
    # X 버튼 클릭 시 감시 스레드를 정리하고 종료하도록 연결
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()


if __name__ == "__main__":
    main()
