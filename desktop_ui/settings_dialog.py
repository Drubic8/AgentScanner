"""Preferences dialog. Changes are committed only after validation and Save."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QStackedWidget, QWidget, QScrollArea, QFormLayout, QComboBox,
    QSpinBox, QCheckBox, QLineEdit, QPushButton, QFileDialog, QMessageBox)
from .preferences import COLUMNS, TABLE_PRESETS, normalize, data_directory


class SettingsDialog(QDialog):
    def __init__(self, current_settings, parent=None, *, configure_access=None):
        super().__init__(parent)
        self.setWindowTitle("Настройки · ASIC Monitor")
        self.resize(840, 650)
        self.setMinimumSize(740, 540)
        self.settings = normalize(current_settings)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(16)
        title = QLabel("Настройки программы")
        title.setObjectName("DialogTitle")
        root.addWidget(title)
        body = QHBoxLayout()
        self.navigation = QListWidget()
        self.navigation.setObjectName("SettingsNavigation")
        self.navigation.setFixedWidth(160)
        self.pages = QStackedWidget()
        body.addWidget(self.navigation)
        body.addWidget(self.pages, 1)
        root.addLayout(body, 1)
        self._general_page()
        self._scanner_page()
        self._columns_page()
        self._export_page("pdf", "PDF-отчёты")
        self._export_page("csv", "Excel и CSV")
        self._access_page(configure_access)
        self.navigation.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.navigation.setCurrentRow(0)
        buttons = QHBoxLayout()
        hint = QLabel("Изменения применятся после сохранения.")
        hint.setObjectName("Muted")
        buttons.addWidget(hint)
        buttons.addStretch()
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Сохранить")
        save.setProperty("primary", True)
        save.setDefault(True)
        save.clicked.connect(self.save_and_close)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        root.addLayout(buttons)

    def _page(self, title, description):
        self.navigation.addItem(title)
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(18, 4, 8, 12)
        layout.setSpacing(14)
        heading = QLabel(title)
        heading.setObjectName("DialogTitle")
        layout.addWidget(heading)
        hint = QLabel(description)
        hint.setWordWrap(True)
        hint.setObjectName("Muted")
        layout.addWidget(hint)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        self.pages.addWidget(scroll)
        return layout

    def _general_page(self):
        layout = self._page("Общие", "Оформление и поведение приложения на этом компьютере.")
        form = QFormLayout()
        form.setSpacing(14)
        self.theme = QComboBox()
        for text, value in (("Как в Windows", "system"), ("Светлая", "light"), ("Тёмная", "dark")):
            self.theme.addItem(text, value)
        self.theme.setCurrentIndex(self.theme.findData(self.settings["theme"]))
        form.addRow("Тема", self.theme)
        self.journal_language = QComboBox()
        self.journal_language.addItem("Русский", "ru")
        self.journal_language.addItem("English", "en")
        self.journal_language.setCurrentIndex(self.journal_language.findData(self.settings["journal_language"]))
        form.addRow("Язык журнала", self.journal_language)
        layout.addLayout(form)
        self.check_updates = QCheckBox("Проверять обновления при запуске")
        self.check_updates.setChecked(self.settings["check_updates"])
        layout.addWidget(self.check_updates)
        hint = QLabel("Для проверки обновлений требуется интернет. Сканирование работает в локальной сети.")
        hint.setWordWrap(True)
        hint.setObjectName("Muted")
        layout.addWidget(hint)
        location = QLabel(f"Настройки и диапазоны сохраняются в:\n{data_directory()}")
        location.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        location.setWordWrap(True)
        location.setObjectName("Muted")
        layout.addStretch()
        layout.addWidget(location)

    def _scanner_page(self):
        layout = self._page("Сканирование", "Выберите производителей и параметры опроса. Новые параметры действуют со следующего сканирования.")
        self.filters = {}
        for key, text in (("scan_bitmain", "Antminer · Bitmain, VNish, PitBit"),
                          ("scan_whatsminer", "Whatsminer · MicroBT"),
                          ("scan_elphapex", "Elphapex"),
                          ("scan_other", "Остальные · Avalon, iPollo, Jasminer, Generic")):
            box = QCheckBox(text)
            box.setChecked(self.settings[key])
            layout.addWidget(box)
            self.filters[key] = box
        form = QFormLayout()
        self.timeout = QSpinBox()
        self.timeout.setRange(1, 10)
        self.timeout.setSuffix(" с")
        self.timeout.setValue(self.settings["timeout"])
        self.workers = QSpinBox()
        self.workers.setRange(1, 128)
        self.workers.setValue(self.settings["workers"])
        form.addRow("Ожидание ответа", self.timeout)
        form.addRow("Параллельные устройства", self.workers)
        self.command_workers = QSpinBox()
        self.command_workers.setRange(1, 32)
        self.command_workers.setValue(self.settings['command_workers'])
        form.addRow("Параллельная отправка команд", self.command_workers)
        layout.addLayout(form)
        hint = QLabel("Сканирование: по умолчанию 2 секунды и 64 устройства. Команды: до 32 устройств одновременно, проверка результата выполняется отдельно. Для медленной сети уменьшите параллельность.\n\nПрофили логинов и паролей настраиваются через «Доступ к ASIC».")
        hint.setWordWrap(True)
        hint.setObjectName("Muted")
        layout.addWidget(hint)
        layout.addStretch()

    def _access_page(self, configure_access):
        layout = self._page("Доступ к ASIC", "Профили логинов и паролей для автоматического сканирования и управления оборудованием.")
        description = QLabel("Настройте общие профили для Antminer, VNish, WhatsMiner и Elphapex, "
                             "а также отдельные профили для нужных IP или подсетей. "
                             "Приоритет имеют профили для конкретных адресов.")
        description.setWordWrap(True)
        layout.addWidget(description)
        self.access_button = QPushButton("Открыть профили доступа")
        self.access_button.setProperty("primary", True)
        self.access_button.setEnabled(configure_access is not None)
        if configure_access:
            self.access_button.clicked.connect(lambda: configure_access(parent=self))
        layout.addWidget(self.access_button)
        hint = QLabel("Пароли скрыты в редакторе и защищены учётной записью Windows при сохранении. "
                      "Профили сохраняются отдельно кнопкой «Сохранить профили»; отмена общих настроек их не отменяет.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addStretch()

    def _column_list(self, layout, key):
        listing = QListWidget()
        listing.setMinimumHeight(160)
        for code, label in COLUMNS.items():
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, code)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if code in self.settings[key] else Qt.CheckState.Unchecked)
            if key == "ui_cols" and code == "IP":
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                item.setToolTip("IP-адрес нужен для выбора и управления устройствами.")
            listing.addItem(item)
        layout.addWidget(listing, 1)
        return listing

    def _columns_page(self):
        layout = self._page("Таблица", "Отображаемые столбцы. IP-адрес остаётся видимым. Состав отчётов настраивается отдельно.")
        form = QFormLayout()
        self.density = QComboBox()
        self.density.addItem("Обычный", "comfortable")
        self.density.addItem("Компактный", "compact")
        self.density.setCurrentIndex(self.density.findData(self.settings["density"]))
        form.addRow("Режим таблицы", self.density)
        layout.addLayout(form)
        hint = QLabel("Компактный режим уменьшает ширину столбцов, высоту строк и отступы. "
                      "Ширину можно изменить вручную за границу заголовка. Полный текст ячейки — при наведении.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.list_ui_cols = self._column_list(layout, "ui_cols")
        presets = QHBoxLayout()
        self.table_preset = QComboBox()
        for code, (label, _) in TABLE_PRESETS.items():
            self.table_preset.addItem(label, code)
        presets.addWidget(self.table_preset, 1)
        apply = QPushButton("Применить набор")
        apply.clicked.connect(self.apply_table_preset)
        presets.addWidget(apply)
        layout.addLayout(presets)
        reset = QPushButton("Сбросить ширину и порядок столбцов")
        reset.clicked.connect(self.reset_table_layout)
        layout.addWidget(reset)

    def apply_table_preset(self):
        code = self.table_preset.currentData()
        columns = TABLE_PRESETS[code][1]
        for index in range(self.list_ui_cols.count()):
            item = self.list_ui_cols.item(index)
            item.setCheckState(Qt.CheckState.Checked if item.data(Qt.ItemDataRole.UserRole) in columns else Qt.CheckState.Unchecked)
        self.density.setCurrentIndex(self.density.findData("compact" if code == "compact" else "comfortable"))

    def reset_table_layout(self):
        from .preferences import defaults
        self.settings["column_widths"] = {}
        self.settings["column_order"] = defaults()["column_order"]

    def _export_page(self, kind, title):
        layout = self._page(title, "Выберите папку, порядок сортировки и столбцы отчёта. Экспорт включает все результаты сканирования.")
        row = QHBoxLayout()
        path = QLineEdit(self.settings["export_dir" if kind == "pdf" else "export_csv_dir"])
        path.setPlaceholderText("По умолчанию — папка данных приложения")
        path.setClearButtonEnabled(True)
        browse = QPushButton("Обзор…")
        def choose():
            folder = QFileDialog.getExistingDirectory(self, "Папка отчётов", path.text())
            if folder:
                path.setText(folder)
        browse.clicked.connect(choose)
        row.addWidget(path, 1)
        row.addWidget(browse)
        layout.addLayout(row)
        copy = QCheckBox("Копировать сохранённый файл в буфер обмена")
        copy.setChecked(self.settings[f"copy_{kind}"])
        layout.addWidget(copy)
        sort = QComboBox()
        for code in ("IP", "Model", "Uptime", "Real HR", "Temp", "Status"):
            sort.addItem(COLUMNS[code], code)
        sort.setCurrentIndex(max(0, sort.findData(self.settings[f"{kind}_sort"])))
        form = QFormLayout()
        form.addRow("Сортировать по", sort)
        layout.addLayout(form)
        setattr(self, f"{kind}_path", path)
        setattr(self, f"{kind}_copy", copy)
        setattr(self, f"{kind}_sort", sort)
        setattr(self, f"list_{kind}_cols", self._column_list(layout, f"{kind}_cols"))

    def save_and_close(self):
        updated = self.settings.copy()
        updated.update({key: box.isChecked() for key, box in self.filters.items()})
        if not any(box.isChecked() for box in self.filters.values()):
            self.navigation.setCurrentRow(1)
            QMessageBox.warning(self, "Сканирование", "Выберите хотя бы одну группу устройств.")
            return
        for key in ("ui_cols", "pdf_cols", "csv_cols"):
            listing = getattr(self, "list_" + key)
            selected = [listing.item(i).data(Qt.ItemDataRole.UserRole) for i in range(listing.count())
                        if listing.item(i).checkState() == Qt.CheckState.Checked]
            if not selected:
                self.navigation.setCurrentRow({"ui_cols": 2, "pdf_cols": 3, "csv_cols": 4}[key])
                QMessageBox.warning(self, "Столбцы", "Выберите хотя бы один столбец.")
                return
            updated[key] = selected
        updated.update(theme=self.theme.currentData(), density=self.density.currentData(),
                       journal_language=self.journal_language.currentData(), table_preset="custom",
                       timeout=self.timeout.value(), workers=self.workers.value(),
                       command_workers=self.command_workers.value(),
                       check_updates=self.check_updates.isChecked())
        for kind in ("pdf", "csv"):
            updated["export_dir" if kind == "pdf" else "export_csv_dir"] = getattr(self, f"{kind}_path").text().strip()
            updated[f"copy_{kind}"] = getattr(self, f"{kind}_copy").isChecked()
            updated[f"{kind}_sort"] = getattr(self, f"{kind}_sort").currentData()
        self.settings = updated
        self.accept()
