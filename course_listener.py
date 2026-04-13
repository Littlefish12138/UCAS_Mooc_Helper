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
import os
import json
from DrissionPage import ChromiumPage, ChromiumOptions
from DrissionPage._elements.chromium_element import ChromiumElement

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
    # 查找页面上 任务点是否完成 对应字样的查找方式，用class以ans-job-icon开头来查找
    IS_COMPLETED = '^ans-job-icon'

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


    # 章节测验按钮的selector
    QUESTION_BUTTON = '#dct2'
    # 章节测验按钮被点击时，对应的属性名
    CLICK_STATUS = 'class'
    CLICKED = 'c2 currents'

    # 试题容器class名
    QUESTION_BOX = ".TiMu singleQuesId"
    # 题干容器class属性名
    STEM_BOX = 'Zy_TItle clearfix'
    # 题干的查找方式，容器class 名为clearfix 或者是 clearfix font-cxsecret fontLabel
    STEM_TEXT = '.^clearfix'
    # 选项的查找方式，找href属性 已完成时为 javascript:void(0) 未完成时后面多一个分号
    OPTIONS_HREF = '^javascript:void(0)'
    # 已经回答的问题，答案容器class属性名
    ANSWER_BOX = 'Py_answer clearfix'
    MY_ANSWER = 'span' # 我的答案位置
    IS_CORRECT_BOX = '.^fr'   # 答案是否正确，对应的class属性，正确时为 fr dui，错误时为 fr cuo
    IS_CORRECT = 'aria-label' # 答案是否正确，对应字符串的属性名

    # 提交答案按钮的selector
    SUBMIT_BUTTON = '#RightCon > div.radiusBG > div > div.ZY_sub.clearfix > a.Btn_blue_1.marleft10.workBtnIndex'
    # 确定提交答案按钮的selector
    CONFIRM_BUTTON = '#confirmSubWin > div > div > a.bluebtn'

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
    def is_completed(page: ChromiumPage) -> bool | None:
        """
        判断当前视频是否播放完成/章节测试题是否已提交\n
        方法为检查是否存在“任务点已完成”字样
        """
        icon = page.ele(f".{PageConfig.IS_COMPLETED}", timeout=2)
        if icon:
            aria_label = icon.attr('aria-label')
            return aria_label == "任务点已完成"
        return None

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
    
    @staticmethod
    def get_to_questions(page: ChromiumPage, log_callback: function=print):
        """
        通过点击，切换到章节测试的部分，返回是否点击成功
        """
        ques_btn = page.ele(f"css:{PageConfig.QUESTION_BUTTON}")
        if ques_btn:
            if ques_btn.attr(f"{PageConfig.CLICK_STATUS}") == PageConfig.CLICKED:
                log_callback("已经在章节测试当中")
                return True
            else:
                log_callback("点击章节测试按钮")
                ques_btn.click()
                return True
        else:
            log_callback("错误：未找到章节测试对应的按钮")
            return False
        
    @staticmethod
    def get_stem(question_box: ChromiumElement, log_callback: function) -> str:
        """
        获取题干字符串\n
        """
        child = question_box.child(f".{PageConfig.STEM_BOX}")
        stem_elem = child.ele(PageConfig.STEM_TEXT)   # 获取题干容器
        if stem_elem:
            stem_text = stem_elem.text
            if stem_text:
                return stem_text
            else:
                log_callback("错误：题干内容为空")
        else:
            log_callback("错误：题干元素不存在或class属性变化")
            return None
    
    @staticmethod
    def get_options(question_box: ChromiumElement, log_callback: function) -> list:
        """获取选项内容,将每个选项内容(不包含选项名A,B,C,D)以列表形式返回"""
        options_list = []
        option_elems = question_box.eles(f"@href{PageConfig.OPTIONS_HREF}") # 获取选项元素列表
        if option_elems:
            for option_elem in option_elems:
                option_elem: ChromiumElement
                options_list.append(option_elem.text)
        else:
            log_callback("错误：选项元素不存在或href属性变化")
        return options_list
    
    @staticmethod
    def get_answer_and_result(question_box: ChromiumElement, log_callback: function) -> tuple[str | None,bool | None]:
        """
        获取我的回答及答案是否正确\n
        返回的str是选项名'CD'或者'对'/'错'
        """
        answer_box, is_correct = None, None
        answer_box = question_box.child(f".{PageConfig.ANSWER_BOX}")
        if answer_box:
            # 获取回答字符串，预期格式为  我的答案：A
            # 获取之后将冒号连同之前的东西全部去掉
            my_answer: str = answer_box.child(f"css:{PageConfig.MY_ANSWER}").text
            my_answer = my_answer.split("：", 1)[1].strip()


            correct_elem = answer_box.child(f"{PageConfig.IS_CORRECT_BOX}")
            if correct_elem:
                is_correct = (correct_elem.attr(PageConfig.IS_CORRECT) == '答案正确')
            else:
                log_callback("错误：未找到是否正确对应的元素")
        else:
            log_callback("错误：我的回答元素不存在或class属性变化")
        
        return my_answer, is_correct

        
    @staticmethod
    def get_questions(page: ChromiumPage, log_callback: function) -> list[dict]:
        """
        获取一个页面中的试题和答案，\n
        :param page: 要求已经访问章节测试页面
        :return: 试题字典列表，每个试题字典格式\n
                {
                    "question": 题干文本,字符串
                    "data": 题目的 data
                    "options": [选项列表],选项形如字符串'军队'(不带选项名), 判断题此项为None
                    "my_answer": 为选择题则为选择的选项字符串列表(不带选项名)，为判断题则为字符串
                    "is_correct": 已回答则为bool值, 未回答则为None
                }
        """
        question_list = []
        # 获取所有题目容器
        question_boxes = page.eles(PageConfig.QUESTION_BOX)
        for idx, q_box in enumerate(question_boxes, start=1):
            q_box: ChromiumElement
            log_callback(f"正在解析第 {idx} 题...")
            question_info = {
                "question": "",
                "data": 0, 
                "options": None,
                "my_answer": None,
                "is_correct": None,
            }

            question_str = CoursePageHandler.get_stem(q_box,log_callback)
            question_info['question'] = question_str

            question_info['data'] = q_box.attr("data") if q_box.attr("data") else None
            
            is_mcq = question_str.startswith(("【单选题】","【多选题】")) # 判断是否为选择题           

            if is_mcq:
                options = CoursePageHandler.get_options(q_box,log_callback)
                question_info["options"] = options

            if CoursePageHandler.is_completed(page) is True:
                my_answer, is_correct = CoursePageHandler.get_answer_and_result(q_box,log_callback)

                question_info["is_correct"] = is_correct
                if is_mcq:  # 选择题将选项字母替换成对应的选项内容
                    my_answer_list = []
                    for _ in my_answer:
                        my_answer_list.append(options[ord(_) - ord('A')])
                    question_info["my_answer"] = my_answer_list
                else:
                    question_info["my_answer"] = my_answer
                    
            question_list.append(question_info)
        
        return question_list

    @staticmethod
    def answer_questions(page: ChromiumPage,answer: list[dict]):
        """
        回答试题，要求输入已经访问对应视频页面的ChromiumPage对象\n
        :param answer: 试题答案，要求为列表
        """
        pass

    @staticmethod
    def submit_answers(page: ChromiumPage, log_callback: function):
        """
        提交答案
        """
        submit_button = page.ele(f"css:{PageConfig.SUBMIT_BUTTON}")
        if submit_button:
            submit_button.click()
            time.sleep(2)
            confirm_button = page.ele(f"css:{PageConfig.CONFIRM_BUTTON}")
            if confirm_button:
                confirm_button.click()
                log_callback("已经提交答案")
                return True
            else:
                log_callback("错误：未找到确定提交按钮")
                return False
        else:
            log_callback("错误，未找到提交按钮")
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
        if handler.is_completed(page):
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

def save_all_questions(page: ChromiumPage, handler: CoursePageHandler, log_callback: function):
    """
    遍历所有视频的章节测试，以字典形式保存所有试题\n
    键是data，值是题目信息，包括题干，选项等等
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

    question_dict_list = []

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

        if not handler.is_video(page):
            log("当前章节不是视频，跳过")
            continue

        handler.get_to_questions(page,log)
        question_dict_list.extend(handler.get_questions(page,log))
    
    question_dict = {d["data"]: d for d in question_dict_list}

    base_dir = os.path.abspath(os.path.dirname(__file__))
    output_dir = os.path.join(base_dir,'_questions','军事理论20260411.json')
    os.makedirs(os.path.dirname(output_dir), exist_ok=True)

    with open(output_dir, 'w', encoding='utf-8') as f:
        json.dump(question_dict, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":

    page = BrowserLauncher.launch_with_user_data(user_data_dir=utils.get_edge_user_data_dir())
    page.get("https://mooc.mooc.ucas.edu.cn/mooc-ans/mycourse/studentstudy?chapterId=577472&courseId=350140000037227&clazzid=350140000031973&enc=f1220c4fcaa1db6d27eefea233837606")
    handler = CoursePageHandler()
    save_all_questions(page,handler,print)