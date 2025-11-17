#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
音视频响度归一化工具
使用PyQt6作为UI，FFmpeg进行响度归一化处理
保留所有元数据，仅调整响度
"""

import sys
import os
import subprocess
from pathlib import Path
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QFileDialog, QMessageBox, QProgressBar, QListWidget,
    QTextEdit, QGroupBox, QCheckBox, QListWidgetItem
)
from PyQt6.QtCore import Qt, pyqtSignal, QThread
from PyQt6.QtGui import QIcon

# ============================================================
# 工具函数区域
# ============================================================

def get_media_info(file_path: str) -> tuple[float, bool]:
    """
    获取媒体文件信息（总时长、是否存在音频流）
    @param file_path: 文件路径
    @return (duration, has_audio)
    """
    try:
        # 调用 ffprobe 获取时长和音频流信息
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-select_streams", "a",
            "-show_entries", "stream=index",
            "-of", "default=noprint_wrappers=1:nokey=1",
            file_path
        ]
        process = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace"
        )

        output = process.stdout.strip().splitlines()
        has_audio = len(output) > 1  # 第一行通常为时长，后面行存在即说明有音频流
        duration = 0.0
        try:
            duration = float(output[0])
        except Exception:
            pass
        return duration, has_audio
    except Exception:
        return 0.0, False


# ============================================================
# FFmpeg 处理线程
# ============================================================

class LoudNormWorker(QThread):
    """FFmpeg响度归一化处理线程"""
    finished = pyqtSignal(str)
    error = pyqtSignal(str)
    progress = pyqtSignal(int)
    log = pyqtSignal(str)
    skipped = pyqtSignal(str)

    def __init__(self, input_file: str, output_file: str):
        super().__init__()
        self.input_file = input_file
        self.output_file = output_file

    def run(self):
        try:
            # Step 1: 检查音频流
            duration, has_audio = get_media_info(self.input_file)
            if not has_audio:
                self.skipped.emit(f"⚠️ 跳过（无音频流）: {os.path.basename(self.input_file)}")
                return

            # Step 2: 构建 FFmpeg 命令
            cmd = [
                "ffmpeg",
                "-i", self.input_file,
                "-filter_complex",
                "[0:a]loudnorm=I=-23:TP=-2:LRA=11:print_format=summary[a]",
                "-map", "[a]",
                "-map", "0:v?",
                "-map", "0:s?",
                "-c:a", "aac",
                "-c:v", "copy",
                "-c:s", "copy",
                "-y",
                self.output_file
            ]

            # Step 3: 启动子进程（隐藏控制台）
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0
            creationflags = subprocess.CREATE_NO_WINDOW

            self.log.emit(f"▶️ 开始处理: {os.path.basename(self.input_file)}")

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                encoding="utf-8",
                errors="replace",
                startupinfo=startupinfo,
                creationflags=creationflags,
            )

            # Step 4: 解析进度输出
            for line in process.stdout:
                line = line.strip()

                # 过滤非关键信息
                if not any(k in line for k in ("time=", "loudnorm", "size=", "speed=")):
                    continue

                if "time=" in line:
                    try:
                        time_str = line.split("time=")[1].split()[0]
                        h, m, s = map(float, time_str.split(":"))
                        current_sec = h * 3600 + m * 60 + s
                        if duration > 0:
                            percent = min(int((current_sec / duration) * 100), 100)
                            self.progress.emit(percent)
                    except Exception:
                        pass

            process.wait()

            # Step 5: 检查结果
            if process.returncode == 0 and os.path.getsize(self.output_file) > 0:
                self.finished.emit(f"✅ 处理完成: {os.path.basename(self.input_file)}")
            else:
                # 若生成了空文件则删除
                if os.path.exists(self.output_file) and os.path.getsize(self.output_file) == 0:
                    os.remove(self.output_file)
                self.error.emit(f"❌ 处理失败或无音频输出: {os.path.basename(self.input_file)}")

        except FileNotFoundError:
            self.error.emit("未找到 FFmpeg，请确保已安装并添加到系统 PATH。")
        except Exception as e:
            self.error.emit(f"❌ 出现异常: {str(e)}")


# ============================================================
# 主界面类
# ============================================================

class LoudNormApp(QMainWindow):
    """主窗口：响度归一化工具"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("音视频响度归一化工具")
        self.setGeometry(100, 100, 900, 700)
        self.init_ui()

        self.process_queue = []
        self.processed_files = set()
        self.current_process_index = 0

    def init_ui(self):
        """初始化 UI 布局"""
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QVBoxLayout(main_widget)

        # 标题
        title_label = QLabel("音视频响度归一化工具")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label.setStyleSheet("font-size: 18px; font-weight: bold; margin: 11px;")
        main_layout.addWidget(title_label)

        # 按钮区
        btn_layout = QHBoxLayout()
        self.add_files_btn = QPushButton("添加文件")
        self.add_files_btn.clicked.connect(self.add_files)
        self.add_folders_btn = QPushButton("添加文件夹")
        self.add_folders_btn.clicked.connect(self.add_folders)
        self.clear_btn = QPushButton("清空队列")
        self.clear_btn.clicked.connect(self.clear_queue)

        for btn, color in [(self.add_files_btn, "#2196F3"), (self.add_folders_btn, "#2196F3"), (self.clear_btn, "#f44336")]:
            btn.setStyleSheet(f"padding:8px; color:white; background-color:{color}")

        btn_layout.addWidget(self.add_files_btn)
        btn_layout.addWidget(self.add_folders_btn)
        btn_layout.addWidget(self.clear_btn)
        main_layout.addLayout(btn_layout)

        # 扫描选项
        self.recursive_check = QCheckBox("递归扫描文件夹")
        self.recursive_check.setChecked(True)
        main_layout.addWidget(self.recursive_check)

        # 文件队列
        queue_group = QGroupBox("待处理文件队列")
        queue_layout = QVBoxLayout()
        self.queue_list = QListWidget()
        queue_layout.addWidget(self.queue_list)
        queue_group.setLayout(queue_layout)
        main_layout.addWidget(queue_group)

        # 进度 & 按钮
        progress_layout = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        progress_layout.addWidget(self.progress_bar)

        self.process_btn = QPushButton("开始处理队列")
        self.process_btn.setStyleSheet("font-size:16px; padding:10px; background-color:#4CAF50; color:white;")
        self.process_btn.setEnabled(False)
        self.process_btn.clicked.connect(self.start_processing_queue)
        progress_layout.addWidget(self.process_btn)
        main_layout.addLayout(progress_layout)

        # 日志输出
        log_group = QGroupBox("处理日志")
        log_layout = QVBoxLayout()
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setStyleSheet("font-family: Consolas; font-size: 12px;")
        log_layout.addWidget(self.log_text)
        log_group.setLayout(log_layout)
        main_layout.addWidget(log_group)

        # 状态栏
        self.status_label = QLabel("就绪 - 请添加文件或文件夹")
        main_layout.addWidget(self.status_label)

    # ========== 文件管理 ==========
    def add_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "选择音视频文件", "",
            "音视频文件 (*.mp4 *.mkv *.mp3 *.flac *.aac *.m4a *.wav);;所有文件 (*)")
        for f in files:
            self.add_file_to_queue(f)

    def add_folders(self):
        folder = QFileDialog.getExistingDirectory(self, "选择文件夹", "")
        if not folder: return
        exts = {'.mp4', '.avi', '.mkv', '.mov', '.flv', '.wmv', '.mp3', '.wav', '.flac', '.aac', '.m4a', '.ogg'}
        paths = Path(folder).rglob("*") if self.recursive_check.isChecked() else Path(folder).iterdir()
        for p in paths:
            if p.is_file() and p.suffix.lower() in exts:
                self.add_file_to_queue(str(p))

    def add_file_to_queue(self, path: str):
        if path in self.processed_files: return
        self.processed_files.add(path)
        self.process_queue.append(path)
        item = QListWidgetItem(os.path.basename(path))
        item.setToolTip(path)
        self.queue_list.addItem(item)
        self.status_label.setText(f"队列中有 {len(self.process_queue)} 个文件待处理")
        self.process_btn.setEnabled(True)

    def clear_queue(self):
        self.process_queue.clear()
        self.processed_files.clear()
        self.queue_list.clear()
        self.status_label.setText("队列已清空")
        self.progress_bar.setValue(0)
        self.process_btn.setEnabled(False)

    # ========== 处理逻辑 ==========
    def start_processing_queue(self):
        if not self.process_queue: return
        self.current_process_index = 0
        self.progress_bar.setValue(0)
        self.log_text.clear()
        self.process_btn.setEnabled(False)
        self.process_next_file()

    def process_next_file(self):
        if self.current_process_index >= len(self.process_queue):
            self.status_label.setText("🎉 全部处理完成！")
            self.process_btn.setEnabled(True)
            self.progress_bar.setValue(100)
            return

        input_file = self.process_queue[self.current_process_index]
        output_file = f"{os.path.splitext(input_file)[0]}_loudnorm{os.path.splitext(input_file)[1]}"

        self.worker = LoudNormWorker(input_file, output_file)
        self.worker.log.connect(self.on_log)
        self.worker.progress.connect(self.progress_bar.setValue)
        self.worker.finished.connect(self.on_file_finished)
        self.worker.error.connect(self.on_file_error)
        self.worker.skipped.connect(self.on_file_skipped)
        self.worker.start()

    def on_file_finished(self, msg):
        self.log_text.append(msg)
        self.replace_original()
        self.next()

    def on_file_error(self, msg):
        self.log_text.append(msg)
        self.next()

    def on_file_skipped(self, msg):
        self.log_text.append(msg)
        self.next()

    def on_log(self, msg):
        self.log_text.append(msg)
        self.log_text.verticalScrollBar().setValue(self.log_text.verticalScrollBar().maximum())

    def replace_original(self):
        input_file = self.process_queue[self.current_process_index]
        output_file = f"{os.path.splitext(input_file)[0]}_loudnorm{os.path.splitext(input_file)[1]}"
        if os.path.exists(output_file):
            import shutil
            shutil.move(output_file, input_file)

    def next(self):
        self.current_process_index += 1
        progress = int((self.current_process_index / len(self.process_queue)) * 100)
        self.progress_bar.setValue(progress)
        self.process_next_file()


# ============================================================
# 主程序入口
# ============================================================

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("音视频响度归一化工具")
    app.setOrganizationName("Cline")
    window = LoudNormApp()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
