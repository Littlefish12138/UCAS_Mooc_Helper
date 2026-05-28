"""
视频自动播放与翻页脚本（重构版）
功能：
1. 使用CDP协议控制浏览器, 无需drivers.exe. 支持多种浏览器启动模式( Edge/Chrome, 普通/无痕, 或连接已启动的调试端口 )
2. 提供类 CourseHandler , 类中提供方法
    - finish_video 完成单个视频任务的挂课。监控视频暂停并自动恢复播放。
    - finish_question 完成单个视频的章节测试题
    - run_course_task 根据要求完成所有视频的挂课/章节测试题
    - get_all_questions 抓取所有课程的章节测试题
3. 使用双端队列管理任务，失败自动重试
类 CourseHandler 的交互逻辑和判断逻辑暂时是按照**2025-2026秋季大一军事理论课**设计的. 
未来如果交互逻辑改变, 需要修改代码. 如果 html 中的某些属性改变, 只需修改配置文件中对应的 locator 即可
"""

import time
import concurrent.futures
from collections import deque
from copy import deepcopy
import logging

from DrissionPage import ChromiumPage

from _locator import ElementLocator


# ================== 报错类 ==================
class AnswerNotFoundError(Exception):
    def __init__(self, data):
        self.data = data
        super().__init__(f"答案中没有 data 为{data}的试题")

class AnswerMisMatchError(Exception):
    def __init__(self, data, is_stem_match=True, is_option_match=True):
        self.data = data
        self.stem_match = "题干不匹配" if not is_stem_match else ""
        self.option_match = "选项不匹配" if not is_option_match else ""
        super().__init__(f"data: {data}的试题{self.stem_match}{self.option_match}")

# ================== 配置区域（选择器、关键字等） ==================
class PageConfig:
    """页面元素选择器和请求关键字配置"""
    def __init__(self):
        # 任务完成图片请求关键字
        self.COMPLETE_IMAGE_KEYWORD = "job-status-new-complete"

        # 监听超时时间，应当设置为比所有视频的最长时间长
        self.LISTEN_TIMEOUT = 3600

        # 加载超时时间
        self.LOAD_TIMEOUT = 10
        # 查找超时时间
        self.LOCATOR_TIMEOUT = 1
        # 页面交互等待时间
        self.PAGE_LOAD_TIME = 2
    
# ================== 课程页面操作处理器 ==================
class CourseHandler:
    def __init__(self, page: ChromiumPage,
                  elem_config: dict,
                    *,
                    logger: logging.Logger = None,
                    answers: dict = {},
                    page_config: PageConfig):
        self.page = page

        self.logger = logger if logger else logging.getLogger(__name__)
        self._config = page_config
        
        self._answers = deepcopy(answers) if answers else None
        self._elem_locator = ElementLocator(self.page,elem_config, 
                                            logger=logger, 
                                            load_timeout=self._config.LOAD_TIMEOUT, 
                                            locate_timeout=self._config.LOCATOR_TIMEOUT)

        self._course_tree = self._elem_locator.extract_info({'chapter_title','section_title','section_click_callback_js','is_section_finished'})


    def _listen_complete_image(self) -> bool:
        """
        监听器: 监听任务完成图片, 成功截获则返回True, 超时时间默认3600s
        """
        self.logger.info("开始等待视频完成图片...")
        self.page.listen.start(self._config.COMPLETE_IMAGE_KEYWORD)
        packet = self.page.listen.wait(count=1, timeout=self._config.LISTEN_TIMEOUT)
        self.page.listen.stop()
        if packet and self._config.COMPLETE_IMAGE_KEYWORD in packet.url:
            self.logger.info(f"检测到视频完成图片: {packet.url}")
            return True
        else:
            self.logger.warning(f"等待{self._config.LISTEN_TIMEOUT}秒未收到完成图片, 认定为超时")
            return False
    
    def _click_button(self, button_name) -> bool:
        """
        针对页面上的无树形结构按钮的点击方法, 处理: 
         - 视频播放区域按钮(video_button)、章节测试区域按钮(question_button)
         - 章节测试提交按钮(submit_button)、章节测试确认提交按钮(confirm_button)
        注意：这里事实上要求**这些按钮的 target name 和其键名保持一致, 并且这些键都在结果的第一层**
         
        :param button_name: 按钮的targets名
        """
        result = self._elem_locator.extract_info({button_name})
        btn_callback = result.get(button_name)
        if btn_callback:
            self.logger.info(f"尝试点击按钮, 按钮名{button_name}")
            btn_callback()
            return True
        else:
            self.logger.critical(f"错误: 无目标按钮点击回调, 按钮名称{button_name}, 提取结果{result}")
            return False

    def _get_video_status(self) -> bool | None:
        """获取视频状态, 没有视频返回None, 有视频按照任务点是否完成返回True/False"""
        self.logger.info("尝试获取本章节视频状态")

        result = self._elem_locator.extract_info({'video_play'})
        
        video_dict: dict = result.get('video')[0]
        if video_dict.get('video_play') is None:
            return None
        else:
            result = self._elem_locator.extract_info({'is_video_finished'})
            video_dict: dict = result.get('video')[0]
            return video_dict.get('is_finished') 

    def _generate_task(self, only_unfinished = False, video_needed = True, question_needed = False) -> deque:
        """
        生成一个deque队列, 包含将符合条件的课程任务, 每个课程任务是一个字典, 包含键：
         - 'click_callback'存有点击回调
         - 'section_title'存有节标题
         - 'video_finished'视频是否完成, 不需要时为 None
         - 'question_finished'章节测试题是否完成, 不需要时为 None\n
        :param only_unfinished: 是否仅提取未完成的(视频和章节测试题有一个未完成即视为未完成)
        """
        tasks = deque()
        chapters: list[dict] = self._course_tree.get('chapters')
        if chapters:
            for chapter in chapters:
                sections: list[dict] = chapter.get('sections')
                if sections:
                    for section in sections:
                        click_callback_js = section.get('click_callback_js')
                        title = section.get('title')
                        title_cleaned = title.replace('\n', '').replace('\t', '') # 有些时候\n\t似乎不会正常去除掉, 不知道为什么
                        is_finished = section.get('is_finished')
                        if only_unfinished and is_finished:
                            continue
                        task = {'click_callback':click_callback_js,
                                'title':title_cleaned}
                        task['video_finished'] = False if video_needed else None
                        task['question_finished'] = False if question_needed else None
                        tasks.append(task)
        return tasks

    # ================== 主业务函数 ==================
    def finish_video(self) -> bool | None:
        """
        挂一个视频，返回是超时还是正常完成, 保持视频的播放状态\n
        :param video_play: 视频的播放方法
        """
        result = self._elem_locator.extract_info({'video_play'})
        video_list: list | None = result.get('video')
        if video_list:
            try:
                video_play = video_list[0].get('video_play')
                if not callable(video_play):
                    self.logger.critical(f"似乎获取到了无效的播放回调, 提取结果{result}")
                    return
            except Exception as e:
                self.logger.critical(f"获取视频播放回调时出现异常{e}, 提取结果为{result}")
                return
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(self._listen_complete_image)
                
                # 主线程负责保持播放
                while not future.done():
                    video_play()
                    time.sleep(1)
                
                return future.result()
        else:
            self.logger.critical(f"尝试提取视频播放回调时出现异常: 似乎没有任何结果. 提取结果: {result}")
            return

    def finish_questions(self):
        """
        完成一个视频的章节测试题, 若已完成则跳过并返回None, 提取失败则中断并返回False, 答案异常则报错, 正常作答完成则返回True
        """
        try:
            result = self._elem_locator.extract_info({'is_question_finished'})
            is_finished = result.get('is_finished')
            if is_finished is True:
                self.logger.info("章节测试题已完成, 跳过")
                return None
            elif is_finished is False:
                questions: dict = self._elem_locator.extract_info({'data','stem','option_content','option_click_callback','judgement'})
                self.logger.debug(f"章节测试部分提取结果{questions}")
            else:
                self.logger.critical(f"提取完成状态时结果异常: 提取结果{result}")
                return False
        except Exception as e:
            self.logger.critical(f"提取课程的章节测试题时出错: {e}")
            return False

        self.logger.info("开始完成章节测试题")
        question_list = questions.get('questions')
        for question in question_list:
            data = question['data']
            answer: dict = self._answers.get(data)
            if answer is not None:
                options: list = question['options']
                # ===================== 检查逻辑 =====================
                # 题干是否一致
                stem: str = question['stem']
                if answer.get('question') and stem != answer['question']:
                    self.logger.warning(f"章节测试题的对应答案似乎不匹配: 预期题干为{answer['question']}, 实际题干为{answer.get("question")}\n")
                    raise AnswerMisMatchError(data,is_option_match=False)
                # 选项是否一致
                if stem.startswith(('【单选题】','【多选题】')):
                    contents = set()
                    for option in options:
                        contents.add(option['content'])
                    if answer.get('options') and set(answer['options']) != contents:
                        self.logger.warning(f"章节测试题的对应答案似乎不匹配: 预期选项为{answer.get("options")}, 实际选项为{set(contents)}\n")
                        raise AnswerMisMatchError(data,is_option_match=False)
                
                # ===================== 通过点击以做题 =====================
                try:
                    if stem.startswith(('【单选题】','【多选题】')):
                        for option in options:
                            if option['content'] in answer['answer']:
                                click_callback = option['click_callback']
                                click_callback()
                    elif stem.startswith('【判断题】'):
                        for option in options:
                            if option['judgement'] == answer['answer']:
                                click_callback = option['click_callback']
                                click_callback()
                except Exception as e:
                    self.logger.critical(f"尝试点击选项时异常: \n 试题data: {data}\n 题干: {stem}\n 异常选项:{locals().get('option', 'N/A')}\n")
                    return False
            else:
                self.logger.warning(f"错误: 答案缺失, 答案中没有data为{data}的试题")
                raise AnswerNotFoundError(data)
        time.sleep(self._config.PAGE_LOAD_TIME)
        return True


    def submit_answers(self):
        """提交答案"""
        self.logger.info("尝试提交结果")
        try:
            self._click_button('submit_button')
            time.sleep(self._config.PAGE_LOAD_TIME)
            self._click_button('confirm_button')
            time.sleep(self._config.PAGE_LOAD_TIME)
        except Exception as e:
            self.logger.critical(f"尝试提交时出错：{e}")
            return False
        
    def run_course_task(self, only_unfinished = True, video_needed = True, question_needed = True) -> list:
        """
        按照要求完成全部任务，返回失败列表\n
        """
        
        tasks = self._generate_task(only_unfinished, video_needed, question_needed)
        failed_list = []

        task_index = 1
        task_length = len(tasks)

        self.logger.info(f"总任务数: {task_length}")
        self.logger.info("开始处理任务队列...\n")
        # 处理任务
        while tasks:
            task: dict = tasks.popleft()

            self.logger.info(f"\n正在处理第{task_index}/{task_length}个任务： {task['title']}...")

            try:
                # 点击视频，跳转到对应页面
                self.logger.info("跳转到对应页面...")
                task.get('click_callback')()
                time.sleep(self._config.PAGE_LOAD_TIME)
                # 完成视频
                video_status = self._get_video_status()

                if video_status is None:
                    self.logger.info("当前章节不是视频, 跳过")
                    task_index += 1
                    continue
                
                if video_needed and (video_status is False):
                    self.logger.info("开始播放视频")
                    success = self.finish_video()
                    if success:
                        self.logger.info(f"章节{task['title']}的视频播放完成")
                    else:
                        self.logger.warning(f"播放章节{task['title']}的视频时出现错误，未完成，重新添加到队列中")
                        tasks.append(task)
                        continue
                else:
                    self.logger.info("当前章节的视频已经播放完成")
                    
                if question_needed:
                    self.logger.info("切换到章节测试区域")
                    self._click_button('question_button')
                    time.sleep(self._config.PAGE_LOAD_TIME)
                    try:
                        self.logger.info("开始处理章节测试区域")
                        finish_msg = self.finish_questions()
                        if finish_msg is True:
                            self.logger.info("开始提交章节测试")
                            self.submit_answers()
                        elif finish_msg is False:
                            tasks.append(task)
                    except (AnswerMisMatchError, AnswerNotFoundError) as e:
                        self.logger.warning(f"\n完成课程{task['title']}的章节测试题时出错: {e}, 不可恢复错误")
                        failed_list.append(task['title'])
                        task_index += 1
                        continue
                    except Exception as e:
                        self.logger.critical(f"\n完成章节{task['title']}的章节测试题时出现未知错误: {e}, 重新添加到队列中")
                        tasks.append(task)
                task_index += 1
            except Exception as e:
                self.logger.critical(f"完成章节{task['title']}时出现错误{e}，重新添加到队列中")
                tasks.append(task)
            
            

        self.logger.info(f"\n所有视频已处理完毕, 失败任务数{len(failed_list)}")
        if failed_list:
            self.logger.warning(f"失败任务: {failed_list}")
                
        return failed_list

    def get_all_questions(self, only_unfinished: bool = True) -> dict:
        """
        提取满足条件的章节测试题, 以json形式返回\n
        :param only_unfinieded: 是否仅提取未完成的
        """
        tasks = self._generate_task(only_unfinished,video_needed=False,question_needed=True)

        task_index = 1
        task_length = len(tasks)
        question_dict = {}

        self.logger.info(f"总任务数: {task_length}")
        self.logger.info("开始提取章节测试题...\n")

        while tasks:
            task: dict = tasks.popleft()
            
            self.logger.info(f"\n正在处理第{task_index}/{task_length}个任务： {task['title']}...")
            
            try:
                # 点击视频，跳转到对应页面
                self.logger.info("跳转到对应页面...")
                task.get('click_callback')()
                time.sleep(self._config.PAGE_LOAD_TIME)

                # 检查是否为视频
                if self._get_video_status() is None:
                    self.logger.info("当前章节不是视频, 应当没有章节测试题, 跳过")
                    task_index += 1
                    continue
                
                self.logger.info("切换到章节测试区域")
                self._click_button('question_button')
                time.sleep(self._config.PAGE_LOAD_TIME)

                result = self._elem_locator.extract_info({'data','stem','options','name_and_content','my_answer','is_answer_correct'})
                
                self.logger.info(f"提取结束, 结果为: \n{result}\n")
                
                questions = result.get('questions')
                for question in questions:
                    data = question['data']
                    question_dict[data] = question

                task_index += 1

            except Exception as e:
                self.logger.warning(f"处理视频{task['title']}时出错: {e}，回到队列等待重试")
                tasks.append(task)

        self.logger.info("\n所有视频的章节测试题读取完成")

        return question_dict
