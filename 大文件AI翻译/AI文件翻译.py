#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🐾 完整 AI 文本翻译器 GUI（PyQt6）
- 支持多段文本翻译
- 背景线程翻译，保持 UI 响应
- 记住 BaseURL / APIKey / 模型名称（多选框控制）
- 文件支持 txt/md/ini/json/log/*.*
"""

import sys
import os
import re
import json
import time
import threading
from typing import List, Dict, Any, Optional
import requests
from PyQt6 import QtWidgets, QtCore
from PyQt6.QtWidgets import (
    QApplication, QWidget, QLabel, QLineEdit, QPushButton, QTextEdit,
    QFileDialog, QComboBox, QProgressBar, QSpinBox, QRadioButton,
    QHBoxLayout, QVBoxLayout, QFormLayout, QCheckBox, QMessageBox
)

CONFIG_FILE = "config.json"


# -------------------------
# 配置管理
# -------------------------
def load_config():
    """加载本地保存的配置"""
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {"base_url": "", "api_key": "", "model": "", "remember": True}


def save_config(data):
    """保存配置到本地"""
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except:
        pass


# -------------------------
# 工具函数
# -------------------------
def safe_read_text_file(path: str, encoding: str = 'utf-8') -> str:
    """安全读取文本文件"""
    encs = [encoding, 'utf-8', 'gb18030', 'gbk', 'latin-1']
    for enc in encs:
        try:
            with open(path, 'r', encoding=enc) as f:
                return f.read()
        except Exception:
            continue
    with open(path, 'rb') as f:
        return f.read().decode('utf-8', errors='ignore')


def write_text_file(path: str, text: str, encoding: str = 'utf-8'):
    """写文本文件"""
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w', encoding=encoding) as f:
        f.write(text)


def sentence_split(text: str) -> List[str]:
    """简单句子切分器"""
    pattern = r'([。！？\!\?]|\.{1,3})(\s+|$)'
    parts = []
    last = 0
    for m in re.finditer(pattern, text, flags=re.M):
        end = m.end()
        parts.append(text[last:end].strip())
        last = end
    if last < len(text):
        tail = text[last:].strip()
        if tail:
            parts.append(tail)
    if not parts:
        parts = [p.strip() for p in text.splitlines() if p.strip()]
    return [p for p in parts if p]


def chunk_text_by_lines_with_overflow(
    text: str,
    max_chars: int = 3000,
    overflow: int = 200,
    min_chunk_chars: int = 300,
    overlap_sentences: int = 2
) -> List[Dict[str, Any]]:
    """按行 + 溢出分块"""
    lines = text.splitlines(keepends=True)
    chunks = []
    buf = []
    buf_len = 0

    def flush_buf():
        nonlocal buf, buf_len
        if not buf:
            return
        joined = ''.join(buf).strip()
        if joined:
            chunks.append({'text': joined, 'sentences': sentence_split(joined)})
        buf = []
        buf_len = 0

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        line_len = len(line)
        if buf_len + line_len <= max_chars:
            buf.append(line)
            buf_len += line_len
            i += 1
            continue
        if buf_len + line_len <= max_chars + overflow:
            buf.append(line)
            buf_len += line_len
            flush_buf()
            i += 1
            continue
        accumulated = ''.join(buf)
        if len(accumulated) == 0:
            sents = sentence_split(line)
            cur, cur_len = [], 0
            for s in sents:
                if cur_len + len(s) <= max_chars or not cur:
                    cur.append(s)
                    cur_len += len(s)
                else:
                    chunks.append({'text': ''.join(cur).strip(), 'sentences': cur.copy()})
                    cur = cur[-overlap_sentences:] if overlap_sentences and len(cur) >= overlap_sentences else []
                    cur_len = sum(len(x) for x in cur)
                    cur.append(s)
                    cur_len += len(s)
            if cur:
                chunks.append({'text': ''.join(cur).strip(), 'sentences': cur.copy()})
            i += 1
            continue
        # 尝试在 accumulated 内找换行
        acc_len = len(accumulated)
        target = max_chars
        lower_bound = max(0, max_chars - overflow)
        cut_pos = None
        if acc_len >= lower_bound:
            search_start = min(acc_len, target)
            for pos in range(search_start, lower_bound - 1, -1):
                if accumulated[pos - 1] == '\n':
                    cut_pos = pos
                    break
        if cut_pos:
            head = accumulated[:cut_pos]
            tail = accumulated[cut_pos:]
            chunks.append({'text': head.strip(), 'sentences': sentence_split(head.strip())})
            buf = [tail]
            buf_len = len(tail)
            continue
        else:
            if acc_len >= max_chars:
                head = accumulated[:max_chars]
                tail = accumulated[max_chars:]
                chunks.append({'text': head.strip(), 'sentences': sentence_split(head.strip())})
                buf = [tail]
                buf_len = len(tail)
                continue
            if buf_len >= min_chunk_chars:
                flush_buf()
                continue
            else:
                buf.append(line)
                buf_len += line_len
                flush_buf()
                i += 1
                continue
    flush_buf()
    return chunks


# -------------------------
# API Client
# -------------------------
class SimpleChatAPI:
    """OpenAI-compatible Chat API"""

    def __init__(self, base_url: str, api_key: str, model: str, timeout: int = 60):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def chat_completion(self, messages: List[Dict[str, str]], temperature: float = 0.0,
                        max_tokens: Optional[int] = None) -> Dict[str, Any]:
        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        payload = {"model": self.model, "messages": messages, "temperature": temperature}
        if max_tokens is not None:
            payload['max_tokens'] = max_tokens
        resp = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
        if resp.status_code >= 400:
            raise RuntimeError(f"API error {resp.status_code}: {resp.text}")
        return resp.json()


# -------------------------
# Chunk Translator
# -------------------------
class ChunkTranslator:
    """管理分段翻译逻辑"""

    def __init__(self, api_client: SimpleChatAPI, target_lang: str = 'zh',
                 prompt_template: Optional[str] = None, strategy: str = 'overlap',
                 overlap_sentences: int = 2, summary_token_limit: int = 120,
                 max_retries: int = 2, retry_backoff: float = 1.0):
        self.client = api_client
        self.target_lang = target_lang
        self.prompt_template = prompt_template or "请将以下文本翻译为 {target_lang}：\n\n{source}"
        self.strategy = strategy
        self.overlap_sentences = overlap_sentences
        self.summary_token_limit = summary_token_limit
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff

    def _call_model_with_retry(self, messages: List[Dict[str, str]], temperature: float = 0.0) -> str:
        last_err = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self.client.chat_completion(messages=messages, temperature=temperature)
                choices = resp.get('choices')
                if choices and len(choices) > 0:
                    return choices[0].get('message', {}).get('content', '')
                return json.dumps(resp, ensure_ascii=False)
            except Exception as e:
                last_err = e
                time.sleep(self.retry_backoff * (2 ** attempt))
        raise RuntimeError(f"Model call failed: {last_err}")

    def _build_messages(self, source_text: str, prev_summary: Optional[str], prev_context: Optional[str]):
        filled = self.prompt_template.format(
            source=source_text,
            prev_summary=prev_summary or "",
            prev_context=prev_context or "",
            target_lang=self.target_lang
        )
        system_msg = ("You are a professional translator. Keep formatting as plaintext. "
                      "If the input is code or structured data, preserve code blocks and technical terms.")
        return [{"role": "system", "content": system_msg},
                {"role": "user", "content": filled}]

    def translate_chunks(self, chunks: List[Dict[str, Any]], progress_callback=None, log_callback=None) -> str:
        total = len(chunks)
        translated_parts = []
        prev_summary = ""
        prev_context = ""

        for idx, chunk in enumerate(chunks):
            source = chunk['text']
            if self.strategy == 'overlap' and self.overlap_sentences > 0 and translated_parts:
                prev_sents = sentence_split(translated_parts[-1])
                prev_context = ''.join(prev_sents[-self.overlap_sentences:]) if prev_sents else ''
            elif self.strategy == 'iterative_summary':
                prev_context = prev_summary or ""
            else:
                prev_context = ""

            messages = self._build_messages(source, prev_summary, prev_context)
            if log_callback:
                log_callback(f"[{idx + 1}/{total}] sending chunk ({len(source)} chars)...")
            out = self._call_model_with_retry(messages, temperature=0.0)
            translated_parts.append(out.strip())

            if self.strategy == 'iterative_summary':
                # 简易摘要：取前80字符
                prev_summary = out.strip()[:80]
                if log_callback:
                    log_callback(f"Generated summary for chunk {idx + 1}: {prev_summary}")

            if progress_callback:
                progress_callback(idx + 1, total)

        return '\n\n'.join(translated_parts)


# -------------------------
# Translate Thread
# -------------------------
class TranslateThread(QtCore.QThread):
    """后台翻译线程"""

    progress = QtCore.pyqtSignal(int, int)
    log = QtCore.pyqtSignal(str)
    done = QtCore.pyqtSignal(str)
    error = QtCore.pyqtSignal(str)

    def __init__(self, api_base: str, api_key: str, model: str,
                 source_path: str, out_dir: Optional[str],
                 target_lang: str, prompt_template: str,
                 strategy: str, max_chars: int, overlap_sentences: int,
                 overflow: int = 200, parent=None):
        super().__init__(parent)
        self.api_base = api_base
        self.api_key = api_key
        self.model = model
        self.source_path = source_path
        self.out_dir = out_dir
        self.target_lang = target_lang
        self.prompt_template = prompt_template
        self.strategy = strategy
        self.max_chars = max_chars
        self.overlap_sentences = overlap_sentences
        self.overflow = overflow

    def run(self):
        try:
            self.log.emit("Reading source file...")
            txt = safe_read_text_file(self.source_path)
            self.log.emit(f"Source length: {len(txt)} chars")
            self.log.emit("Chunking text...")
            chunks = chunk_text_by_lines_with_overflow(
                txt,
                max_chars=self.max_chars,
                overflow=self.overflow,
                overlap_sentences=self.overlap_sentences
            )
            self.log.emit(f"Produced {len(chunks)} chunks")

            client = SimpleChatAPI(self.api_base, self.api_key, self.model)
            translator = ChunkTranslator(
                api_client=client,
                target_lang=self.target_lang,
                prompt_template=self.prompt_template,
                strategy=self.strategy,
                overlap_sentences=self.overlap_sentences
            )

            translated = translator.translate_chunks(chunks,
                                                     progress_callback=lambda c, t: self.progress.emit(c, t),
                                                     log_callback=lambda m: self.log.emit(m))

            base = os.path.basename(self.source_path)
            name, ext = os.path.splitext(base)
            target_code = self.target_lang.replace(' ', '-').lower()
            out_name = f"{name}-{target_code}{ext or '.txt'}"
            out_path = os.path.join(self.out_dir or os.path.dirname(self.source_path), out_name)
            write_text_file(out_path, translated)
            self.done.emit(out_path)
        except Exception as e:
            self.error.emit(str(e))


# -------------------------
# GUI
# -------------------------
class TranslatorGUI(QWidget):
    """主窗口 GUI"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("🐾 AI 文本翻译器")
        self.resize(900, 700)
        self.config = load_config()
        self.thread: Optional[TranslateThread] = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout()
        form = QFormLayout()

        # Base URL
        self.base_input = QLineEdit(self.config.get("base_url", ""))
        form.addRow("Base URL:", self.base_input)
        # API Key
        self.key_input = QLineEdit(self.config.get("api_key", ""))
        self.key_input.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("API Key:", self.key_input)
        # Model
        self.model_input = QLineEdit(self.config.get("model", ""))
        form.addRow("Model:", self.model_input)
        # Remember checkbox
        self.remember_check = QCheckBox("记住设置")
        self.remember_check.setChecked(self.config.get("remember", True))
        form.addRow(self.remember_check)

        # Source file
        hfile = QHBoxLayout()
        self.source_path_edit = QLineEdit()
        self.btn_browse_source = QPushButton("选择源文件")
        self.btn_browse_source.clicked.connect(self.select_source_file)
        hfile.addWidget(self.source_path_edit)
        hfile.addWidget(self.btn_browse_source)
        form.addRow("源文件:", hfile)

        # Output dir
        hout = QHBoxLayout()
        self.out_dir_edit = QLineEdit()
        self.btn_browse_out = QPushButton("选择输出目录")
        self.btn_browse_out.clicked.connect(self.select_out_dir)
        hout.addWidget(self.out_dir_edit)
        hout.addWidget(self.btn_browse_out)
        form.addRow("输出目录:", hout)

        # Target language
        self.target_lang_input = QLineEdit("中文 (Chinese)")
        form.addRow("目标语言:", self.target_lang_input)

        # Prompt
        self.prompt_edit = QTextEdit()
        default_prompt = "请将下面文本翻译为 {target_lang}，保留段落结构和代码：\n\n{source}"
        self.prompt_edit.setPlainText(default_prompt)
        form.addRow("提示词模板:", self.prompt_edit)

        # Chunk & overlap
        chunk_layout = QHBoxLayout()
        self.max_chars_spin = QSpinBox()
        self.max_chars_spin.setRange(500, 20000)
        self.max_chars_spin.setValue(3000)
        chunk_layout.addWidget(QLabel("Chunk 最大字符数:"))
        chunk_layout.addWidget(self.max_chars_spin)

        self.overlap_spin = QSpinBox()
        self.overlap_spin.setRange(0, 10)
        self.overlap_spin.setValue(2)
        chunk_layout.addWidget(QLabel("Overlap sentences:"))
        chunk_layout.addWidget(self.overlap_spin)

        self.overflow_spin = QSpinBox()
        self.overflow_spin.setRange(0, 5000)
        self.overflow_spin.setValue(200)
        chunk_layout.addWidget(QLabel("允许溢出字符数:"))
        chunk_layout.addWidget(self.overflow_spin)
        form.addRow(chunk_layout)

        # Strategy
        strategy_layout = QHBoxLayout()
        self.strategy_combo = QComboBox()
        self.strategy_combo.addItems(["overlap", "iterative_summary"])
        strategy_layout.addWidget(QLabel("上下文策略:"))
        strategy_layout.addWidget(self.strategy_combo)
        form.addRow(strategy_layout)

        layout.addLayout(form)

        # Control buttons
        btn_layout = QHBoxLayout()
        self.btn_start = QPushButton("开始翻译")
        self.btn_start.clicked.connect(self.start_translation)
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.clicked.connect(self.cancel_translation)
        btn_layout.addWidget(self.btn_start)
        btn_layout.addWidget(self.btn_cancel)
        layout.addLayout(btn_layout)

        # Progress & log
        self.progress_bar = QProgressBar()
        layout.addWidget(self.progress_bar)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        layout.addWidget(self.log_text)

        self.setLayout(layout)

    # -----------------
    # UI helpers
    # -----------------
    def select_source_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择要翻译的文件", "",
            "文本文件 (*.txt *.md *.ini *.json *.log);;所有文件 (*.*)"
        )
        if path:
            self.source_path_edit.setText(path)

    def select_out_dir(self):
        path = QFileDialog.getExistingDirectory(self, "选择输出目录", "")
        if path:
            self.out_dir_edit.setText(path)

    def append_log(self, text: str):
        self.log_text.append(text)
        self.log_text.verticalScrollBar().setValue(self.log_text.verticalScrollBar().maximum())

    # -----------------
    # Translation logic
    # -----------------
    def start_translation(self):
        src = self.source_path_edit.text().strip()
        if not src or not os.path.isfile(src):
            QMessageBox.warning(self, "错误", "请先选择源文件")
            return

        base = self.base_input.text().strip()
        key = self.key_input.text().strip()
        model = self.model_input.text().strip()
        if not base or not key or not model:
            QMessageBox.warning(self, "错误", "请填写 BaseURL、APIKey 和 Model")
            return

        out_dir = self.out_dir_edit.text().strip() or None
        target_lang = self.target_lang_input.text().strip()
        prompt_template = self.prompt_edit.toPlainText()
        strategy = self.strategy_combo.currentText()
        max_chars = self.max_chars_spin.value()
        overlap = self.overlap_spin.value()
        overflow = self.overflow_spin.value()

        if self.remember_check.isChecked():
            save_config({
                "base_url": base,
                "api_key": key,
                "model": model,
                "remember": True
            })

        self.thread = TranslateThread(
            api_base=base,
            api_key=key,
            model=model,
            source_path=src,
            out_dir=out_dir,
            target_lang=target_lang,
            prompt_template=prompt_template,
            strategy=strategy,
            max_chars=max_chars,
            overlap_sentences=overlap,
            overflow=overflow
        )
        self.thread.progress.connect(lambda c, t: self.progress_bar.setValue(int(c / t * 100)))
        self.thread.log.connect(self.append_log)
        self.thread.done.connect(lambda p: self.append_log(f"✅ 完成，输出: {p}"))
        self.thread.error.connect(lambda e: self.append_log(f"❌ 错误: {e}"))
        self.thread.start()
        self.append_log("🚀 翻译开始...")

    def cancel_translation(self):
        if self.thread and self.thread.isRunning():
            self.thread.terminate()
            self.append_log("🛑 翻译已取消")


# -------------------------
# Run
# -------------------------
if __name__ == "__main__":
    app = QApplication(sys.argv)
    gui = TranslatorGUI()
    gui.show()
    sys.exit(app.exec())
