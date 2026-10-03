"""Create/rename a subnet folder with an explicit destination and inline errors."""
from PyQt6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QLabel,
                             QLineEdit, QVBoxLayout)
from .network_groups import node_at, siblings_at, validate_name, walk_nodes


class FolderDialog(QDialog):
    def __init__(self, groups, parent_path=(), path=None, parent=None):
        super().__init__(parent)
        self.groups, self.path = groups, path
        self.setWindowTitle('Изменить папку' if path else 'Создать папку')
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(12)
        title = QLabel(self.windowTitle())
        title.setObjectName('DialogTitle')
        layout.addWidget(title)
        label = QLabel('Название папки')
        self.name = QLineEdit(node_at(groups, path)['name'] if path else '')
        self.name.setMaxLength(120)
        self.name.setPlaceholderText('Площадка 1 или Перевод в сон')
        label.setBuddy(self.name)
        layout.addWidget(label)
        layout.addWidget(self.name)
        label = QLabel('Расположение')
        self.location = QComboBox()
        self.location.addItem('Все сети (корень)', ())
        for p, node in walk_nodes(groups):
            if node.get('type') == 'folder' and (not path or p[:len(path)] != path):
                breadcrumb = ' / '.join(node_at(groups, p[:i])['name'] for i in range(1, len(p)+1))
                self.location.addItem(breadcrumb, p)
        selected = path[:-1] if path else parent_path
        self.location.setCurrentIndex(next((i for i in range(self.location.count())
                                           if self.location.itemData(i) == selected), 0))
        self.location.setEnabled(not path)
        label.setBuddy(self.location)
        layout.addWidget(label)
        layout.addWidget(self.location)
        self.error = QLabel()
        self.error.setObjectName('ValidationError')
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText('Сохранить папку')
        buttons.button(QDialogButtonBox.StandardButton.Save).setProperty('primary', True)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('Отмена')
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.name.setFocus()

    def save(self):
        try:
            names = [n['name'] for i, n in enumerate(siblings_at(self.groups, self.location.currentData()))
                     if not self.path or i != self.path[-1]]
            validate_name(self.name.text(), names)
        except ValueError as exc:
            self.error.setText(str(exc))
            return
        self.accept()

    def get_data(self):
        return self.name.text().strip(), self.location.currentData()
