"""
视频自动播放与翻页脚本（重构版）
功能：
1. 支持多种浏览器启动模式（Edge/Chrome，普通/无痕，或连接已启动的调试端口）
2. 封装页面操作，便于维护
3. 使用双端队列管理任务，失败自动重试
4. 监控视频暂停并自动恢复播放
"""

import time
import subprocess
import threading
from collections import deque
from DrissionPage import ChromiumPage, ChromiumOptions

import utils
# ================== 配置区域（选择器、关键字等） ==================
class PageConfig:
    """页面元素选择器和请求关键字配置"""
    # 课程树根元素
    COURSE_TREE = "#coursetree"
    # 章节容器
    CHAPTER_CONTAINER = "#coursetree .cells"
    # 章节标题（用于展开）    暂时没用
    CHAPTER_TITLE = "#coursetree .cells > h3"
    # 视频链接容器
    VIDEO_LINK_CONTAINER = ".ncells a"
    # 已完成图标（视频页面）
    COMPLETED_ICON = "#ext-gen1051"  # 或 '.ans-job-icon.ans-job-icon-clear'

    # 是否为视频
    IS_VIDEO = ".video-js"

    # 视频播放状态(是否已经播放过)关键字
    PLAY_STATUS = ".vjs-has-started"
    # 视频当前播放状态关键字
    IS_PAUSED = ".vjs-paused" # 表示暂停
    # 播放按钮（初始播放）
    PLAY_BUTTON = "#video > button"  # 可改为 '.vjs-big-play-button'
    # 播放/暂停控制按钮（用于已开始播放）
    PLAY_PAUSE_CONTROL = ".vjs-play-control"

    # 任务完成图片请求关键字
    COMPLETE_IMAGE_KEYWORD = "job-status-new-complete"

# ================== 浏览器启动器 ==================
class BrowserLauncher:
    """浏览器启动器，支持多种启动模式"""
    # 默认浏览器路径和用户数据目录
    DEFAULT_PATHS = {
        'edge': {
            'browser_path': r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        },
        'chrome': {
            'browser_path': r"C:\Program Files\Google\Chrome\Application\chrome.exe",  
        }
    }

    @staticmethod
    def kill_browser_process(browser_type='edge'):
        """强制结束浏览器进程"""
        if browser_type == 'edge':
            subprocess.run(['taskkill', '/F', '/IM', 'msedge.exe'], capture_output=True)
        elif browser_type == 'chrome':
            subprocess.run(['taskkill', '/F', '/IM', 'chrome.exe'], capture_output=True)

    @classmethod
    def launch_with_user_data(cls, browser_type='edge', browser_path=None, user_data_dir=None):
        """模式1: 自动启动浏览器, 使用指定用户数据目录"""
        # 如果未传入浏览器路径，尝试使用默认路径
        if browser_path is None:
            browser_path = cls.DEFAULT_PATHS[browser_type]['browser_path']
        if user_data_dir is None:
            raise ValueError("错误: 未输入用户数据目录")

        # 如果用户使用默认数据目录，终止当前正在运行的 Edge 实例以避免冲突
        if user_data_dir == utils.get_edge_user_data_dir():
            print("尝试结束当前 Edge 实例")
            cls.kill_browser_process(browser_type)
        time.sleep(2)

        print(f"正在启动 {browser_type.capitalize()} 浏览器...")
        co = ChromiumOptions()
        co.set_local_port(9444)
        co.set_user_data_path(user_data_dir)
        co.set_browser_path(browser_path)
        page = ChromiumPage(co)
        print(f"{browser_type.capitalize()} 浏览器已启动")
        return page

    @classmethod
    def launch_incognito(cls, browser_path: str):
        """
        模式2: 无痕模式启动浏览器\n
        :param browser_type: 浏览器类型
        :param login_callback: 登录回调函数
        """
        #incognito_args = []
        # edge 和 chrome 的无痕启动参数是不同的
        """if browser_type == 'edge':
            incognito_args = ['--inprivate']
        elif browser_type == 'chrome':
            incognito_args = ['--incognito']"""

        # 使用临时用户数据目录（无痕模式会自动创建临时目录）
        if browser_path is None:
            raise ValueError("错误：浏览器路径为空")
        co = ChromiumOptions()
        co.set_local_port(9445)
        co.set_browser_path(browser_path)
        co.set_argument('--new-window')
        co.incognito(True)
        """for arg in incognito_args:
            co.set_argument(arg)"""

        page = ChromiumPage(co)
        return page

    @classmethod
    def connect_to_existing(cls, port=9222):
        """模式3: 连接到已启动的调试端口浏览器"""
        print(f"正在连接到调试端口为 {port} 的浏览器...")
        co = ChromiumOptions()
        co.set_local_port(port)
        page = ChromiumPage(co)
        return page

# ================== 课程页面操作处理器 ==================
class CoursePageHandler:
    @staticmethod
    def expand_all_chapters(page: ChromiumPage):
        """展开所有章节"""
        js = f"""
            var chapters = document.querySelectorAll('{PageConfig.CHAPTER_TITLE}');
            for (var i = 0; i < chapters.length; i++) {{
                chapters[i].click();
            }}
            return chapters.length;
        """
        count = page.run_js(js)
        print(f"已尝试展开 {count} 个章节")
        time.sleep(0.5)

    @staticmethod
    def get_chapter_video_counts(page: ChromiumPage) -> list:
        """获取每个章节下的视频数量，返回列表"""
        js = f"""
            var cellsList = document.querySelectorAll('{PageConfig.CHAPTER_CONTAINER}');
            var counts = [];
            for (var i = 0; i < cellsList.length; i++) {{
                var videoLinks = cellsList[i].querySelectorAll('{PageConfig.VIDEO_LINK_CONTAINER}');
                counts.push(videoLinks.length);
            }}
            return counts;
        """
        return page.run_js(js)

    @staticmethod
    def click_by_index(page: ChromiumPage, chap_index, video_index):
        """点击指定章节下的指定内容"""
        js = f"""
            var cellsList = document.querySelectorAll('{PageConfig.CHAPTER_CONTAINER}');
            if (cellsList.length < {chap_index}) return false;
            var targetCells = cellsList[{chap_index - 1}];
            var videoLinks = targetCells.querySelectorAll('{PageConfig.VIDEO_LINK_CONTAINER}');
            if (videoLinks.length < {video_index}) return false;
            var targetLink = videoLinks[{video_index - 1}];
            targetLink.click();
            return true;
        """
        return page.run_js(js)
    
    @staticmethod
    def is_video(page: ChromiumPage):
        """判断是否为视频"""
        if page.ele(f"css:{PageConfig.IS_VIDEO}",timeout=3):
            return True
        else:
            return False

    @staticmethod
    def is_video_completed(page: ChromiumPage) -> bool:
        """
        判断当前视频是否已完成\n
        方法为检查是否存在“任务点已完成”字样
        """
        icon = page.ele(PageConfig.COMPLETED_ICON, timeout=2)
        if icon:
            aria_label = icon.attr('aria-label')
            return aria_label == "任务点已完成"
        return False

    @staticmethod
    def click_play_button(page: ChromiumPage, log_callback: function):
        """
        确保视频开始播放。如果已播放则无操作，否则点击播放按钮。
        返回 True 表示视频已可播放，False 表示无法处理。
        """
        try:
            # 1. 先判断视频是否正在播放（.vjs-paused 存在表示暂停，不存在表示播放中）
            is_paused = page.ele(f"css:{PageConfig.IS_PAUSED}", timeout=4)
            if not is_paused:
                log_callback("视频已在播放中")
                return True

            # 2. 视频处于暂停状态（包括未开始）
            play_status = page.ele(f"css:{PageConfig.PLAY_STATUS}")
            if play_status:
                play_button = page.ele(f"css:{PageConfig.PLAY_PAUSE_CONTROL}")
                action_msg = "恢复播放"
            else:
                play_button = page.ele(f"css:{PageConfig.PLAY_BUTTON}", timeout=5)
                action_msg = "开始播放"

            if play_button:
                play_button.click()
                log_callback(action_msg)
                return True
            else:
                log_callback("错误：未找到播放按钮")
                return False
        except Exception as e:
            log_callback(f"点击播放按钮异常: {e}")
            return False

    @staticmethod
    def start_playback_monitor(page: ChromiumPage, log_callback: function, monitor_interval=50):
        """
        启动循环监控器，每隔一段事件检测视频是否暂停，若暂停则恢复播放\n
        :param page: **已经访问**视频页面的Chromiumpage对象
        :param monitor_interval: 检查时间间隔
        :return monitor_thread, stop_event: 监控线程, 停止事件
        """
        stop_event = threading.Event()
        def monitor():
            while not stop_event.is_set():
                try:
                    is_paused = page.ele(f"css:{PageConfig.IS_PAUSED}", timeout=4)
                    if is_paused:
                        CoursePageHandler.click_play_button(page, log_callback)
                except Exception as e:
                    log_callback(f"监控器异常: {e}")
                # 分段睡眠以便及时响应停止事件
                for _ in range(monitor_interval):
                    if stop_event.is_set():
                        break
                    time.sleep(0.1)
        monitor_thread = threading.Thread(
            target=monitor,
            name="MonitorThread",
            daemon=True
        )
        monitor_thread.start()
        return monitor_thread, stop_event

    @staticmethod
    def listen_complete_image(page: ChromiumPage, log_callack: function, timeout=3600) -> bool:
        """监听器：监听任务完成图片，成功截获则设置结束事件"""
        #print("等待视频完成图片...")
        page.listen.start(PageConfig.COMPLETE_IMAGE_KEYWORD)
        packet = page.listen.wait(count=1, timeout=timeout)
        page.listen.stop()
        if packet and PageConfig.COMPLETE_IMAGE_KEYWORD in packet.url:
            log_callack(f"检测到视频完成图片: {packet.url}")
            return True
        else:
            log_callack("等待超时，未收到完成图片")
            return False

# ================== 主业务函数 ==================
def run_video_task(page: ChromiumPage, handler: CoursePageHandler, log_callback: function=None):
    """
    执行挂课任务的主函数\n
    :param page: **已经访问**课程链接的ChromiumPage对象,
    :param handler: 页面处理器，负责视频翻页，播放视频等页面操作,
    """
    # 日志回调/直接输出到控制台
    def log(msg):
        if log_callback is not None:
            log_callback(msg)
        else:
            print(msg)
    
    # 获取视频数量
    video_counts = handler.get_chapter_video_counts(page)
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
        if not handler.click_by_index(page, chap_idx, vid_idx):
            log(f"点击视频失败，将重新加入队列")
            tasks.append((chap_idx, vid_idx))
            time.sleep(2)
            continue

        time.sleep(4)  # 等待页面刷新

        # 检查是否为视频
        if not handler.is_video(page):
            log("当前章节不是视频，跳过")
            continue

        # 检查是否已完成
        if handler.is_video_completed(page):
            log("该视频已完成，跳过")
            continue

        # 开始播放
        handler.click_play_button(page, log)

        # 获取监控线程
        monitor_thread, stop_event = handler.start_playback_monitor(page,log)
        
        # 等待完成图片
        success = handler.listen_complete_image(page,log)

        # 停止监控
        stop_event.set()
        monitor_thread.join()

        if not success:
            log("视频播放超时，等待进行重试")
            tasks.append((chap_idx, vid_idx))
        else:
            continue

        time.sleep(1)

    log("\n所有视频已处理完毕")
    if log_callback is None:
        input("按回车退出...")

if __name__ == "__main__":
    page = BrowserLauncher.launch_with_user_data(
        browser_type='edge',
        browser_path=utils.get_edge_path(),
        user_data_dir=utils.get_edge_user_data_dir()
        )
    url = input("输入课程链接")
    page.get(url)
    handler = CoursePageHandler()
    run_video_task(page, CoursePageHandler)