"""
main.py
진입점 — GUI 실행
"""

from gui import App


def main():
    app = App()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()


if __name__ == "__main__":
    main()
