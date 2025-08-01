import pymem
import pymem.process
import win32gui, win32con, win32api
import time, os, json, logging
import requests
from PySide6 import QtWidgets, QtGui, QtCore
from PySide6.QtCore import QFileSystemWatcher, Qt
from PySide6.QtGui import QPainter, QPen, QBrush, QColor, QFont
from qt_material import apply_stylesheet
import sys

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

RESOLUTIONS = [
    (1920, 1080),
    (1440, 1080),
    (1280, 960)
]
CURRENT_RESOLUTION = 0
WINDOW_WIDTH, WINDOW_HEIGHT = RESOLUTIONS[CURRENT_RESOLUTION]
CONFIG_DIR = os.path.join(os.environ['LOCALAPPDATA'], 'temp', 'PyIt')
CONFIG_FILE = os.path.join(CONFIG_DIR, 'config.json')
COLOR_PALETTE = [
    [0.0, 0.5, 0.5],
    [1.0, 1.0, 1.0],
    [0.0, 0.0, 0.0],
    [1.0, 0.0, 0.0],
    [1.0, 1.0, 0.0],
    [0.0, 1.0, 0.0],
    [0.0, 0.0, 1.0],
    [1.0, 0.0, 1.0],
]
DEFAULT_SETTINGS = {
    "esp_enabled": True,
    "enemy_esp": True,
    "enemy_box": True,
    "enemy_skeleton": True,
    "enemy_health": True,
    "enemy_name": True,
    "glow_thickness": 2.0,
    "glow_alpha": 0.3,
    "show_fov_circle": True,
    "show_menu": True,
    "box_color": [0.0, 0.5, 0.5],
    "skeleton_color": [1.0, 1.0, 1.0],
    "name_color": [1.0, 1.0, 1.0],
    "resolution_index": CURRENT_RESOLUTION
}

def load_settings():
    if not os.path.exists(CONFIG_DIR):
        os.makedirs(CONFIG_DIR)
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "w") as f:
            json.dump(DEFAULT_SETTINGS, f, indent=4)
        return DEFAULT_SETTINGS.copy()
    with open(CONFIG_FILE, "r") as f:
        loaded_settings = json.load(f)
    settings = DEFAULT_SETTINGS.copy()
    settings.update(loaded_settings)
    return settings

def save_settings(settings):
    with open(CONFIG_FILE, "w") as f:
        json.dump(settings, f, indent=4)

def get_offsets_and_client_dll():
    try:
        offsets = requests.get('https://raw.githubusercontent.com/a2x/cs2-dumper/main/output/offsets.json').json()
        client_dll = requests.get('https://raw.githubusercontent.com/a2x/cs2-dumper/main/output/client_dll.json').json()
        return offsets, client_dll
    except Exception as e:
        logger.error(f"Failed to fetch offsets or client_dll: {e}")
        raise SystemExit("Cannot proceed without offsets. Exiting.")

def get_window_size(window_title):
    hwnd = win32gui.FindWindow(None, window_title)
    if hwnd:
        rect = win32gui.GetClientRect(hwnd)
        return rect[2], rect[3]
    return WINDOW_WIDTH, WINDOW_HEIGHT

def w2s(mtx, posx, posy, posz, width, height):
    try:
        screenW = mtx[12] * posx + mtx[13] * posy + mtx[14] * posz + mtx[15]
        if screenW > 0.001:
            screenX = mtx[0] * posx + mtx[1] * posy + mtx[2] * posz + mtx[3]
            screenY = mtx[4] * posx + mtx[5] * posy + mtx[6] * posz + mtx[7]
            camX = width / 2
            camY = height / 2
            x = camX + (camX * screenX / screenW)
            y = camY - (camY * screenY / screenW)
            if 0 <= x <= width and 0 <= y <= height:
                return [x, y]
        return [-999, -999]
    except Exception as e:
        logger.error(f"Error in w2s: {e}")
        return [-999, -999]

def get_health_color(health):
    if health <= 0:
        return QColor(255, 0, 0)
    elif health >= 100:
        return QColor(0, 255, 0)
    else:
        red = min(255, int((100 - health) * 2.55))
        green = max(0, int(health * 2.55))
        return QColor(red, green, 0)

key_states = {}
last_key_press_time = {}

def is_key_pressed_global(vk_code):
    try:
        return win32api.GetAsyncKeyState(vk_code) & 0x8000 != 0
    except Exception as e:
        logger.error(f"Error in is_key_pressed_global: {e}")
        return False

def is_key_just_pressed_global(vk_code):
    try:
        current_time = time.time()
        current_state = is_key_pressed_global(vk_code)
        if vk_code not in key_states:
            key_states[vk_code] = False
            last_key_press_time[vk_code] = 0
        if current_state and not key_states[vk_code]:
            if current_time - last_key_press_time[vk_code] > 0.2:
                key_states[vk_code] = True
                last_key_press_time[vk_code] = current_time
                return True
        elif not current_state:
            key_states[vk_code] = False
        return False
    except Exception as e:
        logger.error(f"Error in is_key_just_pressed_global: {e}")
        return False

class OverlayWindow(QtWidgets.QWidget):
    def __init__(self, pm, client, offsets, client_dll, settings):
        super().__init__()
        self.pm = pm
        self.client = client
        self.offsets = offsets
        self.client_dll = client_dll
        self.settings = settings
        self.initUI()
        self.fps = 0
        self.frame_count = 0
        self.last_time = time.time()
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self.update)
        self.timer.start(10)

    def initUI(self):
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(WINDOW_WIDTH, WINDOW_HEIGHT)
        hwnd = self.winId().__int__()
        style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
        style &= ~(win32con.WS_CAPTION | win32con.WS_THICKFRAME)
        win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, style)
        ex_style = win32con.WS_EX_TRANSPARENT | win32con.WS_EX_LAYERED
        win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, ex_style)
        win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, -2, -2, 0, 0,
                              win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        self.esp(painter)
        current_time = time.time()
        self.frame_count += 1
        if current_time - self.last_time >= 1.0:
            self.fps = self.frame_count
            self.frame_count = 0
            self.last_time = current_time

    def esp(self, painter):
        if not self.settings["esp_enabled"]:
            return
        dwEntityList = self.offsets['client.dll']['dwEntityList']
        dwLocalPlayerPawn = self.offsets['client.dll']['dwLocalPlayerPawn']
        dwViewMatrix = self.offsets['client.dll']['dwViewMatrix']
        m_iTeamNum = self.client_dll['client.dll']['classes']['C_BaseEntity']['fields']['m_iTeamNum']
        m_lifeState = self.client_dll['client.dll']['classes']['C_BaseEntity']['fields']['m_lifeState']
        m_pGameSceneNode = self.client_dll['client.dll']['classes']['C_BaseEntity']['fields']['m_pGameSceneNode']
        m_modelState = self.client_dll['client.dll']['classes']['CSkeletonInstance']['fields']['m_modelState']
        m_hPlayerPawn = self.client_dll['client.dll']['classes']['CCSPlayerController']['fields']['m_hPlayerPawn']
        m_iHealth = self.client_dll['client.dll']['classes']['C_BaseEntity']['fields']['m_iHealth']
        m_sSanitizedPlayerName = self.client_dll['client.dll']['classes']['CCSPlayerController']['fields']['m_sSanitizedPlayerName']
        try:
            view_matrix = [self.pm.read_float(self.client + dwViewMatrix + i * 4) for i in range(16)]
            local_player = self.pm.read_longlong(self.client + dwLocalPlayerPawn)
            local_team = self.pm.read_int(local_player + m_iTeamNum)
        except Exception as e:
            logger.error(f"Error initializing ESP: {e}")
            return
        bone_map = {
            'head': 6, 'neck': 5, 'spine_upper': 4, 'spine_mid': 3, 'spine_lower': 2, 'pelvis': 0,
            'left_shoulder': 8, 'left_elbow': 9, 'left_hand': 11, 'right_shoulder': 13, 'right_elbow': 14,
            'right_hand': 16,
            'left_hip': 22, 'left_knee': 23, 'left_foot': 24, 'right_hip': 25, 'right_knee': 26, 'right_foot': 27
        }
        bone_connections = [
            ('head', 'neck'), ('neck', 'spine_upper'), ('spine_upper', 'spine_mid'), ('spine_mid', 'spine_lower'),
            ('spine_lower', 'pelvis'),
            ('spine_upper', 'left_shoulder'), ('left_shoulder', 'left_elbow'), ('left_elbow', 'left_hand'),
            ('spine_upper', 'right_shoulder'), ('right_shoulder', 'right_elbow'), ('right_elbow', 'right_hand'),
            ('pelvis', 'left_hip'), ('left_hip', 'left_knee'), ('left_knee', 'left_foot'),
            ('pelvis', 'right_hip'), ('right_hip', 'right_knee'), ('right_knee', 'right_foot')
        ]
        for i in range(64):
            try:
                entity = self.pm.read_longlong(self.client + dwEntityList)
                if not entity:
                    continue
                list_entry = self.pm.read_longlong(entity + ((8 * (i & 0x7FFF) >> 9) + 16))
                if not list_entry:
                    continue
                entity_controller = self.pm.read_longlong(list_entry + (120) * (i & 0x1FF))
                if not entity_controller:
                    continue
                entity_controller_pawn = self.pm.read_longlong(entity_controller + m_hPlayerPawn)
                if not entity_controller_pawn:
                    continue
                list_entry = self.pm.read_longlong(entity + (0x8 * ((entity_controller_pawn & 0x7FFF) >> 9) + 16))
                if not list_entry:
                    continue
                entity_pawn = self.pm.read_longlong(list_entry + (120) * (entity_controller_pawn & 0x1FF))
                if not entity_pawn or entity_pawn == local_player:
                    continue
                if self.pm.read_int(entity_pawn + m_lifeState) != 256:
                    continue
                is_teammate = self.pm.read_int(entity_pawn + m_iTeamNum) == local_team
                if is_teammate or not self.settings["enemy_esp"]:
                    continue
                health_color = get_health_color(self.pm.read_int(entity_pawn + m_iHealth))
                box_color = QColor(int(self.settings["box_color"][0] * 255), int(self.settings["box_color"][1] * 255),
                                   int(self.settings["box_color"][2] * 255))
                skeleton_color = QColor(int(self.settings["skeleton_color"][0] * 255),
                                        int(self.settings["skeleton_color"][1] * 255),
                                        int(self.settings["skeleton_color"][2] * 255))
                name_color = QColor(int(self.settings["name_color"][0] * 255),
                                    int(self.settings["name_color"][1] * 255),
                                    int(self.settings["name_color"][2] * 255))
                game_scene = self.pm.read_longlong(entity_pawn + m_pGameSceneNode)
                bone_matrix = self.pm.read_longlong(game_scene + m_modelState + 0x80)
                headX = self.pm.read_float(bone_matrix + 6 * 0x20)
                headY = self.pm.read_float(bone_matrix + 6 * 0x20 + 0x4)
                headZ = self.pm.read_float(bone_matrix + 6 * 0x20 + 0x8) + 8
                head_pos = w2s(view_matrix, headX, headY, headZ, WINDOW_WIDTH, WINDOW_HEIGHT)
                legZ = self.pm.read_float(bone_matrix + 28 * 0x20 + 0x8)
                leg_pos = w2s(view_matrix, headX, headY, legZ, WINDOW_WIDTH, WINDOW_HEIGHT)
                if head_pos[0] == -999 or leg_pos[0] == -999:
                    continue
                delta = abs(head_pos[1] - leg_pos[1])
                if delta < 15 or delta > WINDOW_HEIGHT:
                    continue
                leftX = head_pos[0] - delta // 3
                rightX = head_pos[0] + delta // 3
                if (leftX >= 0 and rightX <= WINDOW_WIDTH and
                        head_pos[1] >= 0 and leg_pos[1] <= WINDOW_HEIGHT and
                        leftX < rightX and head_pos[1] < leg_pos[1]):
                    if self.settings["enemy_box"]:
                        painter.setPen(QPen(QColor(0, 0, 0), 2))
                        painter.drawRect(leftX - 1, head_pos[1] - 1, rightX - leftX + 2, leg_pos[1] - head_pos[1] + 2)
                        painter.setPen(QPen(box_color, 1.5))
                        painter.drawRect(leftX, head_pos[1], rightX - leftX, leg_pos[1] - head_pos[1])
                if self.settings["enemy_health"]:
                    entity_hp = self.pm.read_int(entity_pawn + m_iHealth)
                    health_bar_width = 4
                    health_bar_height = delta * (entity_hp / 100.0)
                    health_bar_x = leftX - 10
                    painter.setPen(QPen(QColor(0, 0, 0), 1))
                    painter.setBrush(QBrush(health_color))
                    painter.drawRect(health_bar_x, head_pos[1], health_bar_width, health_bar_height)
                    painter.setBrush(Qt.NoBrush)
                    painter.drawRect(health_bar_x, head_pos[1], health_bar_width, delta)
                if self.settings["enemy_name"]:
                    name_ptr = self.pm.read_longlong(entity_controller + m_sSanitizedPlayerName)
                    player_name = self.pm.read_string(name_ptr, 64) if name_ptr else "Unknown"
                    painter.setFont(QFont("Arial", 10))
                    metrics = painter.fontMetrics()
                    text_width = metrics.horizontalAdvance(player_name)
                    text_x = head_pos[0] - text_width / 2
                    text_y = head_pos[1] - 5
                    painter.setPen(QPen(QColor(0, 0, 0)))
                    for offset_x, offset_y in [(1, 1), (-1, -1), (1, -1), (-1, 1)]:
                        painter.drawText(text_x + offset_x, text_y + offset_y, player_name)
                    painter.setPen(QPen(name_color))
                    painter.drawText(text_x, text_y, player_name)
                if self.settings["enemy_skeleton"]:
                    bone_positions = {}
                    for bone_name, bone_index in bone_map.items():
                        x = self.pm.read_float(bone_matrix + bone_index * 0x20)
                        y = self.pm.read_float(bone_matrix + bone_index * 0x20 + 0x4)
                        z = self.pm.read_float(bone_matrix + bone_index * 0x20 + 0x8)
                        screen_pos = w2s(view_matrix, x, y, z, WINDOW_WIDTH, WINDOW_HEIGHT)
                        if screen_pos[0] != -999:
                            bone_positions[bone_name] = screen_pos
                    painter.setPen(QPen(skeleton_color, 2))
                    for start_bone, end_bone in bone_connections:
                        if start_bone in bone_positions and end_bone in bone_positions:
                            start_pos = bone_positions[start_bone]
                            end_pos = bone_positions[end_bone]
                            painter.drawLine(start_pos[0], start_pos[1], end_pos[0], end_pos[1])
            except Exception as e:
                logger.error(f"Error in ESP loop for entity {i}: {e}")
                continue

class ConfigWindow(QtWidgets.QWidget):
    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.initUI()
        self.setStyleSheet("background-color: #020203;")
        self.move(0, 0)

    def initUI(self):
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setFixedSize(300, 550)
        main_layout = QtWidgets.QVBoxLayout()
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(8)
        resolution_container = self.create_resolution_container()
        main_layout.addWidget(resolution_container)
        esp_container = self.create_esp_container()
        main_layout.addWidget(esp_container)
        color_container = self.create_color_container()
        main_layout.addWidget(color_container)
        main_layout.addItem(
            QtWidgets.QSpacerItem(10, 10, QtWidgets.QSizePolicy.Minimum, QtWidgets.QSizePolicy.Expanding))
        self.setLayout(main_layout)

    def create_resolution_container(self):
        resolution_container = QtWidgets.QWidget()
        resolution_layout = QtWidgets.QVBoxLayout()
        resolution_layout.setContentsMargins(5, 5, 5, 5)
        resolution_layout.setSpacing(5)
        resolution_label = QtWidgets.QLabel("Resolution Settings")
        resolution_label.setAlignment(Qt.AlignCenter)
        resolution_label.setStyleSheet("font-size: 16px; color: #FFFFFF;")
        resolution_layout.addWidget(resolution_label)
        self.resolution_dropdown = QtWidgets.QComboBox()
        resolution_options = [f"{w}x{h}" for w, h in RESOLUTIONS]
        self.resolution_dropdown.addItems(resolution_options)
        self.resolution_dropdown.setCurrentIndex(self.settings["resolution_index"])
        self.resolution_dropdown.setStyleSheet(
            "font-size: 12px; color: #FFFFFF; background-color: #080809; border: 1px solid #FFFFFF; padding: 2px;")
        self.resolution_dropdown.currentIndexChanged.connect(self.change_resolution)
        resolution_layout.addWidget(self.resolution_dropdown)
        resolution_container.setLayout(resolution_layout)
        resolution_container.setStyleSheet("background-color: #080809; border-radius: 5px;")
        return resolution_container

    def change_resolution(self, index):
        self.settings["resolution_index"] = index
        save_settings(self.settings)

    def create_esp_container(self):
        esp_container = QtWidgets.QWidget()
        esp_layout = QtWidgets.QVBoxLayout()
        esp_layout.setContentsMargins(5, 5, 5, 5)
        esp_layout.setSpacing(5)
        esp_label = QtWidgets.QLabel("ESP Settings")
        esp_label.setAlignment(Qt.AlignCenter)
        esp_label.setStyleSheet("font-size: 16px; color: #FFFFFF;")
        esp_layout.addWidget(esp_label)
        self.esp_enabled_cb = QtWidgets.QCheckBox("Enable ESP")
        self.esp_enabled_cb.setChecked(self.settings["esp_enabled"])
        self.esp_enabled_cb.stateChanged.connect(self.save_settings)
        esp_layout.addWidget(self.esp_enabled_cb)
        self.enemy_esp_cb = QtWidgets.QCheckBox("Enemy ESP")
        self.enemy_esp_cb.setChecked(self.settings["enemy_esp"])
        self.enemy_esp_cb.stateChanged.connect(self.save_settings)
        esp_layout.addWidget(self.enemy_esp_cb)
        self.enemy_box_cb = QtWidgets.QCheckBox("Enemy Box")
        self.enemy_box_cb.setChecked(self.settings["enemy_box"])
        self.enemy_box_cb.stateChanged.connect(self.save_settings)
        esp_layout.addWidget(self.enemy_box_cb)
        self.enemy_skeleton_cb = QtWidgets.QCheckBox("Enemy Skeleton")
        self.enemy_skeleton_cb.setChecked(self.settings["enemy_skeleton"])
        self.enemy_skeleton_cb.stateChanged.connect(self.save_settings)
        esp_layout.addWidget(self.enemy_skeleton_cb)
        self.enemy_health_cb = QtWidgets.QCheckBox("Enemy Health")
        self.enemy_health_cb.setChecked(self.settings["enemy_health"])
        self.enemy_health_cb.stateChanged.connect(self.save_settings)
        esp_layout.addWidget(self.enemy_health_cb)
        self.enemy_name_cb = QtWidgets.QCheckBox("Enemy Name")
        self.enemy_name_cb.setChecked(self.settings["enemy_name"])
        self.enemy_name_cb.stateChanged.connect(self.save_settings)
        esp_layout.addWidget(self.enemy_name_cb)
        esp_container.setLayout(esp_layout)
        esp_container.setStyleSheet("background-color: #080809; border-radius: 5px;")
        return esp_container

    def create_color_container(self):
        color_container = QtWidgets.QWidget()
        color_layout = QtWidgets.QVBoxLayout()
        color_layout.setContentsMargins(5, 5, 5, 5)
        color_layout.setSpacing(10)
        color_label = QtWidgets.QLabel("Color Selection")
        color_label.setStyleSheet("font-size: 14px; color: #FFFFFF;")
        color_layout.addWidget(color_label)
        self.feature_dropdown = QtWidgets.QComboBox()
        self.feature_dropdown.addItems(["Box", "Skeleton", "Name"])
        self.feature_dropdown.setStyleSheet(
            "font-size: 12px; color: #FFFFFF; background-color: #080809; border: 1px solid #FFFFFF; padding: 2px;")
        color_layout.addWidget(self.feature_dropdown)
        color_layout.addSpacing(10)
        color_grid = QtWidgets.QGridLayout()
        color_grid.setSpacing(8)
        self.color_buttons = []
        for i, color in enumerate(COLOR_PALETTE):
            btn = QtWidgets.QPushButton()
            btn.setFixedSize(25, 25)
            btn.setStyleSheet(
                f"background-color: rgb({int(color[0] * 255)}, {int(color[1] * 255)}, {int(color[2] * 255)}); border: 1px solid #FFFFFF;")
            btn.clicked.connect(lambda checked, c=color: self.set_color(c))
            color_grid.addWidget(btn, i // 4, i % 4)
            self.color_buttons.append(btn)
        color_layout.addLayout(color_grid)
        color_layout.addSpacing(20)
        color_container.setLayout(color_layout)
        color_container.setStyleSheet("background-color: #080809; border-radius: 5px;")
        return color_container

    def set_color(self, color):
        feature = self.feature_dropdown.currentText().lower()
        key = f"{feature}_color"
        self.settings[key] = list(color)
        save_settings(self.settings)
        self.update_color_buttons()

    def save_settings(self):
        self.settings["esp_enabled"] = self.esp_enabled_cb.isChecked()
        self.settings["enemy_esp"] = self.enemy_esp_cb.isChecked()
        self.settings["enemy_box"] = self.enemy_box_cb.isChecked()
        self.settings["enemy_skeleton"] = self.enemy_skeleton_cb.isChecked()
        self.settings["enemy_health"] = self.enemy_health_cb.isChecked()
        self.settings["enemy_name"] = self.enemy_name_cb.isChecked()
        save_settings(self.settings)
        self.update_color_buttons()

    def update_color_buttons(self):
        feature = self.feature_dropdown.currentText().lower()
        current_color = self.settings[f"{feature}_color"]
        for btn, color in zip(self.color_buttons, COLOR_PALETTE):
            btn.setStyleSheet(
                f"background-color: rgb({int(color[0] * 255)}, {int(color[1] * 255)}, {int(color[2] * 255)}); border: {'2px solid #FFFF00' if color == current_color else '1px solid #FFFFFF'};")

    def mousePressEvent(self, event: QtGui.QMouseEvent):
        if event.button() == Qt.LeftButton:
            self.is_dragging = True
            self.drag_start_position = event.globalPosition().toPoint()

    def mouseMoveEvent(self, event: QtGui.QMouseEvent):
        if hasattr(self, 'is_dragging') and self.is_dragging:
            delta = event.globalPosition().toPoint() - self.drag_start_position
            self.move(self.pos() + delta)
            self.drag_start_position = event.globalPosition().toPoint()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent):
        if event.button() == Qt.LeftButton:
            self.is_dragging = False

def main():
    global WINDOW_WIDTH, WINDOW_HEIGHT, CURRENT_RESOLUTION
    settings = load_settings()
    CURRENT_RESOLUTION = settings["resolution_index"]
    WINDOW_WIDTH, WINDOW_HEIGHT = RESOLUTIONS[CURRENT_RESOLUTION]
    offsets, client_dll = get_offsets_and_client_dll()
    timeout = 30
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            pm = pymem.Pymem("cs2.exe")
            client = pymem.process.module_from_name(pm.process_handle, "client.dll").lpBaseOfDll
            logger.info("Successfully attached to cs2.exe")
            break
        except Exception as e:
            logger.warning(f"Failed to attach to cs2.exe: {e}. Retrying...")
            time.sleep(1)
    else:
        logger.error("Failed to attach to cs2.exe after timeout.")
        raise SystemExit("Cannot proceed without attaching to cs2.exe.")
    app = QtWidgets.QApplication(sys.argv)
    apply_stylesheet(app, theme='dark_purple.xml')
    config_window = ConfigWindow(settings)
    overlay_window = OverlayWindow(pm, client, offsets, client_dll, settings)
    if settings["show_menu"]:
        config_window.show()
    overlay_window.show()
    file_watcher = QFileSystemWatcher([CONFIG_FILE])
    def reload_settings():
        nonlocal settings
        settings.update(load_settings())
        overlay_window.settings = settings
        if settings["show_menu"]:
            config_window.show()
        else:
            config_window.hide()
        config_window.esp_enabled_cb.setChecked(settings["esp_enabled"])
        config_window.enemy_esp_cb.setChecked(settings["enemy_esp"])
        config_window.enemy_box_cb.setChecked(settings["enemy_box"])
        config_window.enemy_skeleton_cb.setChecked(settings["enemy_skeleton"])
        config_window.enemy_health_cb.setChecked(settings["enemy_health"])
        config_window.enemy_name_cb.setChecked(settings["enemy_name"])
        config_window.resolution_dropdown.setCurrentIndex(settings["resolution_index"])
        config_window.update_color_buttons()
        global WINDOW_WIDTH, WINDOW_HEIGHT, CURRENT_RESOLUTION
        if CURRENT_RESOLUTION != settings["resolution_index"]:
            CURRENT_RESOLUTION = settings["resolution_index"]
            WINDOW_WIDTH, WINDOW_HEIGHT = RESOLUTIONS[CURRENT_RESOLUTION]
            overlay_window.setFixedSize(WINDOW_WIDTH, WINDOW_HEIGHT)
    file_watcher.fileChanged.connect(reload_settings)
    def handle_hotkeys():
        VK_INSERT = 0x2D
        if is_key_just_pressed_global(VK_INSERT):
            settings["show_menu"] = not settings["show_menu"]
            if settings["show_menu"]:
                config_window.show()
            else:
                config_window.hide()
            save_settings(settings)
            logger.info(f"Menu toggled: {'ON' if settings['show_menu'] else 'OFF'}")
    hotkey_timer = QtCore.QTimer()
    hotkey_timer.timeout.connect(handle_hotkeys)
    hotkey_timer.start(50)
    sys.exit(app.exec())

if __name__ == "__main__":
    print("waiting for cs2.exe")
    while True:
        time.sleep(1)
        try:
            pm = pymem.Pymem("cs2.exe")
            break
        except Exception:
            pass
    print("starting esp")
    time.sleep(2)
    main()
