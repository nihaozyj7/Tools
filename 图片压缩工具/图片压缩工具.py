import os
import sys
import subprocess
import hashlib
from typing import List

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QPushButton, QListWidget, QLabel,
                             QFileDialog, QMessageBox, QSpinBox, QProgressBar,
                             QGroupBox, QSplitter, QLineEdit)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont


class CompressionWorker(QThread):
    """压缩工作线程"""
    progress_updated = pyqtSignal(int, str, str)
    finished = pyqtSignal()
    error_occurred = pyqtSignal(str)

    def __init__(self, image_paths: List[str], target_size_kb: int, output_dir: str):
        super().__init__()
        self.image_paths = image_paths
        self.target_size_kb = target_size_kb
        self.output_dir = output_dir
        self.is_running = True

    def run(self):
        """执行压缩任务"""
        total = len(self.image_paths)

        # 确保输出目录存在
        os.makedirs(self.output_dir, exist_ok=True)

        for i, image_path in enumerate(self.image_paths):
            if not self.is_running:
                break

            try:
                filename = os.path.basename(image_path)
                self.progress_updated.emit(i + 1, f"正在处理: {filename}", filename)
                output_path = self.compress_image(image_path)

                # 获取原始大小和压缩后大小
                original_size = os.path.getsize(image_path) / 1024
                compressed_size = os.path.getsize(output_path) / 1024
                compression_ratio = (1 - compressed_size / original_size) * 100

                self.progress_updated.emit(
                    i + 1,
                    f"完成: {filename} ({compressed_size:.1f}KB, 压缩率: {compression_ratio:.1f}%)",
                    filename
                )

            except Exception as e:
                self.error_occurred.emit(f"处理 {os.path.basename(image_path)} 时出错: {str(e)}")

        self.finished.emit()

    def compress_image(self, image_path: str) -> str:
        """压缩单张图片，以最高画质在目标大小范围内"""
        filename = os.path.basename(image_path)
        name, ext = os.path.splitext(filename)
        output_filename = f"{name}_compressed{ext}"
        output_path = os.path.join(self.output_dir, output_filename)

        # 从最高画质开始
        quality = 2
        while quality <= 31:
            cmd = [
                'ffmpeg',
                '-i', image_path,
                '-q:v', str(quality),
                '-y',
                output_path
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise Exception(f"FFmpeg执行失败: {result.stderr}")

            # 检查大小
            current_size = os.path.getsize(output_path) / 1024  # KB
            if current_size <= self.target_size_kb:
                # 已经符合目标大小，直接返回
                return output_path

            # 如果还超出目标大小，降低画质（增大 q:v）
            quality += 1

        # 如果到最后都无法压到目标大小，那就返回最后一次结果
        return output_path


    def adjust_to_target_size(self, output_path: str, original_path: str) -> str:
        """调整图片到目标大小"""
        current_size = os.path.getsize(output_path) / 1024  # KB

        if current_size <= self.target_size_kb:
            return output_path

        # 如果文件仍然太大，尝试不同的压缩策略
        filename = os.path.basename(output_path)
        name, ext = os.path.splitext(filename)

        # 尝试不同的质量设置
        for quality in [10, 15, 20, 25, 30]:
            if not self.is_running:
                break

            temp_path = os.path.join(self.output_dir, f"temp_{name}{ext}")
            cmd = [
                'ffmpeg',
                '-i', original_path,
                '-q:v', str(quality),
                '-y',
                temp_path
            ]

            subprocess.run(cmd, capture_output=True)

            temp_size = os.path.getsize(temp_path) / 1024
            if temp_size <= self.target_size_kb or quality == 30:
                os.replace(temp_path, output_path)
                break
            else:
                os.remove(temp_path)

        return output_path

    def stop(self):
        """停止压缩"""
        self.is_running = False


class ImageCompressor(QMainWindow):
    """图片压缩工具主窗口"""
    def __init__(self):
        super().__init__()
        self.image_paths = []
        self.file_hashes = set()
        self.worker = None
        self.init_ui()

    def init_ui(self):
        """初始化用户界面"""
        self.setWindowTitle('图片压缩工具')
        self.setGeometry(100, 100, 900, 700)

        # ====== 设置全局字体 ======
        font = QFont()
        font.setPointSize(14)  # 全局字体大小
        self.setFont(font)

        # 中央部件
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        # 主布局
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(12)
        main_layout.setContentsMargins(20, 20, 20, 20)

        # 创建分割器
        splitter = QSplitter(Qt.Orientation.Vertical)

        # 上部区域 - 文件操作
        top_widget = QWidget()
        top_layout = QVBoxLayout(top_widget)

        # 文件操作组
        file_group = QGroupBox("图片文件")
        file_layout = QVBoxLayout(file_group)

        # 按钮布局
        button_layout = QHBoxLayout()
        button_layout.setSpacing(10)

        # 添加图片按钮
        self.add_button = QPushButton('添加图片')
        self.add_button.setMinimumHeight(40)  # 设置高度
        self.add_button.clicked.connect(self.add_images)
        button_layout.addWidget(self.add_button)

        # 移除选中
        self.remove_button = QPushButton('移除选中')
        self.remove_button.setMinimumHeight(40)
        self.remove_button.clicked.connect(self.remove_selected)
        button_layout.addWidget(self.remove_button)

        # 清空列表
        self.clear_button = QPushButton('清空列表')
        self.clear_button.setMinimumHeight(40)
        self.clear_button.clicked.connect(self.clear_list)
        button_layout.addWidget(self.clear_button)

        button_layout.addStretch()
        file_layout.addLayout(button_layout)

        # 图片列表
        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.list_widget.setMinimumHeight(200)
        file_layout.addWidget(self.list_widget)

        top_layout.addWidget(file_group)

        # 输出目录设置
        output_group = QGroupBox("输出设置")
        output_layout = QVBoxLayout(output_group)

        # 输出目录选择
        dir_layout = QHBoxLayout()
        dir_label = QLabel('输出目录:')
        dir_label.setMinimumHeight(35)
        dir_layout.addWidget(dir_label)

        self.output_path_edit = QLineEdit()
        self.output_path_edit.setReadOnly(True)
        self.output_path_edit.setMinimumHeight(35)
        dir_layout.addWidget(self.output_path_edit, 1)

        self.output_button = QPushButton('浏览...')
        self.output_button.setMinimumHeight(40)
        self.output_button.clicked.connect(self.select_output_directory)
        dir_layout.addWidget(self.output_button)

        output_layout.addLayout(dir_layout)

        # 目标大小设置
        size_layout = QHBoxLayout()
        size_label = QLabel('目标大小:')
        size_label.setMinimumHeight(35)
        size_layout.addWidget(size_label)

        self.size_spinbox = QSpinBox()
        self.size_spinbox.setRange(10, 10240)
        self.size_spinbox.setValue(500)
        self.size_spinbox.setSuffix(" KB")
        self.size_spinbox.setMinimumHeight(35)
        size_layout.addWidget(self.size_spinbox)

        size_layout.addStretch()
        output_layout.addLayout(size_layout)

        top_layout.addWidget(output_group)
        top_layout.setStretch(0, 3)
        top_layout.setStretch(1, 1)

        # 下部区域 - 进度和操作
        bottom_widget = QWidget()
        bottom_layout = QVBoxLayout(bottom_widget)

        # 进度信息
        progress_group = QGroupBox("进度信息")
        progress_layout = QVBoxLayout(progress_group)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setMinimumHeight(35)  # 进度条更粗
        progress_layout.addWidget(self.progress_bar)

        self.status_label = QLabel('准备就绪 - 请添加图片并设置输出目录')
        self.status_label.setWordWrap(True)
        self.status_label.setMinimumHeight(40)
        status_font = QFont()
        status_font.setPointSize(14)
        self.status_label.setFont(status_font)
        progress_layout.addWidget(self.status_label)

        bottom_layout.addWidget(progress_group)

        # 压缩按钮
        self.compress_button = QPushButton('开始压缩')
        self.compress_button.setMinimumHeight(45)
        self.compress_button.clicked.connect(self.start_compression)
        bottom_layout.addWidget(self.compress_button)

        # 添加到分割器
        splitter.addWidget(top_widget)
        splitter.addWidget(bottom_widget)
        splitter.setSizes([500, 200])

        main_layout.addWidget(splitter)

        # 设置默认输出目录为桌面
        desktop_path = os.path.join(os.path.expanduser("~"), "Desktop")
        self.output_directory = desktop_path
        self.output_path_edit.setText(desktop_path)


    def add_images(self):
        """添加图片到列表"""
        file_dialog = QFileDialog()
        file_paths, _ = file_dialog.getOpenFileNames(
            self,
            '选择图片',
            '',
            '图片文件 (*.png *.jpg *.jpeg *.bmp *.tiff *.webp *.gif);;所有文件 (*)'
        )

        if not file_paths:
            return

        # 过滤重复文件
        new_paths = self.filter_duplicates(file_paths)

        # 添加到列表
        added_count = 0
        for path in new_paths:
            try:
                # 检查文件是否可读
                with open(path, 'rb'):
                    pass

                self.image_paths.append(path)
                file_hash = self.calculate_file_hash(path)
                self.file_hashes.add(file_hash)

                # 显示文件名和大小
                size_kb = os.path.getsize(path) / 1024
                display_text = f"{os.path.basename(path)} ({size_kb:.1f}KB)"
                self.list_widget.addItem(display_text)
                added_count += 1

            except IOError:
                self.update_status(f"无法读取文件: {os.path.basename(path)}")

        self.update_status(f"已添加 {added_count} 张图片，过滤了 {len(file_paths) - added_count} 张重复或无效图片")

    def filter_duplicates(self, file_paths: List[str]) -> List[str]:
        """过滤重复的图片文件"""
        new_paths = []

        for path in file_paths:
            file_hash = self.calculate_file_hash(path)
            if file_hash not in self.file_hashes:
                new_paths.append(path)

        return new_paths

    def calculate_file_hash(self, file_path: str) -> str:
        """计算文件的MD5哈希值"""
        hash_md5 = hashlib.md5()
        try:
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    hash_md5.update(chunk)
            return hash_md5.hexdigest()
        except IOError:
            return ""

    def select_output_directory(self):
        """选择输出目录"""
        directory = QFileDialog.getExistingDirectory(
            self,
            "选择输出目录",
            self.output_directory
        )

        if directory:
            self.output_directory = directory
            self.output_path_edit.setText(directory)
            self.update_status(f"输出目录已设置为: {directory}")

    def remove_selected(self):
        """移除选中的图片"""
        selected_items = self.list_widget.selectedItems()
        if not selected_items:
            QMessageBox.information(self, "提示", "请先选择要移除的图片")
            return

        # 从后往前删除
        for item in reversed(selected_items):
            row = self.list_widget.row(item)
            self.list_widget.takeItem(row)
            removed_path = self.image_paths.pop(row)

            # 从哈希集中移除
            file_hash = self.calculate_file_hash(removed_path)
            if file_hash in self.file_hashes:
                self.file_hashes.remove(file_hash)

        self.update_status(f"已移除 {len(selected_items)} 张图片")

    def clear_list(self):
        """清空图片列表"""
        if not self.image_paths:
            return

        reply = QMessageBox.question(
            self,
            "确认清空",
            "确定要清空所有图片吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            self.list_widget.clear()
            self.image_paths.clear()
            self.file_hashes.clear()
            self.update_status("已清空图片列表")

    def start_compression(self):
        """开始压缩图片"""
        if not self.image_paths:
            QMessageBox.warning(self, "警告", "请先添加图片")
            return

        # 检查输出目录
        if not hasattr(self, 'output_directory') or not self.output_directory:
            QMessageBox.warning(self, "警告", "请先设置输出目录")
            return

        # 检查输出目录是否可写
        if not os.access(self.output_directory, os.W_OK):
            QMessageBox.warning(self, "警告", "输出目录不可写，请选择其他目录")
            return

        # 禁用UI控件
        self.set_ui_enabled(False)

        # 创建并启动工作线程
        self.worker = CompressionWorker(
            self.image_paths,
            self.size_spinbox.value(),
            self.output_directory
        )
        self.worker.progress_updated.connect(self.update_progress)
        self.worker.finished.connect(self.compression_finished)
        self.worker.error_occurred.connect(self.handle_error)
        self.worker.start()

    def update_progress(self, current: int, message: str, filename: str):
        """更新进度"""
        total = len(self.image_paths)
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)
        self.update_status(message)

    def compression_finished(self):
        """压缩完成"""
        self.set_ui_enabled(True)
        self.progress_bar.setVisible(False)
        self.update_status("压缩完成! 文件已保存到输出目录")

        # 询问是否打开输出目录
        reply = QMessageBox.question(
            self,
            "完成",
            "所有图片已压缩完成。是否打开输出目录？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            try:
                if sys.platform == "win32":
                    os.startfile(self.output_directory)
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", self.output_directory])
                else:
                    subprocess.Popen(["xdg-open", self.output_directory])
            except Exception as e:
                self.update_status(f"无法打开目录: {str(e)}")

    def handle_error(self, error_message: str):
        """处理错误"""
        self.update_status(f"错误: {error_message}")
        QMessageBox.critical(self, "错误", error_message)

    def set_ui_enabled(self, enabled: bool):
        """设置UI控件启用状态"""
        self.add_button.setEnabled(enabled)
        self.remove_button.setEnabled(enabled)
        self.clear_button.setEnabled(enabled)
        self.compress_button.setEnabled(enabled)
        self.size_spinbox.setEnabled(enabled)
        self.output_button.setEnabled(enabled)
        self.progress_bar.setVisible(not enabled)

        if not enabled:
            self.progress_bar.setValue(0)

    def update_status(self, message: str):
        """更新状态标签"""
        self.status_label.setText(message)

    def closeEvent(self, event):
        """窗口关闭事件"""
        if self.worker and self.worker.isRunning():
            reply = QMessageBox.question(
                self,
                "确认退出",
                "压缩任务正在进行中，确定要退出吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )

            if reply == QMessageBox.StandardButton.Yes:
                self.worker.stop()
                self.worker.wait()
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()


def main():
    """主函数"""
    app = QApplication(sys.argv)

    # 检查FFmpeg是否可用
    try:
        subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        QMessageBox.critical(
            None,
            "错误",
            "未找到FFmpeg。请确保已安装FFmpeg并添加到系统PATH中。"
        )
        return 1

    window = ImageCompressor()
    window.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
