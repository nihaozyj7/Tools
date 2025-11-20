#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PyQt6 版 —— 内嵌歌词微调器（界面微调：右下角备份按钮）
- 本文件在原实现基础上调整了底部布局：
  * 右下角新增“写入并备份”按钮，使用强调色（accent）
  * 底部微调区域与备份按钮分为左右两部分
- 依赖：PyQt6, mutagen, python-vlc (可选), qdarkstyle (可选)
"""

from __future__ import annotations

import sys
import threading
import time
import shutil
import re
from pathlib import Path
from typing import List, Optional, Tuple

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QSplitter,
    QFileDialog,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QPushButton,
    QLabel,
    QSlider,
    QListWidget,
    QListWidgetItem,
    QTextEdit,
    QStatusBar,
)

from mutagen import File as MutagenFile
from mutagen.id3 import ID3, USLT, SYLT, Encoding
from mutagen.flac import FLAC
from mutagen.mp4 import MP4

try:
    import vlc  # type: ignore
    HAS_VLC = True
except Exception:
    HAS_VLC = False

try:
    import qdarkstyle  # type: ignore
    HAS_QDARK = True
except Exception:
    HAS_QDARK = False


# ---------------------------
# 数据结构与 LRC 工具函数
# ---------------------------
class LyricLine:
    """表示一行 LRC 歌词：时间戳（毫秒）和文本。"""

    def __init__(self, timestamp_ms: int, text: str):
        # 行时间（毫秒）
        self.timestamp_ms: int = int(timestamp_ms)
        # 行文本
        self.text: str = text

    def to_lrc_tag(self) -> str:
        """将此行序列化为 LRC 标签文本，例如 "[01:23.45]歌词文本"。"""
        ms = max(0, int(self.timestamp_ms))
        sec = ms // 1000
        m = sec // 60
        s = sec % 60
        hundredths = (ms % 1000) // 10
        return f"[{m:02d}:{s:02d}.{hundredths:02d}]{self.text}"


def parse_lrc_text(raw: str) -> List[LyricLine]:
    """解析字符串中的 LRC 标签，返回按时间排序的 LyricLine 列表。"""
    lines: List[LyricLine] = []
    for raw_line in (raw or "").splitlines():
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        tags = re.findall(r"\[(\d+):(\d+)(?:\.(\d+))?\]", raw_line)
        text = re.sub(r"\[\d+:\d+(?:\.\d+)?\]", "", raw_line).strip()
        if tags:
            for (min_s, sec_s, frac_s) in tags:
                try:
                    m = int(min_s)
                    s = int(sec_s)
                    ms = m * 60 * 1000 + s * 1000
                    if frac_s:
                        f = frac_s
                        if len(f) == 3:
                            ms += int(f)
                        elif len(f) == 2:
                            ms += int(f) * 10
                        else:
                            ms += int(f[:3].ljust(3, "0"))
                    lines.append(LyricLine(ms, text))
                except Exception:
                    continue
        else:
            continue
    lines.sort(key=lambda x: x.timestamp_ms)
    return lines


def lrc_lines_to_text(lines: List[LyricLine]) -> str:
    """把 LyricLine 列表写回为 LRC 文本（每行一个标签）。"""
    return "\n".join(l.to_lrc_tag() for l in lines)


# ---------------------------
# 嵌入标签读写（mutagen）
# ---------------------------
def read_embedded_lyrics(path: Path) -> Tuple[str, str]:
    """
    读取音频文件嵌入的歌词并返回 (lyrics_text, source_tag)。
    source_tag 例子：'id3:USLT', 'id3:SYLT', 'flac:LYRICS', 'mp4:©lyr', 'none'
    """
    audio = MutagenFile(path)
    if audio is None:
        return "", "none"
    try:
        if path.suffix.lower() == ".mp3" or (hasattr(audio, "tags") and isinstance(audio.tags, ID3)):
            try:
                id3 = ID3(path)
            except Exception:
                id3 = audio.tags
            if id3 is not None:
                sylts = id3.getall("SYLT")
                if sylts:
                    txt = sylts[0].text if hasattr(sylts[0], "text") else str(sylts[0])
                    return txt, "id3:SYLT"
                uslts = id3.getall("USLT")
                if uslts:
                    txt = uslts[0].text if hasattr(uslts[0], "text") else str(uslts[0])
                    return txt, "id3:USLT"
            return "", "none"
        if isinstance(audio, FLAC) or path.suffix.lower() == ".flac":
            try:
                fl = FLAC(path)
                for key in ("LYRICS", "UNSYNCEDLYRICS", "lyrics", "unsyncedlyrics"):
                    if key in fl.tags:
                        v = fl.tags.get(key)
                        if isinstance(v, list):
                            return "\n".join(v), f"flac:{key}"
                        else:
                            return str(v), f"flac:{key}"
            except Exception:
                pass
            return "", "none"
        if isinstance(audio, MP4) or path.suffix.lower() in (".m4a", ".mp4"):
            try:
                mp4 = MP4(path)
                for key in ("\xa9lyr", "©lyr", "lyrics"):
                    if key in mp4.tags:
                        v = mp4.tags.get(key)
                        if isinstance(v, list):
                            return "\n".join(v), f"mp4:{key}"
                        else:
                            return str(v), f"mp4:{key}"
            except Exception:
                pass
            return "", "none"
        tags = getattr(audio, "tags", {}) or {}
        for candidate in ("LYRICS", "UNSYNCEDLYRICS", "lyrics", "unsyncedlyrics", "\xa9lyr"):
            if candidate in tags:
                v = tags[candidate]
                if isinstance(v, list):
                    return "\n".join(v), f"tag:{candidate}"
                else:
                    return str(v), f"tag:{candidate}"
    except Exception:
        return "", "none"
    return "", "none"


def write_embedded_lyrics(path: Path, new_text: str, source_tag: str, make_backup: bool = True) -> None:
    """
    把 new_text 写回到指定文件的 source_tag（read_embedded_lyrics 返回的标签）。
    - 先备份原文件到父目录下的 .bf 子文件夹中
    - 对 SYLT 做 best-effort 写入并同时写 USLT 作为回退
    - 对 FLAC/MP4 写回相同键
    """
    if make_backup:
        backup_dir = path.parent / ".bf"
        backup_dir.mkdir(exist_ok=True)
        backup_path = backup_dir / (path.name + ".bak")
        shutil.copy2(path, backup_path)

    audio = MutagenFile(path)
    if source_tag.startswith("id3") or path.suffix.lower() == ".mp3":
        try:
            id3 = ID3(path)
        except Exception:
            id3 = ID3()
        if source_tag == "id3:SYLT":
            try:
                syl = SYLT(encoding=Encoding.UTF16, lang="eng", format=2, type=1, desc="", text=new_text)
                id3.delall("SYLT")
                id3.add(syl)
                id3.delall("USLT")
                id3.add(USLT(encoding=3, lang="eng", desc="", text=new_text))
            except Exception:
                id3.delall("SYLT")
                id3.delall("USLT")
                id3.add(USLT(encoding=3, lang="eng", desc="", text=new_text))
        else:
            id3.delall("USLT")
            id3.add(USLT(encoding=3, lang="eng", desc="", text=new_text))
        id3.save(path)
        return

    if source_tag.startswith("flac:") or path.suffix.lower() == ".flac":
        key = source_tag.split(":", 1)[1] if ":" in source_tag else "LYRICS"
        fl = FLAC(path)
        if fl.tags is None:
            fl.add_tags()
        fl.tags.pop(key, None)
        fl.tags[key] = [new_text]
        fl.save()
        return

    if source_tag.startswith("mp4:") or path.suffix.lower() in (".m4a", ".mp4"):
        key = source_tag.split(":", 1)[1] if ":" in source_tag else "\xa9lyr"
        mp4 = MP4(path)
        if mp4.tags is None:
            mp4.add_tags()
        mp4.tags[key] = [new_text]
        mp4.save()
        return

    if audio is not None:
        tags = getattr(audio, "tags", {})
        for candidate in ("LYRICS", "UNSYNCEDLYRICS", "lyrics", "unsyncedlyrics", "\xa9lyr"):
            if candidate in tags:
                try:
                    if isinstance(tags[candidate], list):
                        tags[candidate] = [new_text]
                    else:
                        tags[candidate] = new_text
                    audio.save()
                    return
                except Exception:
                    pass
    raise RuntimeError(f"无法写回歌词到 {path} 的 {source_tag}")


# ---------------------------
# 读取基本元信息（title/artist）
# ---------------------------
def read_basic_metadata(path: Path) -> Tuple[Optional[str], Optional[str]]:
    """读取基本元信息 title / artist，尽量使用 easy 接口回退到具体格式解析。"""
    audio = MutagenFile(path)
    if audio is None:
        return None, None
    tags = getattr(audio, "tags", {}) or {}
    try:
        easy = MutagenFile(path, easy=True)
        if easy and easy.tags:
            title = easy.tags.get("title", [None])[0]
            artist = easy.tags.get("artist", [None])[0]
            return title, artist
    except Exception:
        pass
    try:
        id3 = ID3(path)
        title = None
        artist = None
        if id3 is not None:
            if "TIT2" in id3:
                title = str(id3["TIT2"].text[0])
            if "TPE1" in id3:
                artist = str(id3["TPE1"].text[0])
            return title, artist
    except Exception:
        pass
    try:
        if path.suffix.lower() == ".flac":
            fl = FLAC(path)
            title = fl.tags.get("title", [None])[0] if fl.tags and "title" in fl.tags else None
            artist = fl.tags.get("artist", [None])[0] if fl.tags and "artist" in fl.tags else None
            return title, artist
    except Exception:
        pass
    try:
        if path.suffix.lower() in (".mp4", ".m4a"):
            mp4 = MP4(path)
            title = mp4.tags.get("\xa9nam", [None])[0] if mp4.tags and "\xa9nam" in mp4.tags else None
            artist = mp4.tags.get("\xa9ART", [None])[0] if mp4.tags and "\xa9ART" in mp4.tags else None
            return title, artist
    except Exception:
        pass
    return None, None


# ---------------------------
# 播放器后端：VLC 与 模拟
# ---------------------------
class BasePlayer:
    """播放器抽象接口。"""
    def set_media(self, path: Optional[Path]) -> None: ...
    def play(self) -> None: ...
    def pause(self) -> None: ...
    def stop(self) -> None: ...
    def seek(self, ms: int) -> None: ...
    def get_time(self) -> int: ...
    def get_length(self) -> int: ...
    def is_playing(self) -> bool: ...


class VLCPlayer(BasePlayer):
    """基于 python-vlc 的播放器实现（需要本机安装 VLC）。"""

    def __init__(self):
        self.instance = vlc.Instance()  # type: ignore
        self.player = self.instance.media_player_new()  # type: ignore
        self._length = 0

    def set_media(self, path: Optional[Path]) -> None:
        if path is None:
            self.player.set_media(None)  # type: ignore
            self._length = 0
            return
        media = self.instance.media_new(str(path))  # type: ignore
        self.player.set_media(media)  # type: ignore
        self._length = 0

    def play(self) -> None:
        self.player.play()  # type: ignore

    def pause(self) -> None:
        self.player.pause()  # type: ignore

    def stop(self) -> None:
        self.player.stop()  # type: ignore

    def seek(self, ms: int) -> None:
        try:
            self.player.set_time(ms)  # type: ignore
        except Exception:
            pass

    def get_time(self) -> int:
        try:
            t = self.player.get_time()  # type: ignore
            return max(0, int(t)) if t is not None else 0
        except Exception:
            return 0

    def get_length(self) -> int:
        try:
            d = self.player.get_length()  # type: ignore
            if d and d > 0:
                self._length = int(d)
            return self._length
        except Exception:
            return self._length

    def is_playing(self) -> bool:
        try:
            return bool(self.player.is_playing())  # type: ignore
        except Exception:
            return False


class SimulatedPlayer(BasePlayer):
    """用于没有 VLC 的回退播放器（基于 QTimer 模拟进度，便于调试）。"""

    def __init__(self):
        self._length = 180000
        self._time = 0
        self._playing = False
        self._lock = threading.Lock()

    def set_media(self, path: Optional[Path]) -> None:
        self._time = 0
        self._length = 180000 if path is None else 180000

    def play(self) -> None:
        with self._lock:
            self._playing = True

    def pause(self) -> None:
        with self._lock:
            self._playing = False

    def stop(self) -> None:
        with self._lock:
            self._playing = False
            self._time = 0

    def seek(self, ms: int) -> None:
        with self._lock:
            self._time = max(0, min(self._length, ms))

    def get_time(self) -> int:
        with self._lock:
            return int(self._time)

    def get_length(self) -> int:
        with self._lock:
            return int(self._length)

    def is_playing(self) -> bool:
        with self._lock:
            return bool(self._playing)

    def tick(self, dt_ms: int):
        with self._lock:
            if self._playing:
                self._time += dt_ms
                if self._time >= self._length:
                    self._playing = False
                    self._time = self._length


# ---------------------------
# 主应用窗口（包含新的右下角备份按钮布局）
# ---------------------------
class LyricsEditorMainWindow(QMainWindow):
    """主窗口：组织界面、状态、控制与信号槽逻辑。"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("内嵌歌词微调 · 喵版")
        self.resize(1200, 800)

        # 状态
        self.folder: Optional[Path] = None
        self.audio_files: List[Path] = []
        self.current_path: Optional[Path] = None
        self.current_raw_text: str = ""
        self.current_source_tag: str = "none"
        self.current_lines: List[LyricLine] = []
        self.current_index: Optional[int] = None

        # 播放器后端
        if HAS_VLC:
            try:
                self.player: BasePlayer = VLCPlayer()
            except Exception:
                self.player = SimulatedPlayer()
        else:
            self.player = SimulatedPlayer()

        # UI
        self._build_ui()

        # 更新计时器
        self.update_interval_ms = 80
        self._qtimer = QTimer(self)
        self._qtimer.timeout.connect(self._ui_updater)
        self._qtimer.start(self.update_interval_ms)
        self._last_tick = time.time()

    def _build_ui(self):
        """构建主界面：左文件表、右歌词区、底部控制与右下角备份按钮（accent）。"""
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter)

        # 左侧文件表
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        splitter.addWidget(left_widget)

        self.table = QTableWidget(0, 3)
        self.table.setFixedWidth(450)
        self.table.setHorizontalHeaderLabels(["文件名", "标题", "艺术家"])
        self.table.setColumnWidth(0, 250)
        self.table.setColumnWidth(1, 110)
        self.table.setColumnWidth(2, 90)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(self.table.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(self.table.EditTrigger.NoEditTriggers)
        self.table.cellDoubleClicked.connect(self._on_table_double)
        self.table.cellClicked.connect(self._on_table_click)
        left_layout.addWidget(self.table)

        btn_row = QHBoxLayout()
        self.btn_open = QPushButton("📂 打开文件夹")
        self.btn_open.clicked.connect(self.select_folder)
        btn_row.addWidget(self.btn_open)
        self.btn_rescan = QPushButton("🔁 重新扫描")
        self.btn_rescan.clicked.connect(self.scan_folder)
        btn_row.addWidget(self.btn_rescan)
        left_layout.addLayout(btn_row)

        # 右侧主区
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        splitter.addWidget(right_widget)

        # 行列表
        self.line_list = QListWidget()
        # 关键：连接双击信号 -> 跳转到对应时间并选中该行
        self.line_list.itemDoubleClicked.connect(self._on_line_double)
        # 可选：单击也同步选中（增强体验）
        self.line_list.itemClicked.connect(lambda it: self.select_line(it.data(Qt.ItemDataRole.UserRole)))
        right_layout.addWidget(self.line_list, stretch=6)


        # 原始歌词编辑区（当无时间戳时显示）
        self.raw_edit = QTextEdit()
        self.raw_edit.setPlaceholderText("(当未检测到时间戳时原始歌词显示在此，可编辑)")
        # 兼容不同 PyQt 绑定，使用稳妥的换行方法（WidgetWidth）
        try:
            self.raw_edit.setLineWrapMode(self.raw_edit.LineWrapMode.WidgetWidth)
        except Exception:
            # 兜底（极少数绑定）
            try:
                self.raw_edit.setLineWrapMode(self.raw_edit.WidgetWidth)
            except Exception:
                pass
        self.raw_edit.setVisible(False)
        right_layout.addWidget(self.raw_edit, stretch=4)

        # 播放控制行
        control_row = QHBoxLayout()
        self.btn_play = QPushButton("▶️ 播放")
        self.btn_play.clicked.connect(self.toggle_play)
        control_row.addWidget(self.btn_play)
        self.btn_stop = QPushButton("⏹ 停止")
        self.btn_stop.clicked.connect(self.stop)
        control_row.addWidget(self.btn_stop)
        control_row.addWidget(QLabel("时间:"))
        self.lbl_time = QLabel("00:00.000 / 00:00.000")
        control_row.addWidget(self.lbl_time)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.sliderPressed.connect(self._on_slider_press)
        self.slider.sliderReleased.connect(self._on_slider_release)
        control_row.addWidget(self.slider, stretch=2)
        right_layout.addLayout(control_row)

        # 微调按钮组（左侧）
        micro_widget = QWidget()
        micro_layout = QHBoxLayout(micro_widget)
        micro_layout.setContentsMargins(0, 0, 0, 0)
        self.btn_minus = QPushButton("-100ms")
        self.btn_minus.clicked.connect(lambda: self.shift_selected(-100))
        micro_layout.addWidget(self.btn_minus)
        self.btn_plus = QPushButton("+100ms")
        self.btn_plus.clicked.connect(lambda: self.shift_selected(100))
        micro_layout.addWidget(self.btn_plus)
        self.btn_global_minus = QPushButton("整体 -500ms")
        self.btn_global_minus.clicked.connect(lambda: self.shift_global(-500))
        micro_layout.addWidget(self.btn_global_minus)
        self.btn_global_plus = QPushButton("整体 +500ms")
        self.btn_global_plus.clicked.connect(lambda: self.shift_global(500))
        micro_layout.addWidget(self.btn_global_plus)

        # 底部区域：左为微调组，右为强调色写回按钮（放在最右下角）
        bottom_row = QHBoxLayout()
        bottom_row.addWidget(micro_widget, stretch=1)

        # 右侧容器用于把按钮挤到最右下角
        right_bottom_widget = QWidget()
        right_bottom_layout = QHBoxLayout(right_bottom_widget)
        right_bottom_layout.setContentsMargins(0, 0, 0, 0)
        right_bottom_layout.addStretch(1)

        # 强调色按钮（写入并备份），放在右下角
        self.btn_save = QPushButton("💾 写回并备份")
        self.btn_save.clicked.connect(self.save_back)
        self.btn_save.setToolTip("把修改写回音频文件并备份原文件到 .bf/ 文件夹")
        # 使用强调色样式（可根据你偏好的主色调整）
        self.btn_save.setStyleSheet(
            """
            QPushButton {
                background-color: #4f6ef7;
                color: white;
                border-radius: 6px;
                padding: 8px 14px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #3e57d1;
            }
            QPushButton:pressed {
                background-color: #3346b0;
            }
            """
        )
        # 把按钮加到右侧布局并右对齐
        right_bottom_layout.addWidget(self.btn_save, 0, Qt.AlignmentFlag.AlignRight)

        # 将右侧容器加入底部行
        bottom_row.addWidget(right_bottom_widget, stretch=0)

        # 添加底部行到右侧主布局（确保靠底部显示）
        right_layout.addLayout(bottom_row)

        # 状态栏
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("请选择文件夹并扫描 ♪")

        # splitter stretch 调整
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)

    # -----------------------
    # 主题切换回调
    # -----------------------
    def _on_theme_change(self, idx: int):
        try:
            if idx == 1 and HAS_QDARK:
                qss = qdarkstyle.load_stylesheet_pyqt6()
                self.setStyleSheet(qss)
                self.status.showMessage("已切换到 暗色 主题")
            else:
                self.setStyleSheet("")
                self.status.showMessage("已切换到 浅色 主题")
        except Exception as e:
            QMessageBox.warning(self, "主题切换失败", f"无法切换主题：{e}")

    # -----------------------
    # 扫描 / 加载文件
    # -----------------------
    def select_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择包含音频的文件夹", str(Path.home()))
        if not folder:
            return
        if self.folder and Path(folder) == self.folder:
            return
        self.folder = Path(folder)
        self.status.showMessage(f"已选择：{self.folder}")
        self.scan_folder()

    def scan_folder(self):
        if not self.folder:
            QMessageBox.information(self, "提示", "请先选择文件夹")
            return
        self.audio_files = []
        self.table.setRowCount(0)
        supported = (".mp3", ".flac", ".m4a", ".mp4")
        row_idx = 0
        for p in sorted(self.folder.iterdir()):
            if p.suffix.lower() in supported and p.is_file():
                self.audio_files.append(p)
                title, artist = read_basic_metadata(p)
                text, src = read_embedded_lyrics(p)
                self.table.insertRow(row_idx)
                self.table.setItem(row_idx, 0, QTableWidgetItem(p.name))
                self.table.setItem(row_idx, 1, QTableWidgetItem(title or ""))
                self.table.setItem(row_idx, 2, QTableWidgetItem(artist or ""))
                for col in range(3):
                    item = self.table.item(row_idx, col)
                    if item:
                        item.setData(Qt.ItemDataRole.UserRole, str(p))
                row_idx += 1
        self.status.showMessage(f"扫描完成：{len(self.audio_files)} 个文件")

    def _on_table_click(self, row: int, col: int):
        item = self.table.item(row, 0)
        if not item:
            return
        path_str = item.data(Qt.ItemDataRole.UserRole)
        if not path_str:
            return
        self.load_file(Path(path_str))

    def _on_table_double(self, row: int, col: int):
        item = self.table.item(row, 0)
        if not item:
            return
        path_str = item.data(Qt.ItemDataRole.UserRole)
        if not path_str:
            return
        path = Path(path_str)
        self.load_file(path)
        self._play_when_ready(timeout_ms=2000, check_interval_ms=40)

    # -----------------------
    # 加载歌词与渲染
    # -----------------------
    def load_file(self, path: Path):
        self.stop()
        self.current_path = path
        title, artist = read_basic_metadata(path)
        raw_text, src = read_embedded_lyrics(path)
        self.current_raw_text = raw_text or ""
        self.current_source_tag = src
        self.current_lines = parse_lrc_text(self.current_raw_text)
        self.current_index = None
        self._render_lyrics()
        self.status.showMessage(f"加载: {path.name}  标题: {title or '-'}  艺术家: {artist or '-'}  来源: {src}  行数: {len(self.current_lines)}")
        try:
            self.player.set_media(path)
        except Exception:
            pass

    def _render_lyrics(self):
        self.line_list.clear()
        if self.current_lines:
            self.raw_edit.setVisible(False)
            for idx, ln in enumerate(self.current_lines):
                item = QListWidgetItem(ln.to_lrc_tag())
                item.setData(Qt.ItemDataRole.UserRole, idx)
                self.line_list.addItem(item)
        else:
            self.raw_edit.setVisible(True)
            self.raw_edit.setPlainText(self.current_raw_text or "(没有嵌入的歌词)")

    # -----------------------
    # 行双击跳转与选择
    # -----------------------
    def _on_line_double(self, item: QListWidgetItem):
        idx = item.data(Qt.ItemDataRole.UserRole)
        if idx is None:
            return
        self.select_line(idx)
        try:
            self.player.seek(self.current_lines[idx].timestamp_ms)
        except Exception:
            pass

    def select_line(self, idx: int):
        self.current_index = idx
        self.line_list.setCurrentRow(idx)

    # -----------------------
    # 播放控制
    # -----------------------
    def toggle_play(self):
        if self.player.is_playing():
            self.player.pause()
            self.btn_play.setText("▶️ 播放")
        else:
            self.player.play()
            self.btn_play.setText("⏸ 暂停")

    def stop(self):
        self.player.stop()
        self.btn_play.setText("▶️ 播放")
        self.current_index = None
        self.line_list.clearSelection()
        self.lbl_time.setText("00:00.000 / 00:00.000")
        self.slider.setValue(0)

    def _play_when_ready(self, timeout_ms: int = 2000, check_interval_ms: int = 40):
        attempts_left = max(1, timeout_ms // max(1, check_interval_ms))

        def _try():
            nonlocal attempts_left
            try:
                length = int(self.player.get_length())
            except Exception:
                length = 0
            if length and length > 100:
                try:
                    self.player.play()
                    self.btn_play.setText("⏸ 暂停")
                    self.status.showMessage(f"已开始播放（时长 {length} ms）")
                except Exception:
                    self.status.showMessage("播放失败（调用 player.play() 时出错）")
                return
            attempts_left -= 1
            if attempts_left <= 0:
                try:
                    self.player.play()
                    self.btn_play.setText("⏸ 暂停")
                except Exception as e:
                    self.status.showMessage(f"播放失败（原因：{e}）")
                return
            QTimer.singleShot(check_interval_ms, _try)

        QTimer.singleShot(0, _try)

    # -----------------------
    # 定时器更新：进度与高亮
    # -----------------------
    def _ui_updater(self):
        if isinstance(self.player, SimulatedPlayer):
            now = time.time()
            dt = int((now - self._last_tick) * 1000)
            self._last_tick = now
            if dt > 0:
                self.player.tick(dt)

        t = max(0, int(self.player.get_time()))
        total = max(1, int(self.player.get_length()))

        def fmt(ms: int) -> str:
            s = ms // 1000
            m = s // 60
            s = s % 60
            rem = ms % 1000
            return f"{m:02d}:{s:02d}.{rem:03d}"

        self.lbl_time.setText(f"{fmt(t)} / {fmt(total)}")
        pos = int((t / total) * 1000.0) if total > 0 else 0
        if not getattr(self, "_seeking_by_user", False):
            self.slider.setValue(pos)

        if self.current_lines:
            idx = self._find_current_line_index(t)
            if idx is None:
                self.line_list.clearSelection()
                self.current_index = None
            else:
                if idx != self.current_index:
                    self.select_line(idx)

    def _find_current_line_index(self, t_ms: int) -> Optional[int]:
        if not self.current_lines:
            return None
        if t_ms < self.current_lines[0].timestamp_ms:
            return None
        lo, hi = 0, len(self.current_lines) - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            if self.current_lines[mid].timestamp_ms <= t_ms:
                lo = mid + 1
            else:
                hi = mid - 1
        return hi if hi >= 0 else None

    # -----------------------
    # slider 交互
    # -----------------------
    def _on_slider_press(self):
        self._seeking_by_user = True

    def _on_slider_release(self):
        try:
            total = max(1, int(self.player.get_length()))
            val = int(self.slider.value())
            pos = int((val / 1000.0) * total)
            self.player.seek(pos)
        except Exception:
            pass
        finally:
            self._seeking_by_user = False

    # -----------------------
    # 微调（行 / 全局）
    # -----------------------
    def shift_selected(self, delta_ms: int):
        if not self.current_path:
            QMessageBox.information(self, "提示", "请先选择并加载文件")
            return
        if not self.current_lines:
            QMessageBox.information(self, "提示", "未解析到带时间戳的歌词，无法按行微调")
            return
        if self.current_index is None:
            QMessageBox.information(self, "提示", "请先点击要微调的行")
            return
        idx = self.current_index
        target_line = self.current_lines[idx]
        target_line.timestamp_ms = max(0, target_line.timestamp_ms + delta_ms)
        self.current_lines.sort(key=lambda x: x.timestamp_ms)
        self._render_lyrics()
        try:
            new_idx = self.current_lines.index(target_line)
            self.select_line(new_idx)
        except ValueError:
            self.current_index = None
        self.status.showMessage(f"已将原行 {idx+1} 偏移 {delta_ms} ms")

    def shift_global(self, delta_ms: int):
        if not self.current_lines:
            QMessageBox.information(self, "提示", "未解析到带时间戳的歌词，无法整体偏移")
            return
        for ln in self.current_lines:
            ln.timestamp_ms = max(0, ln.timestamp_ms + delta_ms)
        self.current_lines.sort(key=lambda x: x.timestamp_ms)
        self._render_lyrics()
        self.current_index = None
        self.status.showMessage(f"已对整首歌曲应用整体偏移 {delta_ms} ms")

    # -----------------------
    # 写回嵌入标签（含备份） - 由右下角强调按钮触发
    # -----------------------
    def save_back(self):
        if not self.current_path:
            QMessageBox.information(self, "提示", "请先选择并加载文件")
            return
        if not self.current_source_tag or self.current_source_tag == "none":
            QMessageBox.information(self, "提示", "未检测到嵌入歌词标签，写回操作取消")
            return
        if self.current_lines:
            new_text = lrc_lines_to_text(self.current_lines)
        else:
            new_text = self.raw_edit.toPlainText()
        try:
            write_embedded_lyrics(self.current_path, new_text, self.current_source_tag, make_backup=True)
            QMessageBox.information(self, "完成", f"已写回：{self.current_path.name}\n备份保存至：{self.current_path.parent / '.bf' / (self.current_path.name + '.bak')}")
        except Exception as e:
            QMessageBox.critical(self, "写回失败", f"写回文件失败：{e}")

    # -----------------------
    # 退出清理
    # -----------------------
    def closeEvent(self, event):
        try:
            self._qtimer.stop()
        except Exception:
            pass
        try:
            self.player.stop()
        except Exception:
            pass
        super().closeEvent(event)


# ---------------------------
# 运行入口
# ---------------------------
def main():
    app = QApplication(sys.argv)
    # 如果希望启动时默认暗色，可解注释下一行（需安装 qdarkstyle）
    # if HAS_QDARK:
    #     app.setStyleSheet(qdarkstyle.load_stylesheet_pyqt6())

    win = LyricsEditorMainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
