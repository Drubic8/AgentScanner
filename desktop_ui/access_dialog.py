"""Saved access profiles; passwords stay masked outside the editor."""
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                             QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                             QListWidget, QPushButton, QVBoxLayout, QWidget)
from miner_scanner.access import AccessProfile, AccessProfiles

FAMILY_LABELS = {'antminer': 'Antminer · Stock / PitBit', 'vnish': 'Antminer · VNish',
                 'whatsminer': 'WhatsMiner', 'elphapex': 'Elphapex', 'other': 'Другие'}


class AccessProfilesDialog(QDialog):
    def __init__(self, profiles, store, *, target_ips=(), experimental=False, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Профили доступа к ASIC')
        self.resize(820, 520)
        self.profiles = list(profiles.profiles)
        self.store = store
        self.target_ips = list(target_ips)
        self.current = -1
        self.result_profiles = profiles
        layout = QVBoxLayout(self)
        heading = QLabel('Доступ без повторного ввода паролей')
        heading.setStyleSheet('font-size: 18px; font-weight: 600;')
        layout.addWidget(heading)
        description = QLabel('Профили автоматически применяются при сканировании и управлении. '
                             'Сначала используются профили для конкретных IP, затем общие. '
                             'Пароли сохраняются с защитой учётной записи Windows.')
        description.setWordWrap(True)
        layout.addWidget(description)
        body = QHBoxLayout()
        layout.addLayout(body, 1)
        left = QVBoxLayout()
        self.list = QListWidget()
        self.list.setAccessibleName('Сохранённые профили доступа')
        left.addWidget(self.list)
        buttons = QHBoxLayout()
        for title, callback in [('Добавить', self.add_profile), ('Удалить', self.remove_profile)]:
            button = QPushButton(title)
            button.clicked.connect(callback)
            buttons.addWidget(button)
        left.addLayout(buttons)
        order = QHBoxLayout()
        for title, direction in [('Выше', -1), ('Ниже', 1)]:
            button = QPushButton(title)
            button.clicked.connect(lambda checked=False, step=direction: self.move_profile(step))
            order.addWidget(button)
        left.addLayout(order)
        body.addLayout(left, 1)
        self.editor = QWidget()
        form = QFormLayout(self.editor)
        self.name = QLineEdit()
        self.family = QComboBox()
        for key, title in FAMILY_LABELS.items():
            self.family.addItem(title, key)
        self.username = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.show_password = QCheckBox('Показать пароль')
        self.show_password.toggled.connect(lambda visible: self.password.setEchoMode(
            QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password))
        self.auth = QComboBox()
        self.auth.addItems(['digest', 'basic'])
        self.targets = QLineEdit()
        self.targets.setPlaceholderText('* или 10.33.6.0/24, 10.10.90.97')
        self.enabled = QCheckBox('Использовать автоматически')
        for label, widget in [('Название', self.name), ('Устройства / прошивка', self.family),
                              ('Логин', self.username), ('Пароль', self.password),
                              ('', self.show_password), ('HTTP-авторизация', self.auth),
                              ('IP / подсети', self.targets), ('', self.enabled)]:
            form.addRow(label, widget)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        form.addRow(self.hint)
        body.addWidget(self.editor, 2)
        self.experimental = QCheckBox('Разрешить непроверенные команды для выбранных устройств в этом сеансе')
        self.experimental.setEnabled(bool(target_ips))
        self.experimental.setChecked(bool(target_ips) and experimental)
        layout.addWidget(self.experimental)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setStyleSheet('color: #e57373;')
        layout.addWidget(self.error)
        footer = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        footer.button(QDialogButtonBox.StandardButton.Save).setText('Сохранить профили')
        footer.button(QDialogButtonBox.StandardButton.Cancel).setText('Отмена')
        footer.accepted.connect(self.save_profiles)
        footer.rejected.connect(self.reject)
        layout.addWidget(footer)
        self.family.currentIndexChanged.connect(self.update_hint)
        self.list.currentRowChanged.connect(self.select_profile)
        self.refresh(0 if self.profiles else -1)

    def update_hint(self):
        vnish = self.family.currentData() == 'vnish'
        self.username.setEnabled(not vnish)
        self.auth.setEnabled(not vnish)
        self.hint.setText('VNish использует только пароль и получает токен автоматически. '
                          'При отказе проверяется следующий профиль VNish.' if vnish else
                          'Звёздочка означает все IP этого типа оборудования. '
                          'Для нестандартного пароля укажите адреса или подсеть.')

    def commit(self):
        if self.current < 0:
            return True
        try:
            self.profiles[self.current] = AccessProfile(
                self.name.text().strip(), self.family.currentData(), self.username.text().strip(),
                self.password.text(), self.auth.currentText(), self.targets.text().strip(), self.enabled.isChecked())
            self.list.item(self.current).setText(self.profiles[self.current].name)
        except ValueError as exc:
            self.error.setText(str(exc))
            return False
        self.error.clear()
        return True

    def select_profile(self, index):
        if not self.commit():
            self.list.blockSignals(True)
            self.list.setCurrentRow(self.current)
            self.list.blockSignals(False)
            return
        self.current = index
        self.editor.setEnabled(index >= 0)
        if index < 0:
            self.password.clear()
            return
        profile = self.profiles[index]
        self.name.setText(profile.name)
        self.family.setCurrentIndex(self.family.findData(profile.family))
        self.username.setText(profile.username)
        self.password.setText(profile.password)
        self.show_password.setChecked(False)
        self.auth.setCurrentText(profile.auth)
        self.targets.setText(profile.targets)
        self.enabled.setChecked(profile.enabled)
        self.update_hint()

    def refresh(self, selected):
        self.current = -1
        self.list.blockSignals(True)
        self.list.clear()
        self.list.addItems([p.name for p in self.profiles])
        self.list.setCurrentRow(selected)
        self.list.blockSignals(False)
        self.select_profile(selected)

    def add_profile(self):
        if not self.commit():
            return
        self.profiles.append(AccessProfile('Новый профиль', 'antminer', 'root', '',
                                           targets=', '.join(self.target_ips) or '*'))
        self.refresh(len(self.profiles) - 1)

    def remove_profile(self):
        if self.current >= 0:
            index = self.current
            del self.profiles[index]
            self.refresh(min(index, len(self.profiles) - 1))

    def move_profile(self, step):
        index = self.current
        if not self.commit() or not 0 <= index + step < len(self.profiles):
            return
        self.profiles[index], self.profiles[index + step] = self.profiles[index + step], self.profiles[index]
        self.refresh(index + step)

    def save_profiles(self):
        if not self.commit():
            return
        try:
            profiles = AccessProfiles(self.profiles)
            self.store.save(profiles)
        except (OSError, ValueError) as exc:
            self.error.setText(str(exc))
            return
        self.result_profiles = profiles
        self.accept()
