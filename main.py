# main.py
import sys
import os
import json
import logging
import traceback
import warnings
from typing import Optional
from PySide6.QtWidgets import (
    QApplication, QPushButton, QFrame, QRadioButton, QCheckBox,
    QLineEdit, QComboBox, QStackedWidget, QFileDialog, QMessageBox,
    QPlainTextEdit, QWidget
)
from PySide6.QtUiTools import QUiLoader
from PySide6.QtCore import QFile, QIODevice, QObject, Signal, QThread
from PySide6.QtGui import QTextCursor

# 忽略 Qt 样式表警告（box-shadow 等）
warnings.filterwarnings("ignore", message=".*Unknown property.*")

import resources_rc  # 资源文件
import utils
from course_listener import CourseHandler, PageConfig
from DrissionPage import ChromiumPage

# ================== 日志处理器 ==================
class QtSignalHandler(logging.Handler):
    def __init__(self, signal):
        super().__init__()
        self.signal = signal

    def emit(self, record):
        msg = self.format(record)
        self.signal.emit(msg)

# ================== 工作线程信号 ==================
class WorkerSignals(QObject):
    log = Signal(str)
    finished = Signal(bool, str)

# ================== 后台工作线程 ==================
class CourseWorker(QThread):
    def __init__(self, config: dict):
        super().__init__()
        self.config = config
        self.signals = WorkerSignals()

    def log_message(self, msg: str, level: str = "INFO"):
        self.signals.log.emit(f"[{level}] {msg}")

    def run(self):
        try:
            # 加载页面配置
            page_config_path = self.config.get("page_config_path")
            if not page_config_path or not os.path.exists(page_config_path):
                raise Exception("页面配置 JSON 文件不存在或未指定")
            with open(page_config_path, "r", encoding="utf-8") as f:
                elem_config = json.load(f)
            self.log_message(f"已加载页面配置: {page_config_path}")

            # 加载答案
            answers = {}
            if self.config["task_type"] == "complete" and self.config.get("question_needed", False):
                answer_path = self.config.get("answer_path")
                if not answer_path or not os.path.exists(answer_path):
                    raise Exception("完成章节测试题需要提供答案 JSON 文件")
                with open(answer_path, "r", encoding="utf-8") as f:
                    answers = json.load(f)
                self.log_message(f"已加载答案文件: {answer_path}")

            # 启动浏览器
            self.log_message("正在启动浏览器...")
            page = self._launch_browser()
            if not page:
                raise Exception("浏览器启动失败")
            self.log_message("浏览器已启动")

            # 打开课程链接
            course_url = self.config.get("course_url")
            if not course_url:
                raise Exception("课程链接不能为空")
            page.get(course_url)
            self.log_message(f"已打开课程页面: {course_url}")

            # 配置 PageConfig
            page_cfg = PageConfig()
            page_cfg.LISTEN_TIMEOUT = self.config.get("listen_timeout", 3600)
            page_cfg.LOAD_TIMEOUT = self.config.get("load_timeout", 10)
            page_cfg.LOCATOR_TIMEOUT = self.config.get("locate_timeout", 1)
            page_cfg.PAGE_LOAD_TIME = self.config.get("interaction_wait", 2)
            page_cfg.COMPLETE_IMAGE_KEYWORD = self.config.get("complete_image_keyword", "job-status-new-complete")

            # 创建 CourseHandler
            handler = CourseHandler(
                page=page,
                elem_config=elem_config,
                logger=self._get_logger(),
                answers=answers,
                page_config=page_cfg
            )

            # 执行任务
            if self.config["task_type"] == "complete":
                only_unfinished = self.config.get("only_unfinished", True)
                video_needed = self.config.get("video_needed", True)
                question_needed = self.config.get("question_needed", True)
                self.log_message(f"开始执行课程任务: 仅未完成={only_unfinished}, 观看视频={video_needed}, 完成测试={question_needed}")
                failed = handler.run_course_task(
                    only_unfinished=only_unfinished,
                    video_needed=video_needed,
                    question_needed=question_needed
                )
                if failed:
                    self.signals.finished.emit(False, f"部分任务失败: {failed}")
                else:
                    self.signals.finished.emit(True, "所有课程任务已完成")
            else:
                only_unfinished = self.config.get("only_unfinished", True)
                self.log_message(f"开始提取章节测试题，仅未完成={only_unfinished}")
                questions = handler.get_all_questions(only_unfinished=only_unfinished)
                save_path = self.config.get("save_questions_path")
                if not save_path:
                    raise Exception("保存章节测试题需要指定保存路径")
                save_dir = os.path.dirname(save_path)
                if save_dir and not os.path.exists(save_dir):
                    os.makedirs(save_dir, exist_ok=True)
                with open(save_path, "w", encoding="utf-8") as f:
                    json.dump(questions, f, ensure_ascii=False, indent=2)
                self.log_message(f"已保存 {len(questions)} 道题目到 {save_path}")
                self.signals.finished.emit(True, f"成功保存 {len(questions)} 道题目")

        except Exception as e:
            self.log_message(f"发生错误: {str(e)}\n{traceback.format_exc()}", "ERROR")
            self.signals.finished.emit(False, str(e))

    def _launch_browser(self) -> Optional[ChromiumPage]:
        mode = self.config.get("browser_mode")  # "edge", "chrome", "manual"
        incognito = self.config.get("incognito", False)
        port = self.config.get("port", 9444)

        if mode == "manual":
            try:
                page = ChromiumPage(addr=f"127.0.0.1:{port}")
                self.log_message(f"已连接到本地调试端口 {port} 的浏览器")
                return page
            except Exception as e:
                self.log_message(f"连接浏览器失败: {e}", "ERROR")
                return None
        else:
            browser_type = mode
            user_data_dir = self.config.get("user_data_dir")
            if not user_data_dir:
                user_data_dir = None

            if browser_type == "chrome" and not incognito:
                if not user_data_dir:
                    raise Exception("Chrome 非无痕模式下，必须指定一个专用的用户数据目录")
                os.makedirs(user_data_dir, exist_ok=True)

            try:
                page = utils.launch_browser(
                    browser_type=browser_type,
                    user_data_dir=user_data_dir,
                    is_incognito=incognito,
                    port=port
                )
                self.log_message(f"浏览器已启动：{browser_type}，无痕模式={incognito}")
                return page
            except Exception as e:
                self.log_message(f"启动浏览器失败: {e}", "ERROR")
                return None

    def _get_logger(self):
        logger = logging.getLogger("CourseWorker")
        logger.setLevel(logging.INFO)
        if not any(isinstance(h, QtSignalHandler) for h in logger.handlers):
            handler = QtSignalHandler(self.signals.log)
            handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s - %(message)s", "%H:%M:%S"))
            logger.addHandler(handler)
        return logger

# ================== 主窗口 ==================
class MainWindow:
    def __init__(self, ui_file="mainwindow.xml"):
        self.loader = QUiLoader()
        ui_file_path = os.path.join(os.path.dirname(__file__), ui_file)
        ui_file = QFile(ui_file_path)
        if not ui_file.open(QIODevice.ReadOnly):
            raise RuntimeError(f"无法打开 UI 文件: {ui_file_path}")
        self.window = self.loader.load(ui_file)
        ui_file.close()
        if not self.window:
            raise RuntimeError("加载 UI 失败")

        # 记录用户是否手动输入过路径（用于自动填充覆盖判断）
        self.browser_path_manual = False
        self.user_data_path_manual = False

        self.worker = None

        self._get_widgets()
        self._init_state()
        self._connect_signals()

    def _get_widgets(self):
        # 侧边栏
        self.btn_task = self.window.findChild(QPushButton, "btn_task")
        self.btn_settings = self.window.findChild(QPushButton, "btn_settings")
        self.stacked_widget = self.window.findChild(QStackedWidget, "stackedWidget")

        # 任务页面控件
        self.input_course_url = self.window.findChild(QLineEdit, "input_course_url")
        self.radio_complete_course = self.window.findChild(QRadioButton, "radio_complete_course")
        self.radio_save_questions = self.window.findChild(QRadioButton, "radio_save_questions")
        self.checkbox_only_unfinished_course = self.window.findChild(QCheckBox, "checkbox_only_unfinished_course")
        self.checkbox_complete_test = self.window.findChild(QCheckBox, "checkbox_complete_test")
        self.checkbox_watch_video = self.window.findChild(QCheckBox, "checkbox_watch_video")
        self.checkbox_only_unfinished_save = self.window.findChild(QCheckBox, "checkbox_only_unfinished_save")

        # 查找器配置
        self.input_page_config_path = self.window.findChild(QLineEdit, "input_page_config_path")
        self.btn_browse_page_config = self.window.findChild(QPushButton, "btn_browse_page_config")
        self.input_answer_path = self.window.findChild(QLineEdit, "input_answer_path")
        self.btn_browse_answer_config = self.window.findChild(QPushButton, "btn_browse_answer_config")
        self.input_save_path = self.window.findChild(QLineEdit, "input_save_path")
        self.btn_browse_save_path = self.window.findChild(QPushButton, "icon_folder")

        # 浏览器配置
        self.radio_edge = self.window.findChild(QRadioButton, "radio_edge")
        self.radio_chrome = self.window.findChild(QRadioButton, "radio_chrome")
        self.radio_manual = self.window.findChild(QRadioButton, "radio_manual")
        self.checkbox_incognito = self.window.findChild(QCheckBox, "checkbox_incognito")

        self.combo_browser_path_mode = self.window.findChild(QComboBox, "combo_browser_path_mode")
        self.input_browser_path = self.window.findChild(QLineEdit, "input_browser_path")
        self.btn_browse_browser = self.window.findChild(QPushButton, "btn_browse_browser")

        self.combo_user_data_path_mode = self.window.findChild(QComboBox, "combo_user_data_path_mode")
        self.input_user_data_path = self.window.findChild(QLineEdit, "input_user_data_path")
        self.btn_browse_user_data = self.window.findChild(QPushButton, "btn_browse_user_data")

        self.input_port = self.window.findChild(QLineEdit, "input_port")

        # 开始按钮
        self.btn_start_task = self.window.findChild(QPushButton, "btn_start_task")

        # 设置页面控件
        self.input_listen_timeout = self.window.findChild(QLineEdit, "input_listen_timeout")
        self.input_complete_image_keyword = self.window.findChild(QLineEdit, "input_complete_image_keyword")
        self.input_load_timeout = self.window.findChild(QLineEdit, "input_load_timeout")
        self.input_locate_timeout = self.window.findChild(QLineEdit, "input_locate_timeout")
        self.input_page_load_time = self.window.findChild(QLineEdit, "input_page_load_time")

        # 日志
        self.text_log = self.window.findChild(QPlainTextEdit, "text_log")
        self.combo_log_level = self.window.findChild(QComboBox, "combo_log_level")

    def _init_state(self):
        self.btn_task.setChecked(True)
        self.stacked_widget.setCurrentIndex(0)

        self.combo_browser_path_mode.setCurrentIndex(0)  # 自动获取
        self.combo_user_data_path_mode.setCurrentIndex(0)  # 自动获取
        self.input_browser_path.setReadOnly(True)
        self.input_user_data_path.setReadOnly(True)

        self._refresh_browser_path()
        self._refresh_user_data_path()
        self._on_browser_type_changed(True)
        self._on_incognito_changed()

    def _connect_signals(self):
        # 侧边栏
        self.btn_task.clicked.connect(lambda: self.stacked_widget.setCurrentIndex(0))
        self.btn_settings.clicked.connect(lambda: self.stacked_widget.setCurrentIndex(1))

        # 浏览器类型切换
        self.radio_edge.toggled.connect(lambda checked: self._on_browser_type_changed(checked))
        self.radio_chrome.toggled.connect(lambda checked: self._on_browser_type_changed(checked))
        self.radio_manual.toggled.connect(lambda checked: self._on_browser_type_changed(checked))

        # 无痕模式
        self.checkbox_incognito.toggled.connect(self._on_incognito_changed)

        # 浏览器路径模式切换
        self.combo_browser_path_mode.currentIndexChanged.connect(self._on_browser_path_mode_changed)
        self.combo_user_data_path_mode.currentIndexChanged.connect(self._on_user_data_path_mode_changed)

        # 浏览按钮
        self.btn_browse_page_config.clicked.connect(lambda: self._browse_json_file(self.input_page_config_path))
        self.btn_browse_answer_config.clicked.connect(lambda: self._browse_json_file(self.input_answer_path))
        self.btn_browse_save_path.clicked.connect(self._browse_save_file)
        self.btn_browse_browser.clicked.connect(self._browse_browser_executable)
        self.btn_browse_user_data.clicked.connect(self._browse_user_data_directory)

        # 手动编辑标记
        self.input_browser_path.textEdited.connect(lambda: setattr(self, 'browser_path_manual', True))
        self.input_user_data_path.textEdited.connect(lambda: setattr(self, 'user_data_path_manual', True))

        # 开始任务
        self.btn_start_task.clicked.connect(self._on_start_clicked)

    # ------------------ 路径填充逻辑 ------------------
    def _refresh_browser_path(self):
        if self.radio_manual.isChecked():
            self.input_browser_path.clear()
            self.input_browser_path.setEnabled(False)
            self.btn_browse_browser.setEnabled(False)
            self.combo_browser_path_mode.setEnabled(False)
            return
        else:
            self.input_browser_path.setEnabled(True)
            self.btn_browse_browser.setEnabled(True)
            self.combo_browser_path_mode.setEnabled(True)

        mode = self.combo_browser_path_mode.currentText()
        if mode == "自动获取":
            if not self.browser_path_manual:
                browser_type = "edge" if self.radio_edge.isChecked() else "chrome"
                path = utils.get_browser_path(browser_type)
                if path:
                    self.input_browser_path.setText(path)
                else:
                    self.input_browser_path.clear()
                    QMessageBox.warning(self.window, "提示", f"未能自动获取 {browser_type} 浏览器路径，请手动选择")
        else:  # 手动选择
            if not self.browser_path_manual:
                self.input_browser_path.clear()

    def _refresh_user_data_path(self):
        # 手动模式下或无痕模式下，禁用整个区域
        if self.radio_manual.isChecked() or self.checkbox_incognito.isChecked():
            self.input_user_data_path.clear()
            self.input_user_data_path.setEnabled(False)
            self.btn_browse_user_data.setEnabled(False)
            self.combo_user_data_path_mode.setEnabled(False)
            return
        else:
            self.input_user_data_path.setEnabled(True)
            self.btn_browse_user_data.setEnabled(True)
            # 当浏览器为 Chrome 时，锁定下拉菜单为手动选择并禁用
            if self.radio_chrome.isChecked():
                self.combo_user_data_path_mode.setEnabled(False)
                # 强制设置为手动选择
                if self.combo_user_data_path_mode.currentIndex() != 1:
                    self.combo_user_data_path_mode.setCurrentIndex(1)
            else:
                self.combo_user_data_path_mode.setEnabled(True)

        mode = self.combo_user_data_path_mode.currentText()
        if mode == "自动获取":
            if not self.user_data_path_manual:
                browser_type = "edge" if self.radio_edge.isChecked() else "chrome"
                path = utils.get_user_data_path(browser_type)
                if path:
                    self.input_user_data_path.setText(path)
                else:
                    self.input_user_data_path.clear()
                    QMessageBox.warning(self.window, "提示", f"未能自动获取 {browser_type} 用户数据目录")
        else:  # 手动选择
            if not self.user_data_path_manual:
                self.input_user_data_path.clear()

    # ------------------ 事件处理 ------------------
    def _on_browser_type_changed(self, checked: bool = True):
        if not checked:
            return
        
        self._refresh_browser_path()
        self._refresh_user_data_path()
        # Chrome 非无痕模式时提示用户数据目录必要性
        if self.radio_chrome.isChecked() and not self.checkbox_incognito.isChecked():
            if not self.input_user_data_path.text().strip():
                QMessageBox.warning(self.window, "Chrome 安全策略",
                    "Chrome 非无痕模式下，必须指定一个专用的用户数据目录。\n请手动选择一个目录或勾选无痕模式。")

    def _on_incognito_changed(self):
        self._refresh_user_data_path()
        if self.radio_chrome.isChecked() and not self.checkbox_incognito.isChecked():
            if not self.input_user_data_path.text().strip():
                QMessageBox.warning(self.window, "Chrome 安全策略",
                    "Chrome 非无痕模式下，必须指定一个专用的用户数据目录。")

    def _on_browser_path_mode_changed(self):
        self._refresh_browser_path()

    def _on_user_data_path_mode_changed(self):
        self._refresh_user_data_path()

    # ------------------ 文件选择 ------------------
    def _browse_json_file(self, line_edit: QLineEdit):
        file_path, _ = QFileDialog.getOpenFileName(self.window, "选择 JSON 文件", "", "JSON 文件 (*.json)")
        if file_path:
            line_edit.setText(file_path)

    def _browse_save_file(self):
        current = self.input_save_path.text().strip()
        file_path, _ = QFileDialog.getSaveFileName(self.window, "保存章节测试题", current, "JSON 文件 (*.json)")
        if file_path:
            if not file_path.lower().endswith(".json"):
                file_path += ".json"
            self.input_save_path.setText(file_path)

    def _browse_browser_executable(self):
        file_path, _ = QFileDialog.getOpenFileName(self.window, "选择浏览器可执行文件", "", "可执行文件 (*.exe)")
        if file_path:
            self.input_browser_path.setText(file_path)
            self.browser_path_manual = True

    def _browse_user_data_directory(self):
        directory = QFileDialog.getExistingDirectory(self.window, "选择用户数据目录")
        if directory:
            self.input_user_data_path.setText(directory)
            self.user_data_path_manual = True

    # ------------------ 任务启动 ------------------
    def _on_start_clicked(self):
        config = self._collect_config()
        if not config:
            return

        self.btn_start_task.setEnabled(False)
        self.btn_start_task.setText("执行中...")

        self.worker = CourseWorker(config)
        self.worker.signals.log.connect(self._append_log)
        self.worker.signals.finished.connect(self._on_task_finished)
        self.worker.start()

    def _collect_config(self):
        task_type = "complete" if self.radio_complete_course.isChecked() else "save"
        config = {"task_type": task_type}

        course_url = self.input_course_url.text().strip()
        if not course_url:
            QMessageBox.warning(self.window, "错误", "课程链接不能为空")
            return None
        config["course_url"] = course_url

        page_config_path = self.input_page_config_path.text().strip()
        if not page_config_path or not os.path.exists(page_config_path):
            QMessageBox.warning(self.window, "错误", "页面配置 JSON 文件不存在或未指定")
            return None
        config["page_config_path"] = page_config_path

        if task_type == "complete" and self.checkbox_complete_test.isChecked():
            answer_path = self.input_answer_path.text().strip()
            if not answer_path or not os.path.exists(answer_path):
                QMessageBox.warning(self.window, "错误", "完成章节测试题需要提供有效的答案 JSON 文件")
                return None
            config["answer_path"] = answer_path

        if task_type == "save":
            save_path = self.input_save_path.text().strip()
            if not save_path:
                file_path, _ = QFileDialog.getSaveFileName(self.window, "保存章节测试题", "", "JSON 文件 (*.json)")
                if not file_path:
                    return None
                if not file_path.lower().endswith(".json"):
                    file_path += ".json"
                save_path = file_path
                self.input_save_path.setText(save_path)
            config["save_questions_path"] = save_path

        if self.radio_manual.isChecked():
            config["browser_mode"] = "manual"
        elif self.radio_edge.isChecked():
            config["browser_mode"] = "edge"
        else:
            config["browser_mode"] = "chrome"

        config["incognito"] = self.checkbox_incognito.isChecked()
        config["port"] = int(self.input_port.text().strip() or "9444")
        config["browser_path"] = self.input_browser_path.text().strip()
        config["user_data_dir"] = self.input_user_data_path.text().strip()

        if config["browser_mode"] == "chrome" and not config["incognito"]:
            if not config["user_data_dir"]:
                QMessageBox.warning(self.window, "错误", "Chrome 非无痕模式下，必须指定一个专用的用户数据目录")
                return None

        config["listen_timeout"] = int(self.input_listen_timeout.text().strip() or "3600")
        config["complete_image_keyword"] = self.input_complete_image_keyword.text().strip() or "job-status-new-complete"
        config["load_timeout"] = int(self.input_load_timeout.text().strip() or "10")
        config["locate_timeout"] = int(self.input_locate_timeout.text().strip() or "1")
        config["interaction_wait"] = int(self.input_page_load_time.text().strip() or "2")

        if task_type == "complete":
            config["video_needed"] = self.checkbox_watch_video.isChecked()
            config["question_needed"] = self.checkbox_complete_test.isChecked()
            config["only_unfinished"] = self.checkbox_only_unfinished_course.isChecked()
        else:
            config["only_unfinished"] = self.checkbox_only_unfinished_save.isChecked()

        return config

    def _append_log(self, msg):
        self.text_log.appendPlainText(msg)
        cursor = self.text_log.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.text_log.setTextCursor(cursor)

    def _on_task_finished(self, success: bool, message: str):
        self.btn_start_task.setEnabled(True)
        self.btn_start_task.setText("开始任务")
        if success:
            self._append_log(f"✅ {message}")
            QMessageBox.information(self.window, "完成", message)
        else:
            self._append_log(f"❌ 任务失败: {message}")
            QMessageBox.critical(self.window, "失败", f"任务执行失败:\n{message}")

    def show(self):
        self.window.show()

def main():
    app = QApplication(sys.argv)
    main_win = MainWindow("mainwindow.ui")
    main_win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()