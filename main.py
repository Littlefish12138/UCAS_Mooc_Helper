"""
挂课工具 - 图形界面
支持手动启动（指定浏览器路径、用户数据目录）和自动启动（连接已打开的调试端口浏览器）
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import threading
import time
from collections import deque
from DrissionPage import ChromiumPage

# 导入原有业务类（请确保 course_listener.py 在同一目录下或已安装）
from course_listener import BrowserLauncher, CoursePageHandler, PageConfig

# ================== 业务函数（支持回调） ==================
def run_video_task_with_ui(launch_func, course_url, log_callback=None, completion_callback=None):
    """
    执行视频任务，支持回调输出日志和完成通知
    :param launch_func: 可调用对象，返回 ChromiumPage 实例
    :param course_url: 课程页面URL
    :param log_callback: 接收字符串的回调，用于输出日志
    :param completion_callback: 任务完成时回调，无参数
    """
    def log(msg):
        if log_callback:
            log_callback(msg)
        else:
            print(msg)

    try:
        # 启动浏览器
        page = launch_func()

        # 打开课程页面
        page.get(course_url)
        time.sleep(3)

        # 创建页面处理器
        handler = CoursePageHandler(page, PageConfig)

        # 可选：展开章节（根据页面需要）
        # handler.expand_all_chapters()

        # 获取视频数量
        video_counts = handler.get_chapter_video_counts()
        total_chapters = len(video_counts)
        log(f"检测到 {total_chapters} 章")

        if total_chapters == 0:
            log("未检测到章节，请检查页面结构")
            return

        # 构建任务队列
        tasks = deque()
        for chap_idx in range(1, total_chapters + 1):
            video_num = video_counts[chap_idx - 1]
            log(f"第 {chap_idx} 章共有 {video_num} 个视频")
            for vid_idx in range(1, video_num + 1):
                tasks.append((chap_idx, vid_idx))

        log(f"总任务数: {len(tasks)}")
        log("开始处理任务队列...\n")

        # 处理任务
        while tasks:
            chap_idx, vid_idx = tasks.popleft()
            log(f"\n正在处理第 {chap_idx} 章第 {vid_idx} 个视频...")

            # 点击视频
            if not handler.click_video_by_index(chap_idx, vid_idx):
                log(f"点击视频失败，将重新加入队列")
                tasks.append((chap_idx, vid_idx))
                time.sleep(2)
                continue

            time.sleep(4)  # 等待页面刷新

            # 检查是否已完成
            if handler.is_video_completed():
                log("该视频已完成，跳过（不重试）")
                continue

            # 点击播放按钮
            handler.click_play_button()

            # 启动监控线程
            handler.start_playback_monitor()

            # 等待完成图片
            success = handler.wait_for_complete_image(timeout=3600)

            # 停止监控
            handler.stop_playback_monitor()

            if not success:
                log("视频播放超时，将重新加入队列")
                tasks.append((chap_idx, vid_idx))

            time.sleep(1)

        log("\n所有视频已处理完毕")

    except Exception as e:
        log(f"运行出错: {e}")
    finally:
        if completion_callback:
            completion_callback()

# ================== UI 界面 ==================
class GuiApp:
    def __init__(self, root):
        self.root = root
        self.root.title("UCAS慕课挂课工具")
        self.root.geometry("700x650")
        self.root.resizable(True, True)

        # 变量
        self.launch_mode = tk.StringVar(value="manual")  # "manual" 或 "auto"
        self.browser_type = tk.StringVar(value="Edge")
        self.url = tk.StringVar()
        self.browser_path = tk.StringVar()
        self.user_data_dir = tk.StringVar()
        self.auto_port = tk.StringVar(value="9222")
        self.running = False  # 任务是否运行中
        self.incognito_var = tk.BooleanVar(value=True)

        # 创建控件
        self.create_widgets()

    def create_widgets(self):
        # 标题
        title_label = ttk.Label(self.root, text="UCAS慕课挂课工具", font=("Microsoft YaHei", 16, "bold"))
        title_label.pack(pady=10)

        # 主框架
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # ---------- 课程链接 ----------
        url_frame = ttk.Frame(main_frame)
        url_frame.pack(fill=tk.X, pady=5)
        ttk.Label(url_frame, text="课程链接:").pack(side=tk.LEFT)
        url_entry = ttk.Entry(url_frame, textvariable=self.url, width=60)
        url_entry.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)

        # ---------- 启动模式选择（垂直排列，注释在按钮右侧） ----------
        mode_frame = ttk.LabelFrame(main_frame, text="启动模式", padding="5")
        mode_frame.pack(fill=tk.X, pady=5)

        # 手动启动选项
        manual_btn = ttk.Radiobutton(mode_frame, text="手动启动", variable=self.launch_mode,
                                    value="manual", command=self.on_mode_change)
        manual_btn.grid(row=0, column=0, sticky=tk.W, padx=5, pady=2)
        manual_hint = ttk.Label(mode_frame, text="(使用您的用户数据文件夹，程序自动启动浏览器)")
        manual_hint.grid(row=0, column=1, sticky=tk.W, padx=5)

        # 自动启动选项
        auto_btn = ttk.Radiobutton(mode_frame, text="自动启动", variable=self.launch_mode,
                                value="auto", command=self.on_mode_change)
        auto_btn.grid(row=1, column=0, sticky=tk.W, padx=5, pady=2)
        auto_hint = ttk.Label(mode_frame, text="(连接已开启调试端口的浏览器)")
        auto_hint.grid(row=1, column=1, sticky=tk.W, padx=5)

        # 保存提示标签，备用（如需要高亮，可在此修改样式）
        self.manual_hint = manual_hint
        self.auto_hint = auto_hint

        # ---------- 配置区域（手动/自动动态内容） ----------
        # ---- 配置区域容器 ----
        self.config_container = ttk.Frame(main_frame)
        self.config_container.pack(fill=tk.X, pady=5)

        # 手动启动配置
        self.manual_frame = ttk.Frame(self.config_container, padding="5")
        # ---- 浏览器类型 + 是否选择无痕模式 ----
        row1 = ttk.Frame(self.manual_frame)
        row1.pack(fill=tk.X, pady=2)

        # 浏览器类型
        ttk.Label(row1, text="浏览器类型:").pack(side=tk.LEFT)
        self.browser_combo = ttk.Combobox(row1, textvariable=self.browser_type,
                                        values=["Edge", "Chrome"], state="readonly")
        self.browser_combo.pack(side=tk.LEFT, padx=5)
        self.browser_combo.bind("<<ComboboxSelected>>", self.on_browser_change)
        # 无痕模式
        incognito_cb = ttk.Checkbutton(row1, text="无痕模式(推荐)", variable=self.incognito_var, 
                                       command=self.on_incognito_toggle)
        incognito_cb.pack(side=tk.RIGHT, padx=10)

        # 浏览器路径
        row2 = ttk.Frame(self.manual_frame)
        row2.pack(fill=tk.X, pady=2)
        ttk.Label(row2, text="浏览器路径:").pack(side=tk.LEFT)
        self.browser_path_entry = ttk.Entry(row2, textvariable=self.browser_path, width=50)
        self.browser_path_entry.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        ttk.Button(row2, text="浏览", command=self.browse_browser).pack(side=tk.LEFT, padx=2)

        # 用户数据目录
        row3 = ttk.Frame(self.manual_frame)
        row3.pack(fill=tk.X, pady=2)
        ttk.Label(row3, text="用户数据目录:").pack(side=tk.LEFT)
        self.user_data_entry = ttk.Entry(row3, textvariable=self.user_data_dir, width=50)
        self.user_data_entry.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
        self.user_data_browse_btn = ttk.Button(row3, text="浏览", command=self.browse_user_data)
        self.user_data_browse_btn.pack(side=tk.LEFT, padx=2)

        # 初始根据无痕模式状态设置用户数据目录的可用性
        self.on_incognito_toggle()

        # 自动启动配置
        self.auto_frame = ttk.Frame(self.config_container, padding="5")
        row_auto = ttk.Frame(self.auto_frame)
        row_auto.pack(fill=tk.X, pady=2)
        ttk.Label(row_auto, text="调试端口:").pack(side=tk.LEFT)
        ttk.Entry(row_auto, textvariable=self.auto_port, width=10).pack(side=tk.LEFT, padx=5)
        ttk.Label(row_auto, text="示例:cmd中启动 Chrome 时添加 --remote-debugging-port=9222)").pack(side=tk.LEFT, padx=5)

        # 初始根据模式显示对应区域
        #self.update_mode_visibility()

        # ---------- 运行日志（放在最下方） ----------
        log_label = ttk.Label(main_frame, text="运行日志:")
        log_label.pack(anchor=tk.W, pady=(10, 0))
        self.log_text = scrolledtext.ScrolledText(main_frame, height=15, wrap=tk.WORD, state=tk.DISABLED)
        self.log_text.pack(fill=tk.BOTH, expand=True, pady=5)

        # ---------- 按钮 ----------
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(pady=10)
        self.start_button = ttk.Button(button_frame, text="开始挂课", command=self.start_task)
        self.start_button.pack(side=tk.LEFT, padx=5)
        self.stop_button = ttk.Button(button_frame, text="停止", command=self.stop_task, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT, padx=5)

        # 初始化默认路径
        self.on_browser_change()

        # 初始化对应显示区域
        self.update_mode_visibility()

    def update_mode_hints(self):
        """根据当前模式显示对应提示"""
        mode = self.launch_mode.get()
        if mode == "manual":
            self.manual_hint.lift()
            self.auto_hint.lower()
        else:
            self.manual_hint.lower()
            self.auto_hint.lift()

    def update_mode_visibility(self):
        """根据启动模式显示/隐藏配置区域"""
        mode = self.launch_mode.get()
        if mode == "manual":
            self.manual_frame.grid(row=0, column=0, sticky="nsew")
            self.auto_frame.grid_remove()
        else:
            self.manual_frame.grid_remove()
            self.auto_frame.grid(row=0, column=0, sticky="nsew")
        self.config_container.grid_columnconfigure(0, weight=1)
        self.config_container.grid_rowconfigure(0, weight=1)
        

    def on_mode_change(self):
        """启动模式切换回调"""
        self.update_mode_visibility()
        self.update_mode_hints()

    def on_browser_change(self, event=None):
        """切换浏览器时，自动填充默认路径（如果用户未修改）"""
        bt = self.browser_type.get()
        if bt == "Edge":
            default_browser = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
            default_user_data = r"C:\Users\huawei\AppData\Local\Microsoft\Edge\User Data"
        else:  # Chrome
            default_browser = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
            default_user_data = r"C:\Users\huawei\AppData\Local\Google\Chrome\User Data"

        if not self.browser_path.get():
            self.browser_path.set(default_browser)
        if not self.user_data_dir.get():
            self.user_data_dir.set(default_user_data)
    
    def on_incognito_toggle(self):
        """无痕模式切换时，控制用户数据目录输入框和浏览按钮的状态"""
        incognito = self.incognito_var.get()
        state = tk.DISABLED if incognito else tk.NORMAL
        self.user_data_entry.config(state=state)
        self.user_data_browse_btn.config(state=state)
        if incognito:
            # 清空用户数据目录内容
            self.user_data_dir.set("不使用")
        else:
            # 如果之前没有值，可尝试恢复默认值（可选）
            if not self.user_data_dir.get():
                self.on_browser_change()  # 重新填充默认路径

    def browse_browser(self):
        path = filedialog.askopenfilename(title="选择浏览器可执行文件",
                                          filetypes=[("Executable", "*.exe"), ("All Files", "*.*")])
        if path:
            self.browser_path.set(path)

    def browse_user_data(self):
        path = filedialog.askdirectory(title="选择用户数据目录")
        if path:
            self.user_data_dir.set(path)

    def log(self, msg):
        """将日志添加到文本框（线程安全）"""
        def _append():
            self.log_text.config(state=tk.NORMAL)
            self.log_text.insert(tk.END, msg + "\n")
            self.log_text.see(tk.END)
            self.log_text.config(state=tk.DISABLED)
        self.root.after(0, _append)

    def start_task(self):
        if self.running:
            return
        # 验证输入
        url = self.url.get().strip()
        if not url:
            messagebox.showwarning("警告", "请输入课程链接")
            return

        # 根据启动模式构建启动函数
        mode = self.launch_mode.get()
        if mode == "manual":
            bt = self.browser_type.get().lower()
            if self.incognito_var.get():
                def launch():
                    return BrowserLauncher.launch_incognito(browser_type=bt)
            else:
                browser_path = self.browser_path.get().strip()
                user_data = self.user_data_dir.get().strip()
                if not browser_path or not user_data:
                    messagebox.showwarning("警告", "请填写浏览器路径和用户数据目录")
                    return
                bt = self.browser_type.get().lower()
                def launch():
                    return BrowserLauncher.launch_with_user_data(
                        browser_type=bt,
                        browser_path=browser_path,
                        user_data_dir=user_data
                    )
        else:  # auto
            port_str = self.auto_port.get().strip()
            if not port_str.isdigit():
                messagebox.showwarning("警告", "请输入有效的端口号")
                return
            port = int(port_str)
            def launch():
                return BrowserLauncher.connect_to_existing(port)

        # 设置状态
        self.running = True
        self.start_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL)
        self.log("任务开始...")

        # 启动线程
        self.task_thread = threading.Thread(
            target=run_video_task_with_ui,
            args=(launch, url, self.log, self.task_completed)
        )
        self.task_thread.daemon = True
        self.task_thread.start()

    def stop_task(self):
        """停止任务（简化实现，仅改变状态）"""
        if self.running:
            self.log("正在尝试停止... (任务可能无法立即中断)")
            self.running = False
            self.start_button.config(state=tk.NORMAL)
            self.stop_button.config(state=tk.DISABLED)
            # 实际停止需要修改业务函数支持标志，此处仅做界面处理

    def task_completed(self):
        """任务完成回调"""
        self.running = False
        self.start_button.config(state=tk.NORMAL)
        self.stop_button.config(state=tk.DISABLED)
        self.log("任务已结束。")

# ================== 主程序 ==================
if __name__ == "__main__":
    root = tk.Tk()
    app = GuiApp(root)
    root.mainloop()