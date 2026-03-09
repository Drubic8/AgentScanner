import tkinter as tk
from tkinter import scrolledtext
import threading
import sys
import time
import os
import subprocess
import toml

# --- ИМПОРТЫ АГЕНТА ---
try:
    from agent import run_agent_cycle 
except ImportError:
    run_agent_cycle = None

# --- ИМПОРТ CLI STREAMLIT (ДЛЯ ЗАПУСКА ВНУТРИ EXE) ---
try:
    from streamlit.web import cli as st_cli
except ImportError:
    st_cli = None

# --- КЛАСС ПЕРЕНАПРАВЛЕНИЯ ВЫВОДА ---
class TextRedirector(object):
    def __init__(self, widget, tag="stdout"):
        self.widget = widget
        self.tag = tag

    def write(self, str):
        try:
            self.widget.configure(state="normal")
            self.widget.insert("end", str, (self.tag,))
            self.widget.see("end")
            self.widget.configure(state="disabled")
        except: pass
    
    def flush(self):
        pass

# --- ПОИСК ПУТЕЙ ---
def get_resource_path(filename):
    if getattr(sys, 'frozen', False):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, filename)

# --- ЗАПУСК СЕРВЕРА ---
def start_streamlit_process():
    """
    Запускает этот же EXE файл, но с аргументом 'run-server'.
    Это предотвращает бесконечный цикл.
    """
    dashboard_path = get_resource_path("dashboard.py")
    
    # Команда: MinerAgent.exe run-server
    cmd = [sys.executable, "run-server"]
    
    # Если запущен как скрипт python, а не exe
    if not getattr(sys, 'frozen', False):
         cmd = [sys.executable, "-m", "streamlit", "run", dashboard_path, "--server.port=8501", "--server.headless=true", "--global.developmentMode=false"]

    # Скрываем консольное окно для дочернего процесса
    if sys.platform == "win32":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    else:
        startupinfo = None

    try:
        process = subprocess.Popen(
            cmd, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE,
            text=True,
            startupinfo=startupinfo
        )
        print("☁️  Веб-интерфейс запускается (фоновый процесс)...")
        return process
    except Exception as e:
        print(f"❌ Ошибка запуска сервера: {e}")
        return None

# --- ОСНОВНОЙ GUI ---
def run_gui():
    root = tk.Tk()
    root.title("MinerHotel Agent Monitor v3.1")
    root.geometry("800x600")
    root.configure(bg="#2b2b2b")

    lbl_title = tk.Label(root, text="🤖 MinerHotel Agent & Monitor", font=("Segoe UI", 14, "bold"), bg="#2b2b2b", fg="#00ff00")
    lbl_title.pack(pady=(10, 5))

    lbl_status = tk.Label(root, text="Статус: Инициализация...", font=("Segoe UI", 10), bg="#2b2b2b", fg="#cccccc")
    lbl_status.pack(pady=(0, 10))

    logs = scrolledtext.ScrolledText(root, state='disabled', height=25, bg="#1e1e1e", fg="white", font=("Consolas", 9))
    logs.pack(fill="both", expand=True, padx=10, pady=5)
    logs.tag_config("stderr", foreground="#ff5555") 

    sys.stdout = TextRedirector(logs, "stdout")
    sys.stderr = TextRedirector(logs, "stderr")

    def orchestrator_thread():
        # 1. Читаем ID фермы
        farm_id = "UNKNOWN"
        try:
            if os.path.exists("secrets.toml"):
                data = toml.load("secrets.toml")
                farm_id = data.get("cloud", {}).get("token", "UNKNOWN")
        except: pass

        print(f"🤖 AGENT v3.1. Farm: {farm_id}")
        print("-" * 50)

        # 2. Запускаем Streamlit
        st_process = start_streamlit_process()
        
        time.sleep(3)
        if st_process and st_process.poll() is None:
             lbl_status.config(text="Статус: Веб-сервер активен | Агент работает", fg="#00ff00")
             print("✅ Сервер доступен по адресу: http://85.209.135.49:8000")
        else:
             lbl_status.config(text="Статус: Ошибка веб-сервера", fg="#ff5555")

        # 3. Запускаем Агента
        if run_agent_cycle:
            print("🔄 Старт цикла агента...")
            try:
                run_agent_cycle() 
            except Exception as e:
                print(f"🔥 Критическая ошибка агента: {e}")
        
        if st_process:
            st_process.terminate()

    t = threading.Thread(target=orchestrator_thread, daemon=True)
    t.start()

    root.mainloop()

# --- ТОЧКА ВХОДА (ГЛАВНАЯ ЛОГИКА) ---
if __name__ == "__main__":
    # Если аргумент 'run-server' — мы работаем как сервер Streamlit
    if len(sys.argv) > 1 and sys.argv[1] == "run-server":
        if st_cli:
            dashboard_path = get_resource_path("dashboard.py")
            # Подменяем аргументы, будто мы запустили streamlit из консоли
            sys.argv = [
                "streamlit",
                "run",
                dashboard_path,
                "--server.port=8501",
                "--server.headless=true",
                "--global.developmentMode=false",
            ]
            sys.exit(st_cli.main())
        else:
            print("CRITICAL: Streamlit CLI not found inside EXE")
    
    # Иначе — мы запускаем обычный GUI
    else:
        run_gui()