import sys
import os
import json
import pandas as pd
import re
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
# 0. ИНИЦИАЛИЗАЦИЯ ПУТЕЙ (КАК В DASHBOARD.PY)
# ==========================================
# Это критически важно для стабильности импортов и конфигов!
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

# Теперь импортируем сканер
try:
    from miner_scanner.core import scan_network_range
except ImportError as e:
    scan_network_range = None
    print(f"CRITICAL ERROR: Не могу найти miner_scanner! {e}")

# ==========================================
# 1. PDF ГЕНЕРАТОР (ОБНОВЛЕННЫЕ КОЛОНКИ)
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
    
    # --- СВОДКА (GENERAL SUMMARY) ---
    pdf.set_font("Arial", 'B', 10)
    pdf.cell(0, 5, "GENERAL SUMMARY", 0, 1, 'L')
    
    pdf.set_font("Arial", size=9)
    total_devs = len(df)
    
    # Модели (Топ 5)
    m_str = "-"
    if 'Model' in df.columns:
        makers = df['Model'].apply(lambda x: str(x).split()[0] if ' ' in str(x) else str(x)).value_counts().head(5)
        m_str = ", ".join([f"{k}: {v}" for k, v in makers.items()])
    
    # Хешрейт
    totals = {}
    for _, row in df.iterrows():
        algo = str(row.get('Algo', 'Unknown'))
        hr_str = str(row.get('Real', '0'))
        val = 0.0
        unit = "TH/s"
        try:
            clean_s = hr_str.upper().replace(",", ".")
            import re
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

    pdf.cell(90, 5, f"Total Devices: {total_devs}", 0, 0)
    pdf.cell(0, 5, f"Models: {m_str}", 0, 1)
    for k, v in totals.items():
        if v > 0: pdf.cell(0, 5, f"Total {k}: {v:,.2f}", 0, 1)

    pdf.ln(5)

    # --- ТАБЛИЦА ---
    # Доступная ширина ~277 мм
    COLS = ["IP", "Model", "Uptime", "Real", "Avg", "Fan", "Temp", "Pool", "Worker", "Algo"]
    
    # НОВЫЕ ШИРИНЫ (Pool и Worker сужены, Fan и Temp читаемые)
    WIDTHS = [
        25,  # IP
        30,  # Model
        20,  # Uptime
        18,  # Real
        18,  # Avg
        30,  # Fan (Широко, чтобы влезло 4 вентилятора)
        25,  # Temp (Широко, чтобы влезли чипы)
        40,  # Pool (Узко, шрифт будет мелкий)
        35,  # Worker (Узко, шрифт мелкий)
        25   # Algo
    ]
    # Сумма = 266 мм (с запасом)

    def draw_table_header():
        pdf.set_font("Arial", 'B', 7)
        pdf.set_fill_color(0, 51, 153)     # Синий фон
        pdf.set_text_color(255, 255, 255)  # Белый текст
        for i, c in enumerate(COLS):
            pdf.cell(WIDTHS[i], 7, c, 1, 0, 'C', fill=True)
        pdf.ln()
        pdf.set_text_color(0, 0, 0)        # Черный текст

    draw_table_header()

    for _, row in df.iterrows():
        if pdf.get_y() > 185:
            pdf.add_page()
            draw_table_header()

        # Подготовка данных
        pool_val = str(row.get('Pool', '-'))
        # Вырезаем мусор из пула
        pool_val = pool_val.replace("stratum+tcp://", "").replace("stratum2+tcp://", "").replace("ssl://", "")
        
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

        row_height = 5.5
        for i, text in enumerate(data_raw):
            w = WIDTHS[i]
            
            # --- УПРАВЛЕНИЕ ШРИФТАМИ ---
            
            # 1. Pool и Worker: МЕЛКИЙ шрифт, чтобы влезло
            if i in [7, 8]:
                pdf.set_font("Arial", size=5)
                # Обрезаем если все равно не лезет
                max_chars = int(w * 0.8) 
                if len(text) > max_chars: text = text[:max_chars] + "."
            
            # 2. Fan и Temp: ОБЫЧНЫЙ шрифт, чтобы было видно цифры
            elif i in [5, 6]:
                pdf.set_font("Arial", size=7) 
                
            # 3. Остальные: ОБЫЧНЫЙ шрифт
            else:
                pdf.set_font("Arial", size=7)
                # Мягкая обрезка
                if len(text) > int(w * 0.6): text = text[:int(w*0.6)]
            
            pdf.cell(w, row_height, text, 1, 0, 'C')
        
        pdf.ln()

    try:
        pdf.output(filename)
    except Exception as e:
        raise Exception(f"Ошибка сохранения PDF: {e}")

# ==========================================
# 2. ПОТОК СКАНЕРА
# ==========================================
class ScannerThread(QThread):
    finished = pyqtSignal(list)
    progress = pyqtSignal(str)

    def __init__(self, ip_range):
        super().__init__()
        self.ip_range = ip_range

    def run(self):
        if not scan_network_range:
            self.progress.emit("❌ Ошибка: модуль miner_scanner не загружен!")
            self.finished.emit([])
            return

        self.progress.emit(f"📡 Сканирую сеть {self.ip_range}...")
        try:
            # Вызов функции из core.py
            results = scan_network_range(self.ip_range)
            self.finished.emit(results if results else [])
        except Exception as e:
            self.progress.emit(f"❌ Ошибка сканирования: {e}")
            self.finished.emit([])

# ==========================================
# 3. ГЛАВНОЕ ОКНО
# ==========================================
class MinerApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MinerHotel Monitor Pro")
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

        self.ranges_file = os.path.join(BASE_DIR, "ip_ranges.json") # Явный путь
        self.scan_results = []
        
        # UI
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # БЛОК УПРАВЛЕНИЯ
        range_group = QGroupBox("📡 Управление Сетями")
        range_layout = QHBoxLayout()
        
        range_layout.addWidget(QLabel("Сохраненные:"))
        self.combo_ranges = QComboBox()
        self.combo_ranges.currentIndexChanged.connect(self.on_combo_change)
        range_layout.addWidget(self.combo_ranges, 2)
        
        btn_del = QPushButton("🗑 Удалить")
        btn_del.setStyleSheet("background-color: #dc3545;")
        btn_del.clicked.connect(self.delete_range)
        range_layout.addWidget(btn_del)
        
        range_layout.addSpacing(20)
        range_layout.addWidget(QLabel("Название:"))
        self.input_name = QLineEdit()
        self.input_name.setPlaceholderText("Напр: Ферма 1")
        range_layout.addWidget(self.input_name)
        
        range_layout.addWidget(QLabel("IP:"))
        self.input_ip = QLineEdit()
        self.input_ip.setPlaceholderText("192.168.0.1-255")
        range_layout.addWidget(self.input_ip, 2)
        
        btn_add = QPushButton("💾 Сохранить")
        btn_add.setStyleSheet("background-color: #198754;")
        btn_add.clicked.connect(self.add_range)
        range_layout.addWidget(btn_add)
        
        range_group.setLayout(range_layout)
        layout.addWidget(range_group)

        # КНОПКА ЗАПУСКА
        action_layout = QHBoxLayout()
        self.btn_scan = QPushButton("▶️ ЗАПУСТИТЬ СКАНЕР")
        self.btn_scan.setMinimumHeight(45)
        self.btn_scan.setStyleSheet("background-color: #0d6efd; font-size: 14px;")
        self.btn_scan.clicked.connect(self.start_scan)
        action_layout.addWidget(self.btn_scan)
        layout.addLayout(action_layout)

        # ТАБЛИЦА
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

        # ПОДВАЛ
        footer = QHBoxLayout()
        self.status_label = QLabel("Готов к работе")
        self.status_label.setStyleSheet("font-size: 12px; color: #666; font-weight: bold;")
        footer.addWidget(self.status_label, 1)
        
        btn_pdf = QPushButton("📄 Скачать PDF")
        btn_pdf.setStyleSheet("background-color: #6c757d;")
        btn_pdf.clicked.connect(self.export_pdf)
        footer.addWidget(btn_pdf)
        
        btn_excel = QPushButton("📗 Скачать Excel")
        btn_excel.setStyleSheet("background-color: #20c997;")
        btn_excel.clicked.connect(self.export_excel)
        footer.addWidget(btn_excel)
        
        layout.addLayout(footer)

        self.load_ranges_from_file()

    # --- ЛОГИКА ---
    def load_ranges_from_file(self):
        self.combo_ranges.clear()
        if os.path.exists(self.ranges_file):
            try:
                with open(self.ranges_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for item in data:
                        if isinstance(item, dict):
                            self.combo_ranges.addItem(f"{item.get('name')} ({item.get('range')})", item.get('range'))
                        elif isinstance(item, str):
                            self.combo_ranges.addItem(item, item)
            except: pass

    def add_range(self):
        name = self.input_name.text().strip()
        ip_val = self.input_ip.text().strip()
        if not ip_val: return QMessageBox.warning(self, "Ошибка", "Введите IP")
        if not name: name = f"Сеть {ip_val}"

        data = []
        if os.path.exists(self.ranges_file):
            try:
                with open(self.ranges_file, "r", encoding="utf-8") as f: data = json.load(f)
            except: pass
        
        # Удаляем старый с таким же IP
        data = [x for x in data if isinstance(x, dict) and x.get("range") != ip_val]
        data.append({"name": name, "range": ip_val})
        
        with open(self.ranges_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        
        self.load_ranges_from_file()
        self.input_name.clear()
        self.input_ip.clear()
        QMessageBox.information(self, "Успех", "Сохранено!")

    def delete_range(self):
        idx = self.combo_ranges.currentIndex()
        if idx < 0: return
        val = self.combo_ranges.currentData()
        
        if QMessageBox.question(self, "Удаление", "Удалить?") == QMessageBox.StandardButton.Yes:
            if os.path.exists(self.ranges_file):
                with open(self.ranges_file, "r", encoding="utf-8") as f: data = json.load(f)
                data = [x for x in data if (isinstance(x, dict) and x.get("range") != val) or (isinstance(x, str) and x != val)]
                with open(self.ranges_file, "w", encoding="utf-8") as f: json.dump(data, f, indent=4)
            self.load_ranges_from_file()

    def on_combo_change(self):
        self.input_ip.setText(self.combo_ranges.currentData())

    def start_scan(self):
        ip = self.input_ip.text().strip()
        if not ip: ip = self.combo_ranges.currentData()
        if not ip: return QMessageBox.warning(self, "Ошибка", "Нет IP")

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
            # Helper для чисел
            def mk_item(val):
                # Пробуем сделать числом для сортировки
                try: 
                    # Убираем ед. измерения для сортировки?
                    # Для простоты пока строкой, но можно улучшить
                    return QTableWidgetItem(str(val))
                except: return QTableWidgetItem(str(val))

            self.table.setItem(row, 0, mk_item(dev.get("IP", "")))
            self.table.setItem(row, 1, mk_item(dev.get("Model", "")))
            self.table.setItem(row, 2, mk_item(dev.get("Uptime", "")))
            self.table.setItem(row, 3, mk_item(dev.get("Real", "")))
            self.table.setItem(row, 4, mk_item(dev.get("Avg", "")))
            self.table.setItem(row, 5, mk_item(dev.get("Fan", "")))
            self.table.setItem(row, 6, mk_item(dev.get("Temp", "")))
            self.table.setItem(row, 7, mk_item(dev.get("Pool", "")))
            self.table.setItem(row, 8, mk_item(dev.get("Worker", "")))
            self.table.setItem(row, 9, mk_item(dev.get("Algo", "")))
            
            # Цвета
            st = str(dev.get("Model", ""))
            color = None
            if "Offline" in st or "Error" in st: color = QColor("#ffe6e6")
            elif "Unstable" in st: color = QColor("#fff3cd")
            
            if color:
                for c in range(10): self.table.item(row, c).setBackground(color)
        
        self.table.setSortingEnabled(True)

    def get_table_data(self):
        rows = self.table.rowCount()
        data = []
        for r in range(rows):
            row_data = {}
            for i, col_name in enumerate(self.cols):
                item = self.table.item(r, i)
                row_data[col_name] = item.text() if item else ""
            data.append(row_data)
        return pd.DataFrame(data)

    def export_pdf(self):
        if self.table.rowCount() == 0: return
        f, _ = QFileDialog.getSaveFileName(self, "Save PDF", f"Report_{datetime.now().strftime('%Y-%m-%d')}.pdf", "PDF (*.pdf)")
        if f:
            try:
                generate_pdf_file(self.get_table_data(), f)
                QMessageBox.information(self, "Успех", "PDF сохранен!")
            except Exception as e: QMessageBox.critical(self, "Ошибка", str(e))

    def export_excel(self):
        if self.table.rowCount() == 0: return
        f, _ = QFileDialog.getSaveFileName(self, "Save Excel", f"Report_{datetime.now().strftime('%Y-%m-%d')}.xlsx", "Excel (*.xlsx)")
        if f:
            try:
                self.get_table_data().to_excel(f, index=False)
                QMessageBox.information(self, "Успех", "Excel сохранен!")
            except Exception as e: QMessageBox.critical(self, "Ошибка", str(e))

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MinerApp()
    window.show()
    sys.exit(app.exec())