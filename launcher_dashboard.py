import sys
import os
from streamlit.web import cli as stcli

def resolve_path(path):
    if getattr(sys, 'frozen', False):
        # Если запущено из exe, ищем внутри временной папки
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, path)

if __name__ == "__main__":
    # Указываем Streamlit, какой файл запускать (он будет внутри exe)
    sys.argv = [
        "streamlit",
        "run",
        resolve_path("dashboard.py"),
        "--global.developmentMode=false",
        "--server.headless=true",
    ]
    sys.exit(stcli.main())