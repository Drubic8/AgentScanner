import sys
import re
import os
import json
import time
import subprocess
import requests
import webbrowser
import pandas as pd
from datetime import datetime

# Константы автообновления
CURRENT_VERSION = "1.1.0"
UPDATE_INFO_URL = "https://raw.githubusercontent.com/Drubic8/AgentScanner/main/version.json"

# --- ФИКС ПУТЕЙ ---
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.append(current_dir)

# Добавляем путь к папке miner_scanner
scanner_path = os.path.join(current_dir, 'miner_scanner')
if os.path.exists(scanner_path) and scanner_path not in sys.path:
    sys.path.append(scanner_path)

# --- QT IMPORTS ---
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QTableWidget, QTableWidgetItem, 
                             QPushButton, QLabel, QLineEdit, QFileDialog, 
                             QProgressBar, QMessageBox, QHeaderView, QCheckBox,
                             QListWidget, QAbstractItemView, QInputDialog, QFrame,
                             QScrollArea, QSizePolicy, QMenu, QDialog, QRadioButton, 
                             QButtonGroup, QTextEdit, QListWidgetItem) 
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize
from PyQt6.QtGui import QFont, QColor, QIcon, QAction

# --- FPDF & PANDAS (Вместо ReportLab) ---
try:
    from fpdf import FPDF
    FPDF_AVAIL = True
except ImportError:
    FPDF_AVAIL = False

# --- ИМПОРТ СКАНЕРА ---
try:
    from miner_scanner.core import scan_network_range
    SCANNER_AVAIL = True
except ImportError:
    try:
        from core import scan_network_range
        SCANNER_AVAIL = True
    except ImportError:
        SCANNER_AVAIL = False
        print("Warning: Scanner core not found.")

# --- ИМПОРТ ACTIONS ---
try:
    from miner_scanner.handlers.miner_actions import WhatsminerManager
    ACTIONS_AVAIL = True
except ImportError:
    try:
        from handlers.miner_actions import WhatsminerManager
        ACTIONS_AVAIL = True
    except ImportError:
        ACTIONS_AVAIL = False

CONFIG_FILE = "ip_ranges.json"
APP_TITLE = "ASIC_Monitor"
VER = f"{CURRENT_VERSION}"  # Теперь версия в заголовке окна будет браться автоматически из CURRENT_VERSION

# ==========================================
# ДИАЛОГ КОМАНД (REMOTE CTRL)
# ==========================================
class CommandDialog(QDialog):
    def __init__(self, count, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Remote Control Panel")
        self.setFixedSize(350, 240)
        self.selected_action = None
        
        # Стилизация
        self.setStyleSheet("""
            QDialog { background-color: #F5F7FA; }
            QLabel { color: #333; }
            QRadioButton { font-size: 13px; padding: 5px; color: #333; }
        """)
        
        layout = QVBoxLayout(self)
        
        # Заголовок
        lbl_info = QLabel(f"Selected Devices: {count}")
        lbl_info.setStyleSheet("font-size: 16px; font-weight: bold; color: #0069D9; margin-bottom: 10px;")
        lbl_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(lbl_info)
        
        # Группа кнопок
        self.grp = QButtonGroup(self)
        
        self.rb_led_blink = QRadioButton("💡 LED: Flash (Locate)")
        self.rb_led_auto = QRadioButton("🌑 LED: Normal (Auto)")
        self.rb_reboot = QRadioButton("⚡ Reboot Device")
        
        # По умолчанию выбрана подсветка
        self.rb_led_blink.setChecked(True)
        
        layout.addWidget(self.rb_led_blink)
        layout.addWidget(self.rb_led_auto)
        layout.addWidget(self.rb_reboot)
        
        self.grp.addButton(self.rb_led_blink)
        self.grp.addButton(self.rb_led_auto)
        self.grp.addButton(self.rb_reboot)
        
        layout.addStretch()
        
        # Кнопки
        btn_layout = QHBoxLayout()
        
        btn_cancel = QPushButton("Cancel")
        btn_cancel.setStyleSheet("background-color: #DDD; border: 1px solid #CCC; border-radius: 4px; padding: 6px;")
        btn_cancel.clicked.connect(self.reject)
        
        btn_apply = QPushButton("EXECUTE")
        btn_apply.setStyleSheet("background-color: #0069D9; color: white; font-weight: bold; border-radius: 4px; padding: 6px;")
        btn_apply.clicked.connect(self.on_apply)
        
        btn_layout.addWidget(btn_cancel)
        btn_layout.addWidget(btn_apply)
        layout.addLayout(btn_layout)

    def on_apply(self):
        if self.rb_reboot.isChecked(): self.selected_action = "reboot"
        elif self.rb_led_blink.isChecked(): self.selected_action = "led_on"
        elif self.rb_led_auto.isChecked(): self.selected_action = "led_off"
        self.accept()

# ==========================================
# ДИАЛОГ РЕДАКТОРА ПОДСЕТЕЙ (IP RANGE EDITOR)
# ==========================================
class IPRangeDialog(QDialog):
    def __init__(self, name="", ranges=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("IP Range Editor")
        self.setFixedSize(400, 350)
        
        layout = QVBoxLayout(self)
        
        # Название сети
        lbl_name = QLabel("Название сети (Network Name):")
        lbl_name.setStyleSheet("font-weight: bold;")
        layout.addWidget(lbl_name)
        
        self.le_name = QLineEdit(name)
        self.le_name.setPlaceholderText("Например: Клиент А")
        layout.addWidget(self.le_name)
        
        layout.addSpacing(10)
        
        # Диапазоны (многострочное поле)
        lbl_ranges = QLabel("IP Диапазоны (каждый с новой строки):")
        lbl_ranges.setStyleSheet("font-weight: bold;")
        layout.addWidget(lbl_ranges)
        
        self.te_ranges = QTextEdit()
        self.te_ranges.setPlaceholderText("192.168.1.1-255\n10.10.33.2-255")
        
        if ranges:
            if isinstance(ranges, list):
                self.te_ranges.setPlainText("\n".join(ranges))
            else:
                self.te_ranges.setPlainText(ranges.replace(",", "\n"))
                
        layout.addWidget(self.te_ranges)
        
        layout.addSpacing(10)
        
        # Кнопки
        btn_layout = QHBoxLayout()
        btn_cancel = QPushButton("Отмена")
        btn_cancel.clicked.connect(self.reject)
        
        btn_save = QPushButton("Сохранить")
        btn_save.setStyleSheet("background-color: #0069D9; color: white; font-weight: bold;")
        btn_save.clicked.connect(self.accept)
        
        btn_layout.addStretch()
        btn_layout.addWidget(btn_cancel)
        btn_layout.addWidget(btn_save)
        layout.addLayout(btn_layout)
        
    def get_data(self):
        name = self.le_name.text().strip()
        # Разбиваем текст на строки и удаляем пустые
        raw_ranges = self.te_ranges.toPlainText().split('\n')
        ranges = [r.strip() for r in raw_ranges if r.strip()]
        return name, ranges

# ==========================================
# WORKER: СКАНЕР
# ==========================================
class ScanWorker(QThread):
    progress_signal = pyqtSignal(int, int)
    result_signal = pyqtSignal(list)
    finished_signal = pyqtSignal()
    log_signal = pyqtSignal(str)

    def __init__(self, ranges):
        super().__init__()
        self.ranges = ranges
        self.is_running = True

    def run(self):
        if not SCANNER_AVAIL:
            self.log_signal.emit("Error: Scanner core not found!")
            self.finished_signal.emit()
            return

        total = len(self.ranges)
        for i, r_str in enumerate(self.ranges):
            if not self.is_running: break
            self.log_signal.emit(f"Scanning: {r_str}...")
            try:
                res = scan_network_range(r_str)
                if res: 
                    cleaned_res = []
                    for item in res:
                        model = str(item.get('Model', ''))
                        if "Antminer" in model and "Elphapex" in model:
                            item['Model'] = model.replace("Elphapex ", "")
                        cleaned_res.append(item)
                    self.result_signal.emit(cleaned_res)
            except Exception as e:
                self.log_signal.emit(f"Error {r_str}: {e}")
            self.progress_signal.emit(i + 1, total)
        self.finished_signal.emit()

    def stop(self):
        self.is_running = False

# ==========================================
# WORKER: ACTIONS
# ==========================================
class ActionWorker(QThread):
    log_signal = pyqtSignal(str)
    
    def __init__(self, ip, action_type):
        super().__init__()
        self.ip = ip
        self.action = action_type 

    def run(self):
        if not ACTIONS_AVAIL:
            self.log_signal.emit(f"❌ {self.ip}: Library missing!")
            return

        try:
            wm = WhatsminerManager(self.ip)
            
            if self.action == "reboot":
                self.log_signal.emit(f"⚡ {self.ip}: Rebooting...")
                ok, msg = wm.reboot()
                act_name = "Reboot"
            
            elif self.action == "led_on":
                self.log_signal.emit(f"💡 {self.ip}: LED Blink...")
                ok, msg = wm.blink_led(True)
                act_name = "LED ON"
                
            elif self.action == "led_off":
                self.log_signal.emit(f"🌑 {self.ip}: LED Auto...")
                ok, msg = wm.blink_led(False)
                act_name = "LED OFF"
            else:
                ok, msg = False, "Unknown action"
                act_name = "?"

            icon = "✅" if ok else "❌"
            self.log_signal.emit(f"{icon} {self.ip}: {act_name} -> {msg}")
            
        except Exception as e:
            self.log_signal.emit(f"🔥 Critical Error {self.ip}: {str(e)}")

# ==========================================
# PDF REPORT CLASS (From Dashboard)
# ==========================================
class PDFReport(FPDF):
    def header(self):
        self.set_font('Arial', 'B', 14)
        self.cell(0, 10, 'MinerHotel Daily Report', 0, 1, 'L')
        self.line(10, 20, 287, 20)
        self.ln(5)

    def footer(self):
        self.set_y(-15)
        self.set_font('Arial', 'I', 8)
        self.cell(0, 10, f'Page {self.page_no()}', 0, 0, 'C')

# ==========================================
# ГЛАВНОЕ ОКНО
# ==========================================
class GeminiApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_TITLE} v{VER}")
        self.resize(1350, 850)
        self.scan_data = [] 
        self.ranges_config = self.load_config()
        self.dark_mode = True 
        
        self.init_ui()
        self.apply_theme()

    def init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # === SIDEBAR ===
        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(300)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(15, 20, 15, 20)
        side_layout.setSpacing(10)

        lbl_logo = QLabel("GEMINI TOOLS")
        lbl_logo.setObjectName("Logo")
        lbl_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        side_layout.addWidget(lbl_logo)

        self.btn_theme = QPushButton("☀ / ☾ Switch Theme")
        self.btn_theme.setObjectName("BtnTheme")
        self.btn_theme.clicked.connect(self.toggle_theme)
        side_layout.addWidget(self.btn_theme)
        
        side_layout.addSpacing(10)

        # === КНОПКА ОБНОВЛЕНИЯ ===
        self.btn_update = QPushButton("🔄 Проверить обновления")
        self.btn_update.setObjectName("BtnUpdate")
        self.btn_update.clicked.connect(lambda: self.check_for_updates(auto=False))
        side_layout.addWidget(self.btn_update)
        # ==========================

        # === КНОПКА ИСТОРИИ ИЗМЕНЕНИЙ ===
        self.btn_changelog = QPushButton("📜 История изменений")
        self.btn_changelog.setObjectName("BtnChangelog")
        self.btn_changelog.clicked.connect(self.show_changelog)
        side_layout.addWidget(self.btn_changelog)
        # ==========================

        header_layout = QHBoxLayout()
        lbl_ranges = QLabel("IP RANGES")
        lbl_ranges.setObjectName("SectionHeader")
        
        self.chk_all = QCheckBox("All")
        self.chk_all.setObjectName("ChkAll")
        self.chk_all.stateChanged.connect(self.toggle_all_ranges)
        
        header_layout.addWidget(lbl_ranges)
        header_layout.addStretch()
        header_layout.addWidget(self.chk_all)
        side_layout.addLayout(header_layout)

        self.list_ranges = QListWidget()
        self.list_ranges.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self.refresh_ranges_list()
        
        # === ДВОЙНОЙ КЛИК ДЛЯ РЕДАКТИРОВАНИЯ ===
        self.list_ranges.itemDoubleClicked.connect(self.edit_subnet)
        
        side_layout.addWidget(self.list_ranges)

        btn_layout = QHBoxLayout()
        btn_add = QPushButton("+ Add")
        btn_add.clicked.connect(self.add_range_dialog)
        
        # === КНОПКА ИЗМЕНИТЬ ===
        self.btn_edit_subnet = QPushButton("⚙️ Edit")
        self.btn_edit_subnet.clicked.connect(lambda: self.edit_subnet())
        
        btn_del = QPushButton("- Del")
        btn_del.clicked.connect(self.delete_range)
        
        btn_layout.addWidget(btn_add)
        btn_layout.addWidget(self.btn_edit_subnet) # Кнопка посередине
        btn_layout.addWidget(btn_del)
        side_layout.addLayout(btn_layout)

        side_layout.addSpacing(20)

        self.btn_scan = QPushButton("START SCAN")
        self.btn_scan.setObjectName("BtnScan")
        self.btn_scan.clicked.connect(self.start_scan)
        side_layout.addWidget(self.btn_scan)

        self.btn_stop = QPushButton("STOP")
        self.btn_stop.setObjectName("BtnStop")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop_scan)
        side_layout.addWidget(self.btn_stop)

        side_layout.addStretch()

        lbl_exp = QLabel("EXPORT DATA")
        lbl_exp.setObjectName("SectionHeader")
        side_layout.addWidget(lbl_exp)

        btn_csv = QPushButton("Excel / CSV")
        btn_csv.clicked.connect(self.export_csv)
        side_layout.addWidget(btn_csv)

        btn_pdf = QPushButton("PDF Report (Pro)")
        btn_pdf.clicked.connect(self.export_pdf_pro)
        side_layout.addWidget(btn_pdf)

        # === CONTENT AREA ===
        content = QWidget()
        content.setObjectName("ContentArea")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(20, 20, 20, 20)
        content_layout.setSpacing(15)

        # 1. Stats Scroll
        self.stats_scroll = QScrollArea()
        self.stats_scroll.setWidgetResizable(True)
        self.stats_scroll.setFixedHeight(110)
        self.stats_scroll.setFrameShape(QFrame.Shape.NoFrame)
        
        self.stats_widget = QWidget()
        self.stats_widget.setObjectName("StatsWidget")
        self.stats_layout = QHBoxLayout(self.stats_widget)
        self.stats_layout.setContentsMargins(0, 0, 0, 0)
        self.stats_layout.setSpacing(15)
        self.stats_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        
        self.stats_scroll.setWidget(self.stats_widget)
        content_layout.addWidget(self.stats_scroll)

        self.refresh_stats_cards([]) 

        # 2. CONTROL BAR (Новая панель управления)
        ctrl_layout = QHBoxLayout()
        ctrl_layout.setContentsMargins(0, 5, 0, 5)
        
        lbl_ctrl = QLabel("Device Actions:")
        lbl_ctrl.setStyleSheet("font-weight: bold; color: #888;")
        
        btn_remote = QPushButton("🛠 Remote Ctrl")
        btn_remote.setFixedWidth(140)
        btn_remote.setStyleSheet("background-color: #555; color: white; font-weight: bold; border-radius: 4px; padding: 6px;")
        btn_remote.clicked.connect(self.open_remote_panel)
        
        ctrl_layout.addWidget(lbl_ctrl)
        ctrl_layout.addWidget(btn_remote)
        ctrl_layout.addStretch()
        
        content_layout.addLayout(ctrl_layout)

        # 3. Table
        cols = ["IP", "Model", "Algo", "Real HR", "Avg HR", "Temp", "Fan", "Pool", "Worker", "Uptime"]
        self.table = QTableWidget()
        self.table.setColumnCount(len(cols))
        self.table.setHorizontalHeaderLabels(cols)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_context_menu)

        self.table.itemDoubleClicked.connect(self.open_web_interface)
        
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        h.setStretchLastSection(True)
        self.table.setColumnWidth(0, 130)
        self.table.setColumnWidth(1, 150)
        
        content_layout.addWidget(self.table)

        # 4. Footer
        footer = QHBoxLayout()
        self.status_bar = QLabel("Ready.")
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(8)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        
        footer.addWidget(self.status_bar)
        footer.addWidget(self.progress)
        content_layout.addLayout(footer)

        main_layout.addWidget(sidebar)
        main_layout.addWidget(content)

        # === ДОБАВЛЯЕМ АВТОЗАПУСК ПРОВЕРКИ ОБНОВЛЕНИЙ ===
        self.check_for_updates(auto=True)

    # ==========================================
    # ЛОГИКА АВТООБНОВЛЕНИЯ
    # ==========================================
    def check_for_updates(self, auto=False):
        """Проверяет наличие новой версии на GitHub"""
        try:
            response = requests.get(UPDATE_INFO_URL, timeout=5)
            if response.status_code == 200:
                data = response.json()
                latest_version = data.get("version")
                download_url = data.get("url")
                changelog = data.get("changelog", "Обновление системы.")
                
                # Если версия в интернете больше нашей
                if latest_version > CURRENT_VERSION:
                    reply = QMessageBox.question(
                        self, 
                        'Доступно обновление!', 
                        f'Найдена новая версия: {latest_version} (У вас {CURRENT_VERSION})\n\nЧто нового:\n{changelog}\n\nОбновить сейчас?',
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                    )
                    if reply == QMessageBox.StandardButton.Yes:
                        self.apply_update(download_url)
                else:
                    if not auto: # Если нажали кнопку вручную, скажем что всё ок
                        QMessageBox.information(self, "Обновление", "У вас установлена самая актуальная версия!")
        except Exception as e:
            if not auto:
                QMessageBox.warning(self, "Ошибка", f"Не удалось проверить обновления:\n{e}")

    def apply_update(self, download_url):
        """Скачивает новый EXE и выполняет подмену через BAT"""
        if not getattr(sys, 'frozen', False):
            QMessageBox.warning(self, "Внимание", "Автообновление работает только в скомпилированном .exe файле!")
            return
            
        current_exe = sys.executable
        exe_dir = os.path.dirname(current_exe)
        exe_name = os.path.basename(current_exe)
        new_exe = current_exe + ".new"
        
        # Прячем батник в TEMP от OneDrive
        temp_dir = os.environ.get("TEMP", exe_dir)
        bat_file = os.path.join(temp_dir, f"updater_{int(time.time())}.bat")
        
        try:
            msg = QMessageBox(self)
            msg.setWindowTitle("Обновление")
            msg.setText("Скачиваю обновление... Пожалуйста, подождите.")
            msg.setStandardButtons(QMessageBox.StandardButton.NoButton)
            msg.show()
            QApplication.processEvents()
            
            with requests.get(download_url, stream=True) as r:
                r.raise_for_status()
                with open(new_exe, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192): 
                        f.write(chunk)
            msg.close()
        except Exception as e:
            msg.close()
            QMessageBox.warning(self, "Ошибка скачивания", str(e))
            return

        # Простой батник без костылей очистки
        bat_content = f"""@echo off
chcp 65001 > NUL
title Установка обновления...
echo Пожалуйста, подождите пару секунд...
echo Идет обновление программы {exe_name}
cd /d "{exe_dir}"
:loop
timeout /t 1 /nobreak > NUL
del "{exe_name}" > NUL 2>&1
if exist "{exe_name}" goto loop
ren "{exe_name}.new" "{exe_name}"
start "" "{exe_name}"
del "%~f0"
"""
        with open(bat_file, 'w', encoding='utf-8') as f:
            f.write(bat_content)
            
        # === ТОТАЛЬНАЯ ЗАЧИСТКА В PYTHON ===
        clean_env = os.environ.copy()
        # Удаляем любые следы PyInstaller из памяти
        keys_to_remove = [k for k in clean_env.keys() if 'MEI' in k.upper() or 'PYI' in k.upper()]
        for k in keys_to_remove:
            clean_env.pop(k, None)
            
        # Запускаем батник в НОВОМ видимом окне (0x00000010 = CREATE_NEW_CONSOLE)
        subprocess.Popen(f'"{bat_file}"', shell=True, env=clean_env, creationflags=0x00000010)
        
        # Жесткое отключение программы, чтобы освободить файл .exe
        os._exit(0)

    def show_changelog(self):
        """Отображает окно со списком изменений"""
        changelog_text = f"""
        <h3>ASIC_Monitor v{CURRENT_VERSION}</h3>
        <b>Версия 1.1.0 (Текущая)</b>
        <ul>
            <li>Улучшен интерфейс программы</li>
        </ul>
        """
        
        msg = QMessageBox(self)
        msg.setWindowTitle("История изменений")
        # Включаем поддержку HTML, чтобы список был красивым
        msg.setTextFormat(Qt.TextFormat.RichText) 
        msg.setText(changelog_text)
        msg.exec()    

    # --- MENU & ACTIONS LOGIC ---
    def open_remote_panel(self):
        # Получаем выбранные строки
        rows = sorted(set(i.row() for i in self.table.selectedItems()))
        if not rows:
            QMessageBox.warning(self, "No Selection", "Please select devices from the table first.")
            return

        # Открываем диалог
        dlg = CommandDialog(len(rows), self)
        if dlg.exec(): # Если нажали EXECUTE
            action = dlg.selected_action
            if action:
                self.run_action(action, rows, confirm_needed=True)

    def show_context_menu(self, pos):
        menu = QMenu()
        
        act_reboot = QAction("⚡ Reboot Device", self)
        act_reboot.triggered.connect(lambda: self.run_action("reboot"))
        menu.addAction(act_reboot)
        
        menu.addSeparator()
        
        act_led_on = QAction("💡 LED: Blink", self)
        act_led_on.triggered.connect(lambda: self.run_action("led_on"))
        menu.addAction(act_led_on)

        act_led_off = QAction("🌑 LED: Auto", self)
        act_led_off.triggered.connect(lambda: self.run_action("led_off"))
        menu.addAction(act_led_off)
        
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def run_action(self, action_type, rows=None, confirm_needed=True):
        if not rows:
            rows = sorted(set(i.row() for i in self.table.selectedItems()))
        
        if not rows: return

        # Определяем красивое имя для подтверждения
        nice_name = {
            "reboot": "REBOOT",
            "led_on": "FLASH LED",
            "led_off": "NORMAL LED"
        }.get(action_type, action_type)

        # Финальное подтверждение
        if confirm_needed:
            confirm = QMessageBox.question(
                self, 
                "Confirm Action", 
                f"Are you sure you want to apply {nice_name} to {len(rows)} devices?", 
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if confirm != QMessageBox.StandardButton.Yes: return

        # Запуск задач
        for r in rows:
            ip = self.table.item(r, 0).text()
            worker = ActionWorker(ip, action_type)
            worker.log_signal.connect(self.status_bar.setText)
            worker.finished.connect(worker.deleteLater)
            worker.start()
            if not hasattr(self, 'workers'): self.workers = []
            self.workers.append(worker)

    # --- ОСТАЛЬНЫЕ ФУНКЦИИ (Config, Scan, Export) ---
    def load_config(self):
        if not os.path.exists(CONFIG_FILE): return []
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
                res = []
                if isinstance(d, list): 
                    for item in d:
                        # Автоматически обновляем старый формат на новый
                        if "range" in item and "ranges" not in item:
                            item["ranges"] = [item["range"]]
                        res.append(item)
                    return res
                if isinstance(d, dict): 
                    return [{"name": k, "ranges": [v]} for k,v in d.items()]
        except: return []
        return []

    def save_config(self):
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.ranges_config, f, ensure_ascii=False, indent=4)
        except Exception as e: QMessageBox.critical(self, "Error", str(e))

    def refresh_ranges_list(self):
        self.list_ranges.clear()
        for idx, r in enumerate(self.ranges_config):
            name = r.get('name', '?')
            ranges = r.get('ranges', [])
            
            # Красивое отображение в списке
            if len(ranges) > 1:
                display_text = f"{name} ({len(ranges)} диапазонов)"
            else:
                display_text = f"{name} ({ranges[0] if ranges else 'Пусто'})"
                
            item = QListWidgetItem(display_text)
            item.setData(Qt.ItemDataRole.UserRole, idx) # Надежно прячем индекс внутри элемента
            self.list_ranges.addItem(item)
    def toggle_all_ranges(self, state):
        if state == 2: self.list_ranges.selectAll()
        else: self.list_ranges.clearSelection()

    def add_range_dialog(self):
        dlg = IPRangeDialog(parent=self)
        if dlg.exec():
            name, ranges = dlg.get_data()
            if name and ranges:
                self.ranges_config.append({"name": name, "ranges": ranges})
                self.save_config()
                self.refresh_ranges_list()

    def delete_range(self):
        rows = self.list_ranges.selectedIndexes()
        for r in sorted(rows, reverse=True): del self.ranges_config[r.row()]
        self.save_config()
        self.refresh_ranges_list()

    
    # === ФУНКЦИЯ РЕДАКТИРОВАНИЯ ПОДСЕТИ ===
    def edit_subnet(self, item=None):
        """Открывает окно IPRangeDialog для редактирования"""
        if item is None:
            selected = self.list_ranges.selectedItems()
            if not selected:
                QMessageBox.warning(self, "Внимание", "Сначала выберите подсеть для редактирования!")
                return
            item = selected[0]
            
        # Достаем индекс из элемента
        idx = item.data(Qt.ItemDataRole.UserRole)
        if idx is None or idx >= len(self.ranges_config): return
        
        r_data = self.ranges_config[idx]
        old_name = r_data.get("name", "")
        old_ranges = r_data.get("ranges", [])
        
        # Открываем редактор с предзаполненными данными
        dlg = IPRangeDialog(name=old_name, ranges=old_ranges, parent=self)
        if dlg.exec():
            new_name, new_ranges = dlg.get_data()
            if new_name and new_ranges:
                self.ranges_config[idx]["name"] = new_name
                self.ranges_config[idx]["ranges"] = new_ranges
                self.save_config()
                self.refresh_ranges_list()
    # ======================================    

    def start_scan(self):
        sel = self.list_ranges.selectedItems()
        to_scan = []
        scan_names = []

        # Теперь мы читаем данные напрямую из конфига, а не из текста кнопки!
        if not sel: 
            for r in self.ranges_config:
                to_scan.extend(r.get('ranges', []))
            self.last_scan_name = "All_Ranges"
        else:
            for item in sel:
                idx = item.data(Qt.ItemDataRole.UserRole)
                if idx is not None and idx < len(self.ranges_config):
                    r_data = self.ranges_config[idx]
                    to_scan.extend(r_data.get('ranges', []))
                    scan_names.append(r_data.get('name', 'Net'))
            self.last_scan_name = "_".join(scan_names)

        if not to_scan: 
            QMessageBox.warning(self, "No Range", "Please select IP range.")
            return

        self.table.setRowCount(0)
        self.table.setSortingEnabled(False)
        self.scan_data = []
        self.refresh_stats_cards([])
        
        self.worker = ScanWorker(to_scan)
        self.worker.progress_signal.connect(self.on_progress)
        self.worker.result_signal.connect(self.on_result)
        self.worker.log_signal.connect(self.status_bar.setText)
        self.worker.finished_signal.connect(self.on_finished)
        
        self.btn_scan.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.progress.setValue(0)
        self.worker.start()

    def stop_scan(self):
        if hasattr(self, 'worker'): self.worker.stop()

    def on_progress(self, curr, total):
        self.progress.setValue(int((curr/total)*100))

    def on_result(self, res_list):
        for row in res_list:
            self.scan_data.append(row)
            r = self.table.rowCount()
            self.table.insertRow(r)
            
            class NumItem(QTableWidgetItem):
                def __lt__(self, other):
                    try: return float(self.text().split()[0]) < float(other.text().split()[0])
                    except: return self.text() < other.text()

            self.table.setItem(r, 0, QTableWidgetItem(str(row.get('IP'))))
            self.table.setItem(r, 1, QTableWidgetItem(str(row.get('Model'))))
            self.table.setItem(r, 2, QTableWidgetItem(str(row.get('Algo', '-'))))
            
            hr = str(row.get('Real', '0'))
            hr_item = NumItem(hr)
            if self.dark_mode: hr_item.setForeground(QColor("#00E676"))
            else: hr_item.setForeground(QColor("#007e33")) 
            self.table.setItem(r, 3, hr_item)
            
            self.table.setItem(r, 4, NumItem(str(row.get('Avg'))))
            self.table.setItem(r, 5, NumItem(str(row.get('Temp'))))
            self.table.setItem(r, 6, QTableWidgetItem(str(row.get('Fan'))))
            self.table.setItem(r, 7, QTableWidgetItem(str(row.get('Pool'))))
            self.table.setItem(r, 8, QTableWidgetItem(str(row.get('Worker', '-'))))
            self.table.setItem(r, 9, QTableWidgetItem(str(row.get('Uptime'))))

        self.update_stats()

    def open_web_interface(self, item):
        """Открывает веб-интерфейс асика в браузере по двойному клику на IP"""
        # Получаем таблицу, в которой кликнули
        table = item.tableWidget()
        
        # Проверяем, как называется заголовок колонки, по которой кликнули
        header_item = table.horizontalHeaderItem(item.column())
        
        # Если кликнули именно по колонке "IP"
        if header_item and header_item.text() == "IP":
            ip_address = item.text().strip()
            # Открываем дефолтный браузер
            webbrowser.open(f"http://{ip_address}")

    def on_finished(self):
        self.btn_scan.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.table.setSortingEnabled(True)
        self.status_bar.setText(f"Done. Found {len(self.scan_data)} devices.")

    def update_stats(self):
        if not self.scan_data: return
        df = pd.DataFrame(self.scan_data)
        if 'Algo' not in df.columns: return

        algos = df['Algo'].dropna().unique()
        stats = []
        stats.append({"title": "TOTAL DEVICES", "val": str(len(df))})
        
        for algo in algos:
            if not algo or str(algo) == 'nan': continue
            sub = df[df['Algo'] == algo]
            total_hash = sub['RawHash'].sum() if 'RawHash' in sub.columns else 0
            
            val_str = "0"
            if "SHA-256" in algo: val_str = f"{total_hash:,.1f} TH/s"
            elif "Scrypt" in algo: val_str = f"{total_hash:,.1f} GH/s"
            else: val_str = f"{total_hash:,.1f}"
            
            stats.append({"title": algo.upper(), "val": val_str})
            
        self.refresh_stats_cards(stats)

    def refresh_stats_cards(self, stats_list):
        while self.stats_layout.count():
            item = self.stats_layout.takeAt(0)
            widget = item.widget()
            if widget: widget.deleteLater()
            
        if not stats_list:
            stats_list = [{"title": "TOTAL DEVICES", "val": "0"}]

        for item in stats_list:
            card = QFrame()
            card.setObjectName("StatCard")
            card.setFixedSize(180, 90)
            
            l = QVBoxLayout(card)
            l.setContentsMargins(15, 15, 15, 15)
            l.setSpacing(5)
            
            t = QLabel(item['title'])
            t.setObjectName("CardTitle")
            v = QLabel(item['val'])
            v.setObjectName("CardValue")
            
            l.addWidget(t)
            l.addWidget(v)
            self.stats_layout.addWidget(card)
        self.stats_layout.addStretch()

    def export_csv(self):
        if not self.scan_data: return
        p, _ = QFileDialog.getSaveFileName(self, "Save", "Report.xlsx", "Excel (*.xlsx);;CSV (*.csv)")
        if p:
            df = pd.DataFrame(self.scan_data)
            drops = ['SortIP', 'RawHash', 'Status'] 
            df.drop(columns=[c for c in drops if c in df.columns], inplace=True, errors='ignore')
            if p.endswith(".csv"): df.to_csv(p, index=False)
            else: df.to_excel(p, index=False)
            QMessageBox.information(self, "OK", "Exported.")

    def export_pdf_pro(self):
        # 1. Проверка библиотеки
        if not FPDF_AVAIL:
            QMessageBox.critical(self, "Error", "FPDF library not installed!\nRun: pip install fpdf")
            return
            
        if not self.scan_data: 
            QMessageBox.warning(self, "Empty", "No data to export.")
            return

        try:
            # --- АВТОМАТИЧЕСКИЙ ПУТЬ И ИМЯ ФАЙЛА ---
            export_dir = os.path.join(current_dir, "export_pdf")
            if not os.path.exists(export_dir):
                os.makedirs(export_dir)

            # Берем имя из start_scan
            base_name = getattr(self, 'last_scan_name', 'Manual_Scan')
            # Убираем плохие символы
            clean_name = re.sub(r'[\\/*?:"<>|]', "", base_name)
            if len(clean_name) > 50: clean_name = clean_name[:50]

            timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
            filename = f"{clean_name}_{timestamp}.pdf"
            full_path = os.path.join(export_dir, filename)
            # ---------------------------------------

            # Функция для удаления кириллицы (защита от краша)
            def safe_text(text):
                return str(text).encode('latin-1', 'ignore').decode('latin-1')

            df = pd.DataFrame(self.scan_data)
            
            # --- СИНХРОНИЗАЦИЯ СОРТИРОВКИ С ТАБЛИЦЕЙ ---
            # Узнаем, какая колонка отсортирована в интерфейсе
            sort_col_idx = self.table.horizontalHeader().sortIndicatorSection()
            sort_order = self.table.horizontalHeader().sortIndicatorOrder()
            is_ascending = (sort_order == Qt.SortOrder.AscendingOrder)

            # Карта: Индекс колонки -> Поле в базе данных
            # 0:IP, 1:Model, 2:Algo, 3:Real, 4:Avg, 5:Temp, 6:Fan, 7:Pool, 8:Worker, 9:Uptime
            col_map = {
                0: 'SortIP',   # IP сортируем по числовому значению
                1: 'Model',
                2: 'Algo',
                3: 'RawHash',  # Хешрейт по числу
                4: 'RawHash',
                5: 'Temp',
                7: 'Pool',
                8: 'Worker',
                9: 'Uptime'
            }

            # Применяем сортировку к DataFrame
            sort_key = col_map.get(sort_col_idx)
            if sort_key and sort_key in df.columns:
                # Заполняем пустоты для корректной сортировки
                if sort_key in ['SortIP', 'RawHash']:
                    df[sort_key] = df[sort_key].fillna(0)
                
                df = df.sort_values(by=sort_key, ascending=is_ascending)
            # -------------------------------------------
            
            # ГЕНЕРАЦИЯ PDF
            pdf = PDFReport(orientation='L', unit='mm', format='A4')
            pdf.add_page()
            
            # Заголовок с именем диапазона
            pdf.set_font("Arial", 'B', 12)
            pdf.cell(0, 8, safe_text(f"REPORT: {clean_name}"), 0, 1, 'L')
            
            pdf.set_font("Arial", size=10)
            pdf.cell(0, 5, f"Total Devices: {len(df)}", 0, 1, 'L')
            
            if 'Model' in df.columns:
                makers = df['Model'].apply(lambda x: x.split()[0] if ' ' in x else x).value_counts()
                m_str = ", ".join([f"{k}: {v}" for k,v in makers.items()])
                pdf.cell(0, 5, safe_text(f"Models: {m_str}"), 0, 1, 'L')
            pdf.ln(2)
            
            # Итоги по хешрейту
            if 'RawHash' in df.columns and 'Algo' in df.columns:
                algos = {
                    "SHA-256": "TH/s", "Scrypt": "GH/s", "kHeavyHash": "TH/s",
                    "X11": "GH/s", "Equihash": "kSol/s", "Etchash": "MH/s"
                }
                for algo, unit in algos.items():
                    rows = df[df['Algo'] == algo]
                    if not rows.empty:
                        total = rows['RawHash'].sum()
                        pdf.cell(0, 5, f"Total {algo}: {total:,.2f} {unit}", 0, 1, 'L')
                
                # iPollo (в базе G/s деленные на 1000)
                ipollo_algos = ["Cuckatoo31 (MWC)", "Cuckatoo32 (GRIN)"]
                for algo in ipollo_algos:
                    rows = df[df['Algo'] == algo]
                    if not rows.empty:
                        total = rows['RawHash'].sum() * 1000 
                        pdf.cell(0, 5, f"Total {algo}: {total:,.2f} G/s", 0, 1, 'L')
                    
            pdf.ln(5)
            
            # Таблица
            cols = ["IP", "Model", "Uptime", "Real", "Avg", "Temp", "Fan", "Pool", "Worker", "Algo"]
            widths = [28, 40, 22, 20, 20, 20, 25, 45, 35, 20] 
            
            pdf.set_font("Arial", 'B', 8)
            pdf.set_fill_color(0, 51, 153)
            pdf.set_text_color(255, 255, 255)
            for i, c in enumerate(cols):
                pdf.cell(widths[i], 8, c, 1, 0, 'C', fill=True)
            pdf.ln()
            
            pdf.set_text_color(0, 0, 0)
            pdf.set_font("Arial", size=7)
            
            for _, row in df.iterrows():
                data = [str(row.get(c, '')) for c in cols]
                for i, raw_text in enumerate(data):
                    text = safe_text(raw_text)
                    col_name = cols[i]
                    
                    # Логика обрезки для Worker (показываем хвост)
                    if col_name == "Worker":
                        limit = 22 
                        if len(text) > limit:
                            clean_text = "..." + text[-(limit-3):]
                        else:
                            clean_text = text
                    else:
                        clean_text = text[:38]

                    pdf.cell(widths[i], 6, clean_text, 1, 0, 'C')
                pdf.ln()
            
            # Сохранение и открытие
            pdf.output(full_path)
            
            if os.name == 'nt':
                os.startfile(full_path)
            
            QMessageBox.information(self, "Saved", f"Report saved:\n{filename}")

        except Exception as e:
            # Вывод деталей ошибки (если вдруг снова кириллица просочится)
            import traceback
            error_details = traceback.format_exc()
            QMessageBox.critical(self, "PDF Error", f"{str(e)}\n\nDetails:\n{error_details}")

    def toggle_theme(self):
        self.dark_mode = not self.dark_mode
        self.apply_theme()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 3) 
            if item:
                if self.dark_mode: item.setForeground(QColor("#00E676"))
                else: item.setForeground(QColor("#007e33"))

    def apply_theme(self):
        if self.dark_mode:
            self.setStyleSheet("""
                QMainWindow { background-color: #121212; }
                QWidget { font-family: 'Segoe UI', sans-serif; font-size: 13px; color: #E0E0E0; }
                QDialog { background-color: #1E1E1E; color: #E0E0E0; }
                
                #Sidebar { background-color: #1E1E1E; border-right: 1px solid #333; }
                #ContentArea { background-color: #121212; }
                
                QScrollArea { background-color: transparent; border: none; }
                #StatsWidget { background-color: transparent; }
                
                #Logo { font-size: 20px; font-weight: bold; color: #00E676; letter-spacing: 2px; }
                #SectionHeader { color: #777; font-weight: bold; font-size: 11px; }
                
                QListWidget { background-color: #252525; border: none; border-radius: 6px; padding: 5px; color: #ccc; }
                QListWidget::item:selected { background-color: #333; color: white; border-left: 3px solid #00E676; }
                
                QPushButton { background-color: #2D2D2D; border: none; border-radius: 6px; padding: 8px; color: #E0E0E0; }
                QPushButton:hover { background-color: #3D3D3D; }
                
                #BtnScan { background-color: #00E676; color: #000; font-weight: bold; font-size: 14px; }
                #BtnScan:hover { background-color: #00C853; }
                
                #StatCard { background-color: #1E1E1E; border-radius: 8px; border: 1px solid #333; }
                #CardTitle { color: #888; font-size: 11px; font-weight: bold; }
                #CardValue { color: #00E676; font-size: 18px; font-weight: bold; }
                
                QTableWidget { background-color: #1E1E1E; border: 1px solid #333; gridline-color: #2D2D2D; alternate-background-color: #252525; }
                QHeaderView::section { background-color: #2D2D2D; color: #BBB; border: none; padding: 6px; font-weight: bold; }
                QTableWidget::item:selected { background-color: #004D40; color: white; }
                
                QMenu { background-color: #1E1E1E; border: 1px solid #333; color: #E0E0E0; }
                QMenu::item { padding: 5px 20px; }
                QMenu::item:selected { background-color: #00E676; color: black; }
                
                QRadioButton { color: #E0E0E0; }
            """)
        else:
            self.setStyleSheet("""
                QMainWindow { background-color: #F5F7FA; }
                QWidget { font-family: 'Segoe UI', sans-serif; font-size: 13px; color: #333; }
                QDialog { background-color: #FFF; color: #333; }
                
                #Sidebar { background-color: #FFFFFF; border-right: 1px solid #E1E4E8; }
                #ContentArea { background-color: #F5F7FA; }
                
                QScrollArea { background-color: transparent; border: none; }
                #StatsWidget { background-color: transparent; }
                
                #Logo { font-size: 20px; font-weight: bold; color: #0069D9; letter-spacing: 2px; }
                #SectionHeader { color: #888; font-weight: bold; font-size: 11px; }
                
                QListWidget { background-color: #F0F2F5; border: 1px solid #E1E4E8; border-radius: 6px; padding: 5px; color: #333; }
                QListWidget::item:selected { background-color: #E6F7FF; color: #0069D9; border-left: 3px solid #0069D9; }
                
                QPushButton { background-color: #FFFFFF; border: 1px solid #D1D5DB; border-radius: 6px; padding: 8px; color: #333; }
                QPushButton:hover { background-color: #F3F4F6; }
                
                #BtnScan { background-color: #0069D9; color: #FFF; font-weight: bold; font-size: 14px; border: none; }
                #BtnScan:hover { background-color: #0056b3; }
                
                #StatCard { background-color: #FFFFFF; border-radius: 8px; border: 1px solid #E1E4E8; }
                #CardTitle { color: #6B7280; font-size: 11px; font-weight: bold; }
                #CardValue { color: #0069D9; font-size: 18px; font-weight: bold; }
                
                QTableWidget { background-color: #FFFFFF; border: 1px solid #E1E4E8; gridline-color: #F0F2F5; alternate-background-color: #F9FAFB; color: #333; }
                QHeaderView::section { background-color: #F3F4F6; color: #4B5563; border: none; padding: 6px; font-weight: bold; }
                QTableWidget::item:selected { background-color: #E6F7FF; color: #000; }
                
                QMenu { background-color: #FFFFFF; border: 1px solid #CCC; color: #333; }
                QMenu::item { padding: 5px 20px; }
                QMenu::item:selected { background-color: #0069D9; color: white; }
                
                QRadioButton { color: #333; }
            """)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = GeminiApp()
    window.show()
    sys.exit(app.exec())