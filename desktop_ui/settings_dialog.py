"""Preferences dialog. Changes are committed only after validation and Save."""
from .i18n import tr
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QStackedWidget, QWidget, QScrollArea, QFormLayout, QComboBox,
    QSpinBox, QCheckBox, QLineEdit, QPushButton, QFileDialog, QMessageBox,
    QFontComboBox, QGroupBox, QFrame)
from .preferences import (COLUMNS, TABLE_PRESETS, normalize, data_directory,
    DEFAULT_FONT_FAMILY, DEFAULT_FONT_SIZE, INTERFACE_FONT_RANGE, TABLE_FONT_RANGE)
from .fonts import available_font_family, font_css
from .font_controls import CompactFontComboBox, FontSizeComboBox


class SettingsDialog(QDialog):
    def __init__(self, current_settings, parent=None, *, configure_access=None):
        super().__init__(parent)
        self.setWindowTitle(tr('Настройки · ASIC Monitor'))
        self.resize(840, 650)
        self.setMinimumSize(740, 540)
        self.settings = normalize(current_settings)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(16)
        title = QLabel(tr('Настройки программы'))
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
        self._export_page("pdf", tr('PDF-отчёты'))
        self._export_page("csv", tr('Excel и CSV'))
        self._access_page(configure_access)
        self.navigation.ensurePolished()
        self.navigation.setFixedWidth(max(160, max(self.navigation.fontMetrics().horizontalAdvance(
            self.navigation.item(index).text()) for index in range(self.navigation.count())) + 32))
        self.navigation.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.navigation.setCurrentRow(0)
        buttons = QHBoxLayout()
        hint = QLabel(tr('Изменения применятся после сохранения.'))
        hint.setWordWrap(True)
        hint.setObjectName("Muted")
        buttons.addWidget(hint, 1)
        cancel = QPushButton(tr('Отмена'))
        cancel.clicked.connect(self.reject)
        save = QPushButton(tr('Сохранить'))
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
        layout = self._page(tr('Общие'), tr('Оформление и поведение приложения на этом компьютере.'))
        form = QFormLayout()
        form.setSpacing(14)
        self.theme = QComboBox()
        for text, value in ((tr('Как в Windows'), "system"), (tr('Светлая'), "light"), (tr('Тёмная'), "dark")):
            self.theme.addItem(text, value)
        self.theme.setCurrentIndex(self.theme.findData(self.settings["theme"]))
        form.addRow(tr('Тема'), self.theme)
        self.language = QComboBox()
        self.language.addItem("Русский", "ru")
        self.language.addItem("English", "en")
        self.language.setCurrentIndex(self.language.findData(self.settings["language"]))
        form.addRow(tr('Язык интерфейса'), self.language)
        layout.addLayout(form)
        self._font_controls(layout)
        self.check_updates = QCheckBox(tr('Проверять обновления при запуске'))
        self.check_updates.setChecked(self.settings["check_updates"])
        layout.addWidget(self.check_updates)
        hint = QLabel(tr('Для проверки обновлений требуется интернет. Сканирование работает в локальной сети.'))
        hint.setWordWrap(True)
        hint.setObjectName("Muted")
        layout.addWidget(hint)
        location = QLabel(tr('Настройки и диапазоны сохраняются в:\n{p0}', p0=data_directory()))
        location.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        location.setWordWrap(True)
        location.setObjectName("Muted")
        layout.addStretch()
        layout.addWidget(location)

    def _font_controls(self, layout):
        group = QGroupBox(tr('Шрифт и читаемость'))
        content = QVBoxLayout(group)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.font_family = CompactFontComboBox()
        self.font_family.setFontFilters(QFontComboBox.FontFilter.ScalableFonts)
        self.font_family.setEditable(False)
        self.font_family.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.font_family.setMinimumContentsLength(16)
        self.font_family.setCurrentFont(QFont(available_font_family(self.settings['font_family'])))
        self.font_size = FontSizeComboBox(*INTERFACE_FONT_RANGE, self.settings['font_size'])
        self.table_font_size = FontSizeComboBox(*TABLE_FONT_RANGE, self.settings['table_font_size'])
        for text, control in ((tr('Семейство шрифта'), self.font_family),
                              (tr('Размер интерфейса'), self.font_size),
                              (tr('Размер таблиц'), self.table_font_size)):
            label = QLabel(text)
            label.setBuddy(control)
            control.setAccessibleName(text)
            if control is self.font_family:
                form.addRow(label, control)
            else:
                field = QWidget()
                row = QHBoxLayout(field)
                row.setContentsMargins(0, 0, 0, 0)
                row.addWidget(control)
                row.addWidget(QLabel(tr(' пкс').strip()))
                row.addStretch()
                form.addRow(label, field)
        content.addLayout(form)
        self.font_error = QLabel()
        self.font_error.setObjectName('ValidationError')
        self.font_error.setTextFormat(Qt.TextFormat.PlainText)
        self.font_error.setWordWrap(True)
        self.font_error.hide()
        content.addWidget(self.font_error)
        hint = QLabel(tr('Выберите размер из списка или введите свой. Размер таблиц применяется к устройствам и журналу.'))
        hint.setWordWrap(True)
        hint.setObjectName('Muted')
        content.addWidget(hint)
        preview_title = QLabel(tr('Предпросмотр'))
        preview_title.setObjectName('Muted')
        content.addWidget(preview_title)
        preview = QFrame()
        preview.setObjectName('FontPreview')
        preview_layout = QVBoxLayout(preview)
        preview_layout.setContentsMargins(12, 10, 12, 10)
        self.font_preview = QLabel(tr('Обзор оборудования · Устройства'))
        self.table_font_preview = QLabel('192.0.2.21 · Antminer S21\nReal Hash: 200.00 TH/s · worker.01')
        for label in (self.font_preview, self.table_font_preview):
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            preview_layout.addWidget(label)
        content.addWidget(preview)
        self.reset_fonts_button = QPushButton(tr('Сбросить шрифт'))
        self.reset_fonts_button.clicked.connect(self.reset_fonts)
        content.addWidget(self.reset_fonts_button, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(group)
        self.font_family.currentFontChanged.connect(self.update_font_preview)
        self.font_size.valueChanged.connect(self.update_font_preview)
        self.table_font_size.valueChanged.connect(self.update_font_preview)
        self.font_size.currentTextChanged.connect(self.clear_font_error)
        self.table_font_size.currentTextChanged.connect(self.clear_font_error)
        self.update_font_preview()

    def clear_font_error(self):
        if self.font_size.lineEdit().hasAcceptableInput() and self.table_font_size.lineEdit().hasAcceptableInput():
            self.font_error.hide()

    def update_font_preview(self):
        family = self.font_family.currentFont().family()
        self.font_preview.setStyleSheet(font_css(family, self.font_size.value()))
        self.table_font_preview.setStyleSheet(font_css(family, self.table_font_size.value()))

    def reset_fonts(self):
        self.font_family.setCurrentFont(QFont(available_font_family(DEFAULT_FONT_FAMILY)))
        self.font_size.setValue(DEFAULT_FONT_SIZE)
        self.table_font_size.setValue(DEFAULT_FONT_SIZE)

    def _scanner_page(self):
        layout = self._page(tr('Сканирование'), tr('Выберите производителей и параметры опроса. Новые параметры действуют со следующего сканирования.'))
        self.filters = {}
        for key, text in (("scan_bitmain", "Antminer · Bitmain, VNish, PitBit"),
                          ("scan_whatsminer", "Whatsminer · MicroBT"),
                          ("scan_elphapex", "Elphapex"),
                          ("scan_other", tr('Остальные · Avalon, iPollo, Jasminer, Goldshell, Generic'))):
            box = QCheckBox(text)
            box.setChecked(self.settings[key])
            layout.addWidget(box)
            self.filters[key] = box
        form = QFormLayout()
        self.timeout = QSpinBox()
        self.timeout.setRange(1, 10)
        self.timeout.setSuffix(tr(' с'))
        self.timeout.setValue(self.settings["timeout"])
        self.workers = QSpinBox()
        self.workers.setRange(1, 128)
        self.workers.setValue(self.settings["workers"])
        form.addRow(tr('Ожидание ответа'), self.timeout)
        form.addRow(tr('Параллельные устройства'), self.workers)
        self.command_workers = QSpinBox()
        self.command_workers.setRange(1, 32)
        self.command_workers.setValue(self.settings['command_workers'])
        form.addRow(tr('Параллельная отправка команд'), self.command_workers)
        layout.addLayout(form)
        hint = QLabel(tr('Сканирование: по умолчанию 2 секунды и 64 устройства. Команды: до 32 устройств одновременно, проверка результата выполняется отдельно. Для медленной сети уменьшите параллельность.\n\nПрофили логинов и паролей настраиваются через «Доступ к ASIC».'))
        hint.setWordWrap(True)
        hint.setObjectName("Muted")
        layout.addWidget(hint)
        layout.addStretch()

    def _access_page(self, configure_access):
        layout = self._page(tr('Доступ к ASIC'), tr('Профили логинов и паролей для автоматического сканирования и управления оборудованием.'))
        description = QLabel(tr('Настройте общие профили для Antminer, VNish, WhatsMiner и Elphapex, а также отдельные профили для нужных IP или подсетей. Приоритет имеют профили для конкретных адресов.'))
        description.setWordWrap(True)
        layout.addWidget(description)
        self.access_button = QPushButton(tr('Открыть профили доступа'))
        self.access_button.setProperty("primary", True)
        self.access_button.setEnabled(configure_access is not None)
        if configure_access:
            self.access_button.clicked.connect(lambda: configure_access(parent=self))
        layout.addWidget(self.access_button)
        hint = QLabel(tr('Пароли скрыты в редакторе и защищены учётной записью Windows при сохранении. Профили сохраняются отдельно кнопкой «Сохранить профили»; отмена общих настроек их не отменяет.'))
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
                item.setToolTip(tr('IP-адрес нужен для выбора и управления устройствами.'))
            listing.addItem(item)
        layout.addWidget(listing, 1)
        return listing

    def _columns_page(self):
        layout = self._page(tr('Таблица'), tr('Отображаемые столбцы. IP-адрес остаётся видимым. Состав отчётов настраивается отдельно.'))
        form = QFormLayout()
        self.density = QComboBox()
        self.density.addItem(tr('Обычный'), "comfortable")
        self.density.addItem(tr('Компактный'), "compact")
        self.density.setCurrentIndex(self.density.findData(self.settings["density"]))
        form.addRow(tr('Режим таблицы'), self.density)
        layout.addLayout(form)
        self.auto_fit_columns = QCheckBox(tr('Подбирать ширину столбцов после сканирования'))
        self.auto_fit_columns.setChecked(self.settings["auto_fit_columns"])
        layout.addWidget(self.auto_fit_columns)
        hint = QLabel(tr('Компактный режим уменьшает ширину столбцов, высоту строк и отступы. Ширину можно изменить вручную за границу заголовка. Полный текст ячейки — при наведении.'))
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.list_ui_cols = self._column_list(layout, "ui_cols")
        presets = QHBoxLayout()
        self.table_preset = QComboBox()
        for code, (label, _) in TABLE_PRESETS.items():
            self.table_preset.addItem(tr(label), code)
        presets.addWidget(self.table_preset, 1)
        apply = QPushButton(tr('Применить набор'))
        apply.clicked.connect(self.apply_table_preset)
        presets.addWidget(apply)
        layout.addLayout(presets)
        reset = QPushButton(tr('Сбросить ширину и порядок столбцов'))
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
        layout = self._page(title, tr('Выберите папку, порядок сортировки и столбцы отчёта. Экспорт включает все результаты сканирования.'))
        row = QHBoxLayout()
        path = QLineEdit(self.settings["export_dir" if kind == "pdf" else "export_csv_dir"])
        path.setPlaceholderText(tr('По умолчанию — папка данных приложения'))
        path.setClearButtonEnabled(True)
        browse = QPushButton(tr('Обзор…'))
        def choose():
            folder = QFileDialog.getExistingDirectory(self, tr('Папка отчётов'), path.text())
            if folder:
                path.setText(folder)
        browse.clicked.connect(choose)
        row.addWidget(path, 1)
        row.addWidget(browse)
        layout.addLayout(row)
        copy = QCheckBox(tr('Копировать сохранённый файл в буфер обмена'))
        copy.setChecked(self.settings[f"copy_{kind}"])
        layout.addWidget(copy)
        sort = QComboBox()
        for code in ("IP", "Model", "Uptime", "Real HR", "Temp", "Status"):
            sort.addItem(COLUMNS[code], code)
        sort.setCurrentIndex(max(0, sort.findData(self.settings[f"{kind}_sort"])))
        form = QFormLayout()
        form.addRow(tr('Сортировать по'), sort)
        layout.addLayout(form)
        setattr(self, f"{kind}_path", path)
        setattr(self, f"{kind}_copy", copy)
        setattr(self, f"{kind}_sort", sort)
        setattr(self, f"list_{kind}_cols", self._column_list(layout, f"{kind}_cols"))

    def save_and_close(self):
        for control, limits in ((self.font_size, INTERFACE_FONT_RANGE), (self.table_font_size, TABLE_FONT_RANGE)):
            if not control.lineEdit().hasAcceptableInput():
                self.navigation.setCurrentRow(0)
                self.font_error.setText(tr('Для поля «{p0}» введите целое число от {p1} до {p2} пкс.',
                    p0=control.accessibleName(), p1=limits[0], p2=limits[1]))
                self.font_error.show()
                self.pages.widget(0).ensureWidgetVisible(control)
                control.setFocus()
                control.lineEdit().selectAll()
                return
        self.font_error.hide()
        updated = self.settings.copy()
        updated.update({key: box.isChecked() for key, box in self.filters.items()})
        if not any(box.isChecked() for box in self.filters.values()):
            self.navigation.setCurrentRow(1)
            QMessageBox.warning(self, tr('Сканирование'), tr('Выберите хотя бы одну группу устройств.'))
            return
        for key in ("ui_cols", "pdf_cols", "csv_cols"):
            listing = getattr(self, "list_" + key)
            selected = [listing.item(i).data(Qt.ItemDataRole.UserRole) for i in range(listing.count())
                        if listing.item(i).checkState() == Qt.CheckState.Checked]
            if not selected:
                self.navigation.setCurrentRow({"ui_cols": 2, "pdf_cols": 3, "csv_cols": 4}[key])
                QMessageBox.warning(self, tr('Столбцы'), tr('Выберите хотя бы один столбец.'))
                return
            updated[key] = selected
        updated.update(theme=self.theme.currentData(), density=self.density.currentData(),
                       font_family=self.font_family.currentFont().family(),
                       font_size=self.font_size.value(), table_font_size=self.table_font_size.value(),
                       language=self.language.currentData(), journal_language=self.language.currentData(), table_preset="custom",
                       timeout=self.timeout.value(), workers=self.workers.value(),
                       command_workers=self.command_workers.value(),
                       auto_fit_columns=self.auto_fit_columns.isChecked(),
                       check_updates=self.check_updates.isChecked())
        for kind in ("pdf", "csv"):
            updated["export_dir" if kind == "pdf" else "export_csv_dir"] = getattr(self, f"{kind}_path").text().strip()
            updated[f"copy_{kind}"] = getattr(self, f"{kind}_copy").isChecked()
            updated[f"{kind}_sort"] = getattr(self, f"{kind}_sort").currentData()
        self.settings = updated
        self.accept()
