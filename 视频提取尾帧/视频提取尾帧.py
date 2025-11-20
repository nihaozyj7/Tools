import sys
import os
import cv2
from PyQt6.QtWidgets import (
    QApplication,
    QWidget,
    QLabel,
    QPushButton,
    QFileDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
)


class VideoFrameExtractor(QWidget):
    def __init__(self):
        super().__init__()
        self.initUI()

    def initUI(self):
        # 主布局
        layout = QVBoxLayout()

        # 文件选择部分
        file_layout = QHBoxLayout()
        self.file_label = QLabel("请选择MP4文件:")
        self.file_path_edit = QLineEdit()
        self.browse_button = QPushButton("浏览")
        self.browse_button.clicked.connect(self.select_file)
        file_layout.addWidget(self.file_label)
        file_layout.addWidget(self.file_path_edit)
        file_layout.addWidget(self.browse_button)

        # 输出路径选择部分
        output_layout = QHBoxLayout()
        self.output_label = QLabel("输出目录(可选):")
        self.output_path_edit = QLineEdit()
        self.output_browse_button = QPushButton("浏览")
        self.output_browse_button.clicked.connect(self.select_output_directory)
        output_layout.addWidget(self.output_label)
        output_layout.addWidget(self.output_path_edit)
        output_layout.addWidget(self.output_browse_button)

        # 操作按钮
        button_layout = QHBoxLayout()
        self.extract_button = QPushButton("提取尾帧")
        self.extract_button.clicked.connect(self.extract_frame)
        self.exit_button = QPushButton("退出")
        self.exit_button.clicked.connect(QApplication.instance().quit)
        button_layout.addWidget(self.extract_button)
        button_layout.addWidget(self.exit_button)

        # 添加到主布局
        layout.addLayout(file_layout)
        layout.addLayout(output_layout)
        layout.addLayout(button_layout)

        self.setLayout(layout)
        self.setWindowTitle('视频尾帧提取器')
        self.resize(500, 150)

    def select_file(self):
        """选择输入的 MP4 文件"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择MP4文件", "", "MP4 Files (*.mp4);;All Files (*)"
        )
        if file_path:
            self.file_path_edit.setText(file_path)

    def select_output_directory(self):
        """选择输出目录"""
        directory = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if directory:
            self.output_path_edit.setText(directory)

    def extract_frame(self):
        """执行提取操作"""
        input_file = self.file_path_edit.text().strip()
        if not input_file or not os.path.exists(input_file):
            QMessageBox.warning(self, "警告", "请先选择有效的MP4文件！")
            return

        # 获取输出目录，默认与源文件同级
        output_dir = self.output_path_edit.text().strip()
        if not output_dir:
            output_dir = os.path.dirname(input_file)

        # 生成输出文件名
        base_name = os.path.splitext(os.path.basename(input_file))[0]
        output_file = os.path.join(output_dir, f"{base_name}_last_frame.png")

        try:
            # 使用OpenCV提取尾帧
            result = self.extract_last_frame(input_file, output_file)
            if result:
                QMessageBox.information(self, "成功", f"已成功提取视频尾帧！\n保存路径: {output_file}")
            else:
                QMessageBox.critical(self, "失败", "提取尾帧失败，请检查视频文件是否有效。")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"提取过程中发生错误:\n{str(e)}")

    def extract_last_frame(self, video_path, output_path):
        """使用OpenCV提取视频尾帧"""
        # 打开视频文件
        cap = cv2.VideoCapture(video_path)

        # 检查视频是否成功打开
        if not cap.isOpened():
            return False

        # 获取视频总帧数
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

        # 获取最后一帧的位置
        cap.set(cv2.CAP_PROP_POS_FRAMES, total_frames - 1)

        # 读取最后一帧
        ret, frame = cap.read()

        # 释放资源
        cap.release()

        # 如果成功读取帧，则保存为图片
        if ret:
            success = cv2.imwrite(output_path, frame)
            return success
        else:
            return False


if __name__ == '__main__':
    app = QApplication(sys.argv)
    extractor = VideoFrameExtractor()
    extractor.show()
    sys.exit(app.exec())
