import sys
import os
import json
import pandas as pd
import re
import ipaddress
import socket
import gc
from datetime import datetime

# GUI
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLabel, QLineEdit, QPushButton, 
                             QTableWidget, QTableWidgetItem, QProgressBar, 
                             QMessageBox, QHeaderView, QComboBox, QFileDialog, 
                             QGroupBox, QAbstractItemView)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont

# PDF
from fpdf import FPDF

# ==========================================
# 0. ИНИЦИАЛИЗАЦИЯ ПУТЕЙ
# ==========================================
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

# Импорт модуля Core (чтобы мы могли его патчить)
try:
    import miner_scanner.core as core_module
    from miner_scanner.core import scan_network_range
except ImportError as e:
    core_module = None
    scan_network_range = None
    print(f"CRITICAL ERROR: {e}")

# ==========================================
# 1. КЛАСС ДЛЯ УМНОЙ СОРТИРОВКИ
# ==========================================
class SortableItem(QTableWidgetItem):
    def __init__(self, display_text, sort_value):
        super().__init__(display_text)
        self.sort_value = sort_value

    def __lt__(self, other):
        try: return self.sort_value < other.sort_value
        except: return super().__lt__(other)

# Хелперы для сортировки
def parse_hash_value(hr_str):
    if not hr_str or hr_str == "-": return -1.0
    try:
        clean = str(hr_str).upper().replace(",", ".")
        nums = re.findall(r"[-+]?\d*\.\d+|\d+", clean)
        if not nums: return -1.0
        val = float(nums[0])
        if "T" in clean: return val * 1e12
        if "G" in clean: return val * 1e9
        if "M" in clean: return val * 1e6
        if "K" in clean: return val * 1e3
        return val
    except: return -1.0

def parse_ip_value(ip_str):
    try: return int(ipaddress.IPv4Address(ip_str.strip()))
    except: return 0

def parse_temp_value(temp_str):
    try:
        nums = [int(x) for x in re.findall(r"\d+", str(temp_str))]
        return max(nums) if nums else 0
    except: return 0

# ==========================================
# 2. PDF ГЕНЕРАТОР
# ==========================================
class PDFReport(FPDF):
    def header(self):
        self.set_font('Arial', 'B', 12)
        self.cell(0, 8, f'MinerHotel Report - {datetime.now().strftime("%Y-%m-%d %H:%M")}', 0, 1, 'L')
        self.line(10, 18, 287, 18)
        self.ln(10)

    def footer(self):
        self.set_y(-12)
        self.set_font('Arial', 'I', 7)
        self.cell(0, 10, f'Page {self.page_no()}', 0, 0, 'C')

def generate_pdf_file(df, filename):
    pdf = PDFReport(orientation='L', unit='mm', format='A4')
    pdf.set_auto_page_break(auto=False, margin=10)
    pdf.add_page()
    
    # СВОДКА
    pdf.set_font("Arial", 'B', 10)
    pdf.cell(0, 5, "GENERAL SUMMARY", 0, 1, 'L')
    pdf.set_font("Arial", size=9)
    
    # Модели
    m_str = "-"
    if 'Model' in df.columns:
        makers = df['Model'].apply(lambda x: str(x).split()[0] if ' ' in str(x) else str(x)).value_counts().head(5)
        m_str = ", ".join([f"{k}: {v}" for k, v in makers.items()])
    
    # Суммы
    totals = {}
    for _, row in df.iterrows():
        algo = str(row.get('Algo', 'Unknown'))
        hr_str = str(row.get('Real', '0'))
        val = 0.0; unit = "TH/s"
        try:
            clean_s = hr_str.upper().replace(",", ".")
            nums = re.findall(r"[-+]?\d*\.\d+|\d+", clean_s)
            if nums:
                val = float(nums[0])
                if "GH" in clean_s: unit = "GH/s"
                elif "MH" in clean_s: unit = "MH/s"
                elif "K" in clean_s: unit = "kSol/s"
        except: pass
        key = f"{algo} ({unit})"
        if key not in totals: totals[key] = 0.0
        totals[key] += val

    pdf.cell(90, 5, f"Total Devices: {len(df)}", 0, 0)
    pdf.cell(0, 5, f"Models: {m_str}", 0, 1)
    for k, v in totals.items():
        if v > 0: pdf.cell(0, 5, f"Total {k}: {v:,.2f}", 0, 1)
    pdf.ln(5)

    # ТАБЛИЦА
    COLS = ["IP", "Model", "Uptime", "Real", "Avg", "Fan", "Temp", "Pool", "Worker", "Algo"]
    # Настройки ширины (Pool/Worker узкие, Fan/Temp широкие)
    WIDTHS = [25, 30, 20, 18, 18, 30, 25, 40, 35, 25]

    def draw_header():
        pdf.set_font("Arial", 'B', 7)
        pdf.set_fill_color(0, 51, 153)
        pdf.set_text_color(255, 255, 255)
        for i, c in enumerate(COLS): pdf.cell(WIDTHS[i], 7, c, 1, 0, 'C', fill=True)
        pdf.ln()
        pdf.set_text_color(0, 0, 0)

    draw_header()

    for _, row in df.iterrows():
        if pdf.get_y() > 185:
            pdf.add_page(); draw_header()

        pool_val = str(row.get('Pool', '-')).replace("stratum+tcp://", "").replace("ssl://", "")
        data_raw = [
            str(row.get('IP', '-')),
            str(row.get('Model', '-')).replace("Antminer ", ""),
            str(row.get('Uptime', '-')),
            str(row.get('Real', '-')),
            str(row.get('Avg', '-')),
            str(row.get('Fan', '-')),
            str(row.get('Temp', '-')),
            pool_val,
            str(row.get('Worker', '-')),
            str(row.get('Algo', '-'))[:12]
        ]

        for i, text in enumerate(data_raw):
            w = WIDTHS[i]
            if i in [7, 8]: pdf.set_font("Arial", size=5)
            else: pdf.set_font("Arial", size=7)
            
            # Fan/Temp не режем
            if i not in [5, 6]:
                max_len = int(w * (0.8 if i in [7,8] else 0.6))
                if len(text) > max_len: text = text[:max_len]
            
            pdf.cell(w, 5.5, text, 1, 0, 'C')
        pdf.ln()

    try: pdf.output(filename)
    except Exception as e: raise Exception(f"Error: {e}")

# ==========================================
# 3. ПОТОК СКАНЕРА (С ПАТЧЕМ СТАБИЛЬНОСТИ)
# ==========================================
class ScannerThread(QThread):
    finished = pyqtSignal(list)
    progress = pyqtSignal(str)

    def __init__(self, ip_range):
        super().__init__()
        self.ip_range = ip_range

    def run(self):
        if not scan_network_range or not core_module:
            self.progress.emit("❌ Ошибка модулей")
            self.finished.emit([])
            return

        self.progress.emit(f"📡 Сканирую {self.ip_range}...")
        
        # --- FIX STABILITY (MONKEY PATCH) ---
        # Мы меняем переменную MAX_THREADS прямо в памяти запущенной программы,
        # не трогая файл config.py на диске.
        
        # 1. Запоминаем старое значение (скорее всего 150)
        original_threads = getattr(core_module, 'MAX_THREADS', 150)
        
        # 2. Устанавливаем безопасное значение для Desktop (50 потоков)
        # Это устранит "проглатывание" асиков
        core_module.MAX_THREADS = 50 
        
        # 3. Увеличиваем таймаут сокетов глобально на время скана
        gc.collect()
        old_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(10) # 10 секунд на ожидание
        
        try:
            self.msleep(200) # Пауза перед стартом
            results = scan_network_range(self.ip_range)
            self.finished.emit(results if results else [])
        except Exception as e:
            self.progress.emit(f"❌ Ошибка: {e}")
            self.finished.emit([])
        finally:
            # 4. Возвращаем всё как было (хороший тон)
            core_module.MAX_THREADS = original_threads
            if old_timeout: socket.setdefaulttimeout(old_timeout)

# ==========================================
# 4. ГЛАВНОЕ ОКНО
# ==========================================
class MinerApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MinerHotel Monitor Pro (Desktop)")
        self.resize(1280, 800)
        self.setStyleSheet("""
            QMainWindow { background-color: #f4f4f4; color: #333; }
            QGroupBox { font-weight: bold; border: 1px solid #ccc; border-radius: 6px; margin-top: 6px; padding: 10px; background: white; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
            QPushButton { background-color: #0d6efd; color: white; border-radius: 4px; padding: 6px 12px; font-weight: bold; }
            QPushButton:hover { background-color: #0b5ed7; }
            QLineEdit, QComboBox { padding: 6px; border: 1px solid #ced4da; border-radius: 4px; }
            QTableWidget { gridline-color: #e0e0e0; background: white; selection-background-color: #cfe2ff; selection-color: black; }
            QHeaderView::section { background-color: #e9ecef; padding: 4px; border: 1px solid #dee2e6; font-weight: bold; }
        """)

        self.ranges_file = os.path.join(BASE_DIR, "ip_ranges.json")
        
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # Controls
        range_group = QGroupBox("📡 Управление Сетями")
        range_layout = QHBoxLayout()
        range_layout.addWidget(QLabel("Сохраненные:"))
        self.combo_ranges = QComboBox()
        self.combo_ranges.currentIndexChanged.connect(self.on_combo_change)
        range_layout.addWidget(self.combo_ranges, 2)
        btn_del = QPushButton("🗑"); btn_del.setStyleSheet("background-color: #dc3545; max-width: 40px;")
        btn_del.clicked.connect(self.delete_range)
        range_layout.addWidget(btn_del)
        range_layout.addSpacing(20)
        range_layout.addWidget(QLabel("Название:"))
        self.input_name = QLineEdit()
        range_layout.addWidget(self.input_name)
        range_layout.addWidget(QLabel("IP:"))
        self.input_ip = QLineEdit()
        range_layout.addWidget(self.input_ip, 2)
        btn_add = QPushButton("💾 Сохранить")
        btn_add.setStyleSheet("background-color: #198754;")
        btn_add.clicked.connect(self.add_range)
        range_layout.addWidget(btn_add)
        range_group.setLayout(range_layout)
        layout.addWidget(range_group)

        # Scan
        self.btn_scan = QPushButton("▶️ ЗАПУСТИТЬ СКАНЕР")
        self.btn_scan.setMinimumHeight(45)
        self.btn_scan.clicked.connect(self.start_scan)
        layout.addWidget(self.btn_scan)

        # Table
        self.cols = ["IP", "Model", "Uptime", "Real", "Avg", "Fan", "Temp", "Pool", "Worker", "Algo"]
        self.table = QTableWidget()
        self.table.setColumnCount(len(self.cols))
        self.table.setHorizontalHeaderLabels(self.cols)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSortingEnabled(True)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        layout.addWidget(self.table)

        # Footer
        footer = QHBoxLayout()
        self.status_label = QLabel("Готов к работе")
        self.status_label.setStyleSheet("font-weight: bold;")
        footer.addWidget(self.status_label, 1)
        btn_pdf = QPushButton("📄 Скачать PDF"); btn_pdf.clicked.connect(self.export_pdf)
        btn_pdf.setStyleSheet("background-color: #6c757d;")
        footer.addWidget(btn_pdf)
        btn_excel = QPushButton("📗 Скачать Excel"); btn_excel.clicked.connect(self.export_excel)
        btn_excel.setStyleSheet("background-color: #20c997;")
        footer.addWidget(btn_excel)
        layout.addLayout(footer)

        self.load_ranges_from_file()

    def load_ranges_from_file(self):
        self.combo_ranges.clear()
        if os.path.exists(self.ranges_file):
            try:
                with open(self.ranges_file, "r", encoding="utf-8") as f:
                    for item in json.load(f):
                        if isinstance(item, dict): self.combo_ranges.addItem(f"{item.get('name')} ({item.get('range')})", item.get('range'))
            except: pass

    def add_range(self):
        name = self.input_name.text().strip()
        ip = self.input_ip.text().strip()
        if not ip: return
        if not name: name = f"Сеть {ip}"
        data = []
        if os.path.exists(self.ranges_file):
            try:
                with open(self.ranges_file, "r", encoding="utf-8") as f: data = json.load(f)
            except: pass
        data = [x for x in data if isinstance(x, dict) and x.get("range") != ip]
        data.append({"name": name, "range": ip})
        with open(self.ranges_file, "w", encoding="utf-8") as f: json.dump(data, f, indent=4)
        self.load_ranges_from_file(); self.input_name.clear(); self.input_ip.clear()

    def delete_range(self):
        idx = self.combo_ranges.currentIndex()
        if idx < 0: return
        val = self.combo_ranges.currentData()
        if QMessageBox.question(self, "Delete", "Удалить?") == QMessageBox.StandardButton.Yes:
            with open(self.ranges_file, "r") as f: data = json.load(f)
            data = [x for x in data if x.get("range") != val]
            with open(self.ranges_file, "w") as f: json.dump(data, f, indent=4)
            self.load_ranges_from_file()

    def on_combo_change(self):
        self.input_ip.setText(self.combo_ranges.currentData())

    def start_scan(self):
        ip = self.input_ip.text().strip()
        if not ip: ip = self.combo_ranges.currentData()
        if not ip: return QMessageBox.warning(self, "Error", "No IP")
        self.table.setRowCount(0)
        self.btn_scan.setEnabled(False)
        self.worker = ScannerThread(ip)
        self.worker.progress.connect(self.status_label.setText)
        self.worker.finished.connect(self.on_finished)
        self.worker.start()

    def on_finished(self, results):
        self.btn_scan.setEnabled(True)
        self.status_label.setText(f"✅ Найдено: {len(results)}")
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(results))
        for row, dev in enumerate(results):
            ip_txt = str(dev.get("IP", ""))
            self.table.setItem(row, 0, SortableItem(ip_txt, parse_ip_value(ip_txt)))
            self.table.setItem(row, 1, SortableItem(str(dev.get("Model", "")), str(dev.get("Model", ""))))
            self.table.setItem(row, 2, SortableItem(str(dev.get("Uptime", "")), str(dev.get("Uptime", ""))))
            self.table.setItem(row, 3, SortableItem(str(dev.get("Real", "")), parse_hash_value(str(dev.get("Real", "")))))
            self.table.setItem(row, 4, SortableItem(str(dev.get("Avg", "")), parse_hash_value(str(dev.get("Avg", "")))))
            self.table.setItem(row, 5, SortableItem(str(dev.get("Fan", "")), str(dev.get("Fan", ""))))
            self.table.setItem(row, 6, SortableItem(str(dev.get("Temp", "")), parse_temp_value(str(dev.get("Temp", "")))))
            self.table.setItem(row, 7, SortableItem(str(dev.get("Pool", "")), str(dev.get("Pool", ""))))
            self.table.setItem(row, 8, SortableItem(str(dev.get("Worker", "")), str(dev.get("Worker", ""))))
            self.table.setItem(row, 9, SortableItem(str(dev.get("Algo", "")), str(dev.get("Algo", ""))))
            st = str(dev.get("Model", ""))
            if "Offline" in st or "Error" in st: 
                for c in range(10): self.table.item(row, c).setBackground(QColor("#ffe6e6"))
            elif "Unstable" in st:
                for c in range(10): self.table.item(row, c).setBackground(QColor("#fff3cd"))
        self.table.setSortingEnabled(True)

    def get_table_data(self):
        rows = self.table.rowCount()
        data = []
        for r in range(rows):
            row_data = {}
            for i, col in enumerate(self.cols):
                item = self.table.item(r, i)
                row_data[col] = item.text() if item else ""
            data.append(row_data)
        return pd.DataFrame(data)

    def export_pdf(self):
        if self.table.rowCount() == 0: return
        f, _ = QFileDialog.getSaveFileName(self, "Save", f"Report_{datetime.now().strftime('%Y-%m-%d')}.pdf", "PDF (*.pdf)")
        if f:
            try: generate_pdf_file(self.get_table_data(), f); QMessageBox.information(self, "OK", "Saved!")
            except Exception as e: QMessageBox.critical(self, "Error", str(e))

    def export_excel(self):
        if self.table.rowCount() == 0: return
        f, _ = QFileDialog.getSaveFileName(self, "Save", f"Report_{datetime.now().strftime('%Y-%m-%d')}.xlsx", "Excel (*.xlsx)")
        if f:
            try: self.get_table_data().to_excel(f, index=False); QMessageBox.information(self, "OK", "Saved!")
            except Exception as e: QMessageBox.critical(self, "Error", str(e))

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MinerApp()
    window.show()
    sys.exit(app.exec())