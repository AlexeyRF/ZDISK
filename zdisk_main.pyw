import sys
import asyncio
import logging
import json
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QPushButton, QLineEdit, QLabel, QListWidget, QListWidgetItem, 
    QFileDialog, QProgressBar, QMessageBox, QDialog, QCheckBox,
    QInputDialog, QFrame, QFormLayout, QTreeWidget, QTreeWidgetItem,
    QComboBox, QMenu
)
from PyQt6.QtCore import Qt, pyqtSignal, QObject, QTimer, QSize
from PyQt6.QtGui import QAction, QKeySequence, QShortcut, QPixmap, QImage, QIcon
from qasync import QEventLoop, asyncSlot
import qrcode
from io import BytesIO

from zdisk_client import ZDiskClient
from zdisk_crypto import ZDiskCrypto

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("zdisk_ui")

SETTINGS_FILE = "zdisk_settings.json"

class ProgressSignal(QObject):
    update = pyqtSignal(int, int, str)

class SortableTreeWidgetItem(QTreeWidgetItem):
    def __lt__(self, other):
        column = self.treeWidget().sortColumn()
        # For Size (2) and Date (3) columns, use UserRole for sorting
        if column in [2, 3]:
            val_self = self.data(column, Qt.ItemDataRole.UserRole)
            val_other = other.data(column, Qt.ItemDataRole.UserRole)
            if val_self is not None and val_other is not None:
                try:
                    return float(val_self) < float(val_other)
                except (ValueError, TypeError):
                    pass
        return super().__lt__(other)

class LoginDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ZDisk - Вход")
        self.resize(350, 450)
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        
        self.status_label = QLabel("Нажмите 'Войти', чтобы сгенерировать QR-код")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status_label)

        self.qr_label = QLabel()
        self.qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr_label.setFixedSize(300, 300)
        self.qr_label.setStyleSheet("border: 1px solid #ccc; background: white;")
        layout.addWidget(self.qr_label)
        
        self.qr_btn = QPushButton("Войти по QR")
        layout.addWidget(self.qr_btn)
        
        layout.addWidget(QLabel("Отсканируйте QR-код через мобильное приложение Max."))

class SettingsDialog(QDialog):
    def __init__(self, current_chat_id, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ZDisk - Настройки")
        self.resize(300, 150)
        self.setup_ui(current_chat_id)

    def setup_ui(self, chat_id):
        layout = QVBoxLayout(self)
        form = QFormLayout()
        
        self.chat_id_input = QLineEdit()
        self.chat_id_input.setText(str(chat_id))
        form.addRow("Target Chat ID:", self.chat_id_input)
        
        layout.addLayout(form)
        
        btns = QHBoxLayout()
        self.save_btn = QPushButton("Сохранить")
        self.save_btn.clicked.connect(self.accept)
        self.cancel_btn = QPushButton("Отмена")
        self.cancel_btn.clicked.connect(self.reject)
        
        btns.addWidget(self.save_btn)
        btns.addWidget(self.cancel_btn)
        layout.addLayout(btns)

class TrashDialog(QDialog):
    def __init__(self, trash_items, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ZDisk - Корзина")
        self.resize(600, 400)
        self.trash_items = trash_items
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        self.list = QListWidget()
        for item in self.trash_items:
            # item: {name, path, deleted_at, msg_ids: []}
            dt = datetime.fromtimestamp(item['deleted_at']).strftime("%d.%m.%Y %H:%M")
            list_item = QListWidgetItem(f"{item['name']} (из {item['path'] or '/'}) - Удален: {dt}")
            list_item.setData(Qt.ItemDataRole.UserRole, item)
            self.list.addItem(list_item)
        layout.addWidget(self.list)
        
        btns = QHBoxLayout()
        self.restore_btn = QPushButton("Восстановить")
        self.delete_btn = QPushButton("Удалить навсегда")
        btns.addWidget(self.restore_btn)
        btns.addWidget(self.delete_btn)
        layout.addLayout(btns)

class MainWindow(QMainWindow):
    def __init__(self, client: ZDiskClient):
        super().__init__()
        self.client = client
        self.setWindowTitle("ZDisk")
        self.resize(800, 800)
        self.setup_ui()

    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        
        # Меню
        self.menu_bar = self.menuBar()
        file_menu = self.menu_bar.addMenu("Файл")
        self.upload_action = file_menu.addAction("Загрузить файл")
        file_menu.addSeparator()
        self.settings_action = file_menu.addAction("Настройки")
        file_menu.addSeparator()
        self.exit_action = file_menu.addAction("Выход")
        self.exit_action.triggered.connect(QApplication.instance().quit)

        trash_menu = self.menu_bar.addMenu("Корзина")
        self.trash_open_action = trash_menu.addAction("Открыть корзину")
        self.trash_empty_action = trash_menu.addAction("Очистить старое")

        # Панель управления
        top_layout = QHBoxLayout()
        top_layout.addWidget(QLabel("Поиск:"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Введите имя файла...")
        top_layout.addWidget(self.search_input)
        layout.addLayout(top_layout)
        
        # Дерево файлов
        self.file_tree = QTreeWidget()
        self.file_tree.setHeaderLabels(["Имя", "Тип", "Размер", "Дата"])
        self.file_tree.setColumnWidth(0, 300)
        self.file_tree.setColumnWidth(1, 80)
        self.file_tree.setSortingEnabled(True)
        self.file_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        layout.addWidget(self.file_tree)
        
        # Горячие клавиши
        self.shortcut_refresh = QShortcut(QKeySequence("F5"), self)
        self.shortcut_delete = QShortcut(QKeySequence("Del"), self)
        self.shortcut_shift_delete = QShortcut(QKeySequence("Shift+Del"), self)
        
        # Нижняя панель
        footer = QHBoxLayout()
        self.items_count_label = QLabel("Элементов: 0")
        footer.addWidget(self.items_count_label)
        footer.addStretch()
        self.total_space_label = QLabel("Занято: 0 Б")
        footer.addWidget(self.total_space_label)
        layout.addLayout(footer)

        # Область прогресса
        self.progress_label = QLabel("")
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_label)
        layout.addWidget(self.progress_bar)

class ZDiskApp(QObject):
    def __init__(self, app, loop):
        super().__init__()
        self.app = app
        self.loop = loop
        
        # Устанавливаем иконку приложения
        icon_path = "icon.png"
        if getattr(sys, "frozen", False):
            icon_path = os.path.join(sys._MEIPASS, icon_path)
            
        if os.path.exists(icon_path):
            self.app.setWindowIcon(QIcon(icon_path))
        
        self.client = None
        self.main_window = None
        self.login_dialog = None
        self.settings = self.load_settings()
        
        self.all_files = [] 
        self.trash_metadata = [] 
        self.last_msg_time = None 
        
        self.progress_signal = ProgressSignal()
        self.progress_signal.update.connect(self.on_progress_update)

    def load_settings(self):
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return {"target_chat_id": 0, "passwords": {}, "phone": "+79000000000"}

    def save_settings(self):
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.settings, f, indent=4, ensure_ascii=False)

    def on_progress_update(self, current, total, text):
        if self.main_window:
            self.main_window.progress_bar.setVisible(True)
            self.main_window.progress_bar.setMaximum(total)
            self.main_window.progress_bar.setValue(current)
            self.main_window.progress_label.setText(text)
            if current >= total and total > 0:
                QTimer.singleShot(1000, self.hide_progress)

    def hide_progress(self):
        if self.main_window:
            self.main_window.progress_bar.setVisible(False)
            self.main_window.progress_label.setText("")

    async def _async_dialog(self, func, *args, **kwargs):
        future = self.loop.create_future()
        def wrapper():
            try:
                result = func(*args, **kwargs)
                future.set_result(result)
            except Exception as e:
                future.set_exception(e)
        QTimer.singleShot(0, wrapper)
        return await future

    async def start(self):
        phone = self.settings.get("phone", "+79000000000")
        self.client = ZDiskClient(phone, target_chat_id=self.settings['target_chat_id'], loop=self.loop)
        
        # Cleanup temp files from previous sessions
        try:
            await self.loop.run_in_executor(None, self.client.files.clean_all)
        except Exception as e:
            logger.error(f"Error during startup cleanup: {e}")

        from pymax.session.store import SessionStore
        store = SessionStore("cache", "session.db")
        session = await store.load_session()
        await store.close()
        
        if session and session.token:
            print(f"Обнаружена существующая сессия. Вход...")
            await self.client.start()
            try:
                await asyncio.wait_for(self.client.auth_future, timeout=30)
                self.show_main_window()
            except asyncio.TimeoutError:
                await self._async_dialog(QMessageBox.warning, None, "Таймаут", "Не удалось дождаться синхронизации. Попробуйте перезапустить.")
                self.show_login()
        else:
            self.show_login()

    def show_login(self):
        self.login_dialog = LoginDialog()
        self.login_dialog.qr_btn.clicked.connect(self.on_qr_login)
        self.login_dialog.show()

    @asyncSlot()
    async def on_qr_login(self):
        try:
            self.login_dialog.qr_btn.setEnabled(False)
            self.login_dialog.status_label.setText("Генерация QR-кода...")
            
            # Устанавливаем callback для отображения QR
            self.client.on_qr_received = self.display_qr
            
            # Запускаем клиент. pymax сам обнаружит отсутствие токена и вызовет _login_by_qr
            await self.client.start()
            
            # Ждем подтверждения авторизации (до 5 минут)
            try:
                await asyncio.wait_for(self.client.auth_future, timeout=300)
                
                # Удаляем временный файл QR если он есть
                if os.path.exists("login_qr.txt"):
                    os.remove("login_qr.txt")
                    
                self.login_dialog.accept()
                self.show_main_window()
            except asyncio.TimeoutError:
                self.login_dialog.status_label.setText("Время ожидания истекло. Попробуйте снова.")
                self.login_dialog.qr_btn.setEnabled(True)
                
        except Exception as e:
            logger.error(f"QR Login error: {e}")
            if os.path.exists("login_qr.txt"):
                os.remove("login_qr.txt")
            await self._async_dialog(QMessageBox.critical, self.login_dialog, "Ошибка QR", str(e))
            self.login_dialog.qr_btn.setEnabled(True)

    def display_qr(self, link):
        try:
            import io
            from PIL import Image
            
            # Генерируем QR-код
            qr = qrcode.QRCode(version=1, border=1)
            qr.add_data(link)
            qr.make(fit=True)
            
            # Создаем изображение через qrcode (библиотека поддерживает создание PIL Image)
            # Если qrcode.make_image недоступен или хотим ручной контроль как в qr_to_png:
            qr_img = qr.make_image(fill_color="black", back_color="white").convert('RGB')
            
            # Конвертируем PIL Image в QPixmap
            buffer = io.BytesIO()
            qr_img.save(buffer, format="PNG")
            
            pixmap = QPixmap()
            pixmap.loadFromData(buffer.getvalue())
            
            # Масштабируем для красоты
            scaled_pixmap = pixmap.scaled(300, 300, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            
            self.login_dialog.qr_label.setPixmap(scaled_pixmap)
            self.login_dialog.status_label.setText("Ожидание подтверждения в Max...")
            
        except Exception as e:
            logger.error(f"Error displaying QR: {e}")
            self.login_dialog.status_label.setText(f"Ошибка отрисовки QR: {e}")

    def show_main_window(self):
        self.main_window = MainWindow(self.client)
        
        # Меню
        self.main_window.upload_action.triggered.connect(self.upload_file)
        self.main_window.settings_action.triggered.connect(self.show_settings)
        self.main_window.trash_open_action.triggered.connect(self.show_trash)
        self.main_window.trash_empty_action.triggered.connect(self.empty_trash_old)

        # Поиск и выбор
        self.main_window.search_input.textChanged.connect(self.update_ui_list)
        self.main_window.file_tree.itemDoubleClicked.connect(self.download_file)
        self.main_window.file_tree.itemSelectionChanged.connect(self.update_items_count)
        
        # Контекстное меню и Hotkeys
        self.main_window.file_tree.customContextMenuRequested.connect(self.show_context_menu)
        self.main_window.shortcut_refresh.activated.connect(self.refresh_files)
        self.main_window.shortcut_delete.activated.connect(self.on_delete_key)
        self.main_window.shortcut_shift_delete.activated.connect(self.on_shift_delete_key)
        
        self.main_window.show()
        self.refresh_files()

    def show_context_menu(self, position):
        menu = QMenu()
        item = self.main_window.file_tree.itemAt(position)
        
        if item:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if not data.get("is_dir"):
                menu.addAction("Скачать", lambda: self.download_file(item))
                menu.addAction("Переименовать", self.on_rename_file)
                menu.addAction("Перенести", self.on_move_file)
                menu.addAction("В корзину (Del)", self.on_delete_file)
                menu.addAction("Удалить навсегда (Shift+Del)", lambda: self.on_delete_file(permanent=True))
            else:
                menu.addAction("Создать папку здесь", self.create_folder)
                menu.addAction("Загрузить сюда", self.upload_file)
                menu.addAction("Удалить папку", self.on_delete_folder)
        else:
            # Пустое место
            menu.addAction("Загрузить файл", self.upload_file)
            menu.addAction("Создать папку", self.create_folder)
            menu.addSeparator()
            menu.addAction("Загрузить еще (старые)", self.load_more)
        
        menu.addSeparator()
        menu.addAction("Обновить (F5)", self.refresh_files)
        
        menu.exec(self.main_window.file_tree.viewport().mapToGlobal(position))

    @asyncSlot()
    async def empty_trash_old(self):
        try:
            await asyncio.sleep(1)
            await self.refresh_files()
            await self._async_dialog(QMessageBox.information, self.main_window, "Успех", "Корзина очищена от старых файлов.")
        except Exception as e:
            await self._async_dialog(QMessageBox.critical, self.main_window, "Ошибка", str(e))

    @asyncSlot()
    async def refresh_files(self):
        """Полностью обновляет список файлов, загружая столько же, сколько было до этого."""
        current_count = len(self.all_files)
        limit = max(100, (current_count // 100 + 1) * 100 if current_count > 0 else 100)
        
        self.all_files = []
        self.last_msg_time = None
        
        try:
            # Сначала очищаем старое в корзине (серверная логика)
            await self.client.cleanup_trash()
            # Затем загружаем актуальные метаданные корзины
            self.trash_metadata = await self.client.load_trash_metadata()
        except Exception as e:
            logger.error(f"Trash metadata error: {e}")
            
        await self.load_more(limit=limit)

    @asyncSlot()
    async def load_more(self, limit=100):
        if not self.client:
            return
        try:
            new_files = await self.client.fetch_files(limit=limit, from_time=self.last_msg_time)
            if new_files:
                self.all_files.extend(new_files)
                self.last_msg_time = new_files[-1]['time']
                self.update_ui_list()
            else:
                if self.last_msg_time is not None:
                     await self._async_dialog(QMessageBox.information, self.main_window, "Инфо", "Больше файлов нет")
        except Exception as e:
            logger.error(f"Ошибка загрузки: {e}")

    def format_size(self, size_bytes):
        if size_bytes < 1024: return f"{size_bytes} Б"
        elif size_bytes < 1024**2: return f"{size_bytes/1024:.2f} КБ"
        elif size_bytes < 1024**3: return f"{size_bytes/1024**2:.2f} МБ"
        else: return f"{size_bytes/1024**3:.2f} ГБ"

    def update_ui_list(self):
        self.main_window.file_tree.setSortingEnabled(False)
        self.main_window.file_tree.clear()
        
        search_query = self.main_window.search_input.text().lower()
        trash_ids = set()
        for t in self.trash_metadata:
            for mid in t.get('msg_ids', []):
                trash_ids.add(mid)

        visible_files = []
        for f in self.all_files:
            if f['msg_id'] in trash_ids: continue
            if f['name'] == ".keeper": continue
            if search_query in f['name'].lower():
                visible_files.append(f)

        root_items = {} 
        total_size = 0

        # Build tree structure
        for f in visible_files:
            total_size += f['size']
            path_parts = [p for p in f['path'].split("/") if p]
            
            parent = self.main_window.file_tree.invisibleRootItem()
            curr_path = ""
            for part in path_parts:
                curr_path += "/" + part
                if curr_path not in root_items:
                    item = SortableTreeWidgetItem(parent, [part, "Папка", "", ""])
                    item.setData(0, Qt.ItemDataRole.UserRole, {"is_dir": True, "path": curr_path})
                    root_items[curr_path] = item
                parent = root_items[curr_path]
            
            try:
                timestamp = f['time'] / 1000 if f['time'] > 1e11 else f['time']
                date_str = datetime.fromtimestamp(timestamp).strftime("%d.%m.%Y %H:%M")
                date_sort = timestamp
            except Exception:
                date_str = "Неизвестно"
                date_sort = 0
            
            ext = os.path.splitext(f['name'])[1].lower() if "." in f['name'] else "Файл"
            size_str = self.format_size(f['size'])
            
            file_item = SortableTreeWidgetItem(parent, [f['name'], ext, size_str, date_str])
            # Явно устанавливаем данные для сортировки в UserRole
            file_item.setData(2, Qt.ItemDataRole.UserRole, int(f['size']))
            file_item.setData(3, Qt.ItemDataRole.UserRole, float(date_sort))
            file_item.setData(0, Qt.ItemDataRole.UserRole, f)

        # Handle empty folders (.keeper)
        for f in self.all_files:
            if f['name'] == ".keeper" and f['msg_id'] not in trash_ids:
                if search_query and search_query not in f['path'].lower(): continue
                path_parts = [p for p in f['path'].split("/") if p]
                parent = self.main_window.file_tree.invisibleRootItem()
                curr_path = ""
                for part in path_parts:
                    curr_path += "/" + part
                    if curr_path not in root_items:
                        item = SortableTreeWidgetItem(parent, [part, "Папка", "", ""])
                        item.setData(0, Qt.ItemDataRole.UserRole, {"is_dir": True, "path": curr_path})
                        root_items[curr_path] = item
                    parent = root_items[curr_path]

        self.main_window.total_space_label.setText(f"Занято: {self.format_size(total_size)}")
        self.main_window.file_tree.setSortingEnabled(True)
        self.update_items_count()

    def update_items_count(self):
        items = self.main_window.file_tree.selectedItems()
        if items:
            item = items[0]
            parent = item if item.data(0, Qt.ItemDataRole.UserRole).get("is_dir") else item.parent()
            parent = parent or self.main_window.file_tree.invisibleRootItem()
            count = parent.childCount()
            path_name = parent.text(0) if parent.parent() else "корне"
            self.main_window.items_count_label.setText(f"Элементов в {path_name}: {count}")
        else:
            root_count = self.main_window.file_tree.invisibleRootItem().childCount()
            self.main_window.items_count_label.setText(f"Элементов в корне: {root_count}")

    def get_current_target_path(self):
        items = self.main_window.file_tree.selectedItems()
        if not items: return ""
        item = items[0]
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data.get("is_dir"):
            return data["path"]
        else:
            return data["path"]

    @asyncSlot()
    async def create_folder(self):
        base_path = self.get_current_target_path()
        name, ok = await self._async_dialog(QInputDialog.getText, self.main_window, "Новая папка", f"Имя папки (внутри {base_path or '/'}):")
        if not ok or not name: return
        full_path = f"{base_path.strip('/')}/{name}" if base_path else name
        try:
            await self.client.create_folder(full_path)
            await asyncio.sleep(1.5)
            await self.refresh_files()
        except Exception as e:
            await self._async_dialog(QMessageBox.critical, self.main_window, "Ошибка", f"Ошибка: {e}")

    @asyncSlot()
    async def upload_file(self):
        file_path, _ = await self._async_dialog(QFileDialog.getOpenFileName, self.main_window, "Выберите файл")
        if not file_path: return
        target_path = self.get_current_target_path()
        password = None
        reply = await self._async_dialog(QMessageBox.question, self.main_window, "Шифрование", "Зашифровать файл?", 
                                   QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            password, ok = await self._async_dialog(QInputDialog.getText, self.main_window, "Пароль", "Введите пароль:", QLineEdit.EchoMode.Password)
            if not ok or not password: return
        try:
            def progress(curr, total, text):
                self.progress_signal.update.emit(curr, total, text)
            await self.client.upload_file(file_path, password, progress_callback=progress, target_path=target_path)
            await asyncio.sleep(1.5)
            await self.refresh_files()
        except Exception as e:
            await self._async_dialog(QMessageBox.critical, self.main_window, "Ошибка", f"Ошибка: {e}")
        finally:
            self.hide_progress()

    def on_delete_key(self):
        items = self.main_window.file_tree.selectedItems()
        if not items: return
        data = items[0].data(0, Qt.ItemDataRole.UserRole)
        if data.get("is_dir"):
            self.on_delete_folder(False)
        else:
            self.on_delete_file(False)

    def on_shift_delete_key(self):
        items = self.main_window.file_tree.selectedItems()
        if not items: return
        data = items[0].data(0, Qt.ItemDataRole.UserRole)
        if data.get("is_dir"):
            self.on_delete_folder(True)
        else:
            self.on_delete_file(True)

    @asyncSlot()
    async def on_delete_file(self, permanent=False):
        items = self.main_window.file_tree.selectedItems()
        if not items: return
        item = items[0]
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data.get("is_dir"): return 
        title = "Удаление навсегда" if permanent else "В корзину"
        msg = f"Удалить {data['name']} навсегда?" if permanent else f"Переместить {data['name']} в корзину?"
        reply = await self._async_dialog(QMessageBox.question, self.main_window, title, msg, 
                                   QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            try:
                if permanent:
                    await self.client.delete_file(data['msg_id'])
                else:
                    await self.client.move_to_trash(data['name'], data['path'], [data['msg_id']])
                await asyncio.sleep(1.5)
                await self.refresh_files()
            except Exception as e:
                await self._async_dialog(QMessageBox.critical, self.main_window, "Ошибка", f"Ошибка: {e}")

    @asyncSlot()
    async def on_delete_folder(self, permanent=False):
        items = self.main_window.file_tree.selectedItems()
        if not items: return
        item = items[0]
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data.get("is_dir"): return
        title = "Удаление папки навсегда" if permanent else "В корзину"
        msg = f"Удалить папку {data['path']} и всё содержимое НАВСЕГДА?" if permanent else f"Переместить папку {data['path']} в корзину?"
        reply = await self._async_dialog(QMessageBox.question, self.main_window, title, msg, 
                                   QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            try:
                msg_ids = []
                folder_path = data['path'].strip("/")
                for f in self.all_files:
                    f_path = f['path'].strip("/")
                    if f_path == folder_path or f_path.startswith(folder_path + "/"):
                        msg_ids.append(f['msg_id'])
                if permanent:
                    for mid in msg_ids:
                        await self.client.delete_file(mid)
                else:
                    await self.client.move_to_trash(data['path'].split("/")[-1], data['path'], msg_ids)
                await asyncio.sleep(1.5)
                await self.refresh_files()
            except Exception as e:
                await self._async_dialog(QMessageBox.critical, self.main_window, "Ошибка", f"Ошибка: {e}")

    @asyncSlot()
    async def on_rename_file(self):
        items = self.main_window.file_tree.selectedItems()
        if not items: return
        item = items[0]
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data.get("is_dir"): return 
        new_name, ok = await self._async_dialog(QInputDialog.getText, self.main_window, "Переименовать", "Новое имя:", text=data['name'])
        if not ok or not new_name: return
        try:
            await self.client.rename_file(data['msg_id'], data['path'], new_name)
            await asyncio.sleep(1.5)
            await self.refresh_files()
        except Exception as e:
            if "error.edit.timeout" in str(e):
                await self._async_dialog(QMessageBox.warning, self.main_window, "Ошибка", "Сообщение слишком старое, его нельзя изменить (переименовать).")
            else:
                await self._async_dialog(QMessageBox.critical, self.main_window, "Ошибка", f"Ошибка: {e}")

    @asyncSlot()
    async def on_move_file(self):
        items = self.main_window.file_tree.selectedItems()
        if not items: return
        item = items[0]
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data.get("is_dir"): return 
        new_path, ok = await self._async_dialog(QInputDialog.getText, self.main_window, "Перенести", "Новый путь (папка):", text=data['path'])
        if not ok: return
        try:
            await self.client.move_file(data['msg_id'], new_path, data['name'])
            await asyncio.sleep(1.5)
            await self.refresh_files()
        except Exception as e:
            if "error.edit.timeout" in str(e):
                await self._async_dialog(QMessageBox.warning, self.main_window, "Ошибка", "Сообщение слишком старое, его нельзя изменить (перенести).")
            else:
                await self._async_dialog(QMessageBox.critical, self.main_window, "Ошибка", f"Ошибка: {e}")

    def show_trash(self):
        self.trash_dialog = TrashDialog(self.trash_metadata, self.main_window)
        self.trash_dialog.restore_btn.clicked.connect(lambda: self.loop.create_task(self.restore_from_trash(self.trash_dialog)))
        self.trash_dialog.delete_btn.clicked.connect(lambda: self.loop.create_task(self.empty_trash_item(self.trash_dialog)))
        self.trash_dialog.open()

    async def restore_from_trash(self, dialog):
        item = dialog.list.currentItem()
        if not item: return
        data = item.data(Qt.ItemDataRole.UserRole)
        try:
            await self.client.restore_from_trash(data)
            await asyncio.sleep(1.5)
            await self.refresh_files()
            dialog.accept()
        except Exception as e:
            await self._async_dialog(QMessageBox.critical, dialog, "Ошибка", str(e))

    async def empty_trash_item(self, dialog):
        item = dialog.list.currentItem()
        if not item: return
        data = item.data(Qt.ItemDataRole.UserRole)
        try:
            await self.client.permanent_delete_trash(data)
            await asyncio.sleep(1.5)
            await self.refresh_files()
            dialog.accept()
        except Exception as e:
            await self._async_dialog(QMessageBox.critical, dialog, "Ошибка", str(e))

    def show_settings(self):
        dialog = SettingsDialog(self.settings['target_chat_id'], self.main_window)
        if dialog.exec():
            try:
                new_id = int(dialog.chat_id_input.text())
                self.settings['target_chat_id'] = new_id
                self.client.target_chat_id = new_id
                self.save_settings()
                self.refresh_files()
            except ValueError:
                QMessageBox.warning(self.main_window, "Ошибка", "Некорректный ID")

    def download_file(self, item):
        self.loop.create_task(self._download_file_task(item))

    async def _download_file_task(self, item):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data.get("is_dir"): return
        name = data['name']
        password = None
        if name.endswith(".enc"):
            password = self.settings['passwords'].get(name[:-4]) or self.settings['passwords'].get(name)
            if not password:
                password, ok = await self._async_dialog(QInputDialog.getText, self.main_window, "Пароль", "Введите пароль:", QLineEdit.EchoMode.Password)
                if not ok: return
        save_path, _ = await self._async_dialog(QFileDialog.getSaveFileName, self.main_window, "Сохранить как", name)
        if not save_path: return
        try:
            def progress(curr, total, text):
                if "Downloading" in text: text = text.replace("Downloading", "Скачивание")
                self.progress_signal.update.emit(curr, total, text)
            await self.client.download_file(
                data['msg_id'], 
                data['file_id'], 
                save_path, 
                password, 
                progress_callback=progress,
                is_manifest=data.get('is_manifest', False),
                original_name=data['name']
            )
            await self._async_dialog(QMessageBox.information, self.main_window, "Успех", "Файл скачан")
        except Exception as e:
            await self._async_dialog(QMessageBox.critical, self.main_window, "Ошибка", f"Ошибка: {e}")
        finally:
            self.hide_progress()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)
    zdisk = ZDiskApp(app, loop)
    with loop:
        asyncio.ensure_future(zdisk.start())
        loop.run_forever()
