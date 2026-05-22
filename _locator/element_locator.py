import time
import copy
from functools import partial
from typing import Any
from dataclasses import dataclass

from DrissionPage import ChromiumPage

@dataclass
class StackFrame:
    name: str        # 容器键名(如 "questions", "options" 或 "$root")
    ref: dict        # 指向实际数据容器（dict 或 list）

class ContainerStack:
    def __init__(self, root_dict: dict):
        # 初始化时，栈底永远有 $root 帧
        self._stack = [StackFrame(name="$root", ref=root_dict)]

    def push(self, name: str, ref):
        """压入新的容器帧"""
        self._stack.append(StackFrame(name=name, ref=ref))

    def pop(self) -> StackFrame:
        """弹出栈顶帧（不允许弹出 $root）"""
        if len(self._stack) == 1:
            raise RuntimeError("错误使用：禁止弹出根栈")
        return self._stack.pop()

    @property
    def top(self) -> StackFrame:
        """查看栈顶帧"""
        return self._stack[-1]

    def resolve(self, container: str) -> Any:
        """根据 container 字段解析目标容器"""
        if container == "$root":
            return self._stack[0].ref          # 栈底
        if container == "$parent":
            return self.top.ref                # 栈顶

        # 按名称从栈顶向下查找
        for frame in reversed(self._stack):
            if frame.name == container:
                return frame.ref
        raise KeyError(f"Container '{container}' not found in context stack")

class ElementStack:
    def __init__(self, root: ChromiumPage):
        # 初始化时，栈底永远有 $root 帧
        self._stack = [root]

    def push(self, element):
        """压入新的容器帧"""
        self._stack.append(element)

    def pop(self):
        """弹出栈顶帧（不允许弹出 $root）"""
        if len(self._stack) == 1:
            raise RuntimeError("错误使用：禁止弹出根栈")
        return self._stack.pop()
    
    def top(self):
        """查看栈顶帧"""
        return self._stack[-1]

class ElementLocator:
    """
    元素定位、获取信息\n
    """
    def __init__(self, page: ChromiumPage, config: dict, load_timeout = 10, locate_timeout = 1):
        """
        :param config: 页面配置(字典)
        :param load_timeout: 等待iframe元素加载的超时时间
        :param locate_timeout: 查找超时时间
        """
        self.page = page
        self._config = copy.deepcopy(config)
        self._load_timeout = load_timeout
        self._locate_timeout = locate_timeout
        self._result = {}
        self._element_stack = ElementStack(self.page)
        self._container_stack = ContainerStack(self._result)
    
    @staticmethod
    def _prune_sublevel(level: dict, required_set: set = None) -> (dict | None):
        """
        对输入的 字典/子字典 剪枝, 返回新的字典，其职能为：
         - 剪去不含目标 target 的 sub_element
         - 剪去 targets 中不需要的 target\n
        :param node: 输入需要处理的字典，或者在递归过程中输入某个子字典
        :param required_set: 目标集合，不输则把所有带有 targets 的剪出来
        :return: 该节点剪枝后保留的元素，如果全部剪掉则返回 None
        """
        _pruned_subtree = {}

        for elem_name, elem in level.items():
            elem: dict
            self_needed = False

            has_targets = True if elem.get('targets') else False
            has_sub_elements = True if elem.get('sub_elements') else False
            
            _pruned_targets = {}
            _pruned_sub_elements = {}

            # 1. 修剪当前的 targets 字典，并将结果存入 _pruned_targets 中
            
            if required_set is None:
                self_needed = has_targets
            elif has_targets:
                targets: dict = elem.get('targets')
                for target_name, target in targets.items():
                    if target_name in required_set:
                        self_needed = True
                        _pruned_targets[target_name] = target
            
            # 2. 修建当前的 sub_elements 字典，并将结果存入 _pruned_sub_elements 中

            if has_sub_elements:
                sub_elements: dict = elem.get('sub_elements')
                _pruned_sub_elements = ElementLocator._prune_sublevel(sub_elements,required_set)
                if _pruned_sub_elements is not None:
                    self_needed = True

            # 3.进行决断：保留还是丢弃

            if self_needed:
                new_elem = copy.deepcopy(elem)
                if has_targets and _pruned_targets:
                    new_elem['targets'] = _pruned_targets
                else:
                    # 分支：原先没有 targets 或者 原先的 targets 全部不需要
                    new_elem.pop("targets",None)
                if has_sub_elements and _pruned_sub_elements:
                    new_elem['sub_elements'] = _pruned_sub_elements
                else:
                    # 分支：原先没有 sub_elements 或者 原先的 sub_elements 全部不需要
                    new_elem.pop('sub_elements',None)
                _pruned_subtree[elem_name] = new_elem

        if _pruned_subtree == {}:
            return None
        else:
            return _pruned_subtree
    
    @staticmethod
    def _get_target(element, target: dict):
        """
        根据 target 字典(不是targets字典)从对应的 ChromiumElement 元素中提取对应的信息或者方法\n
        getattr失败, 执行调用某个元素交互函数失败将报错\n
        :return: 字符串或者回调函数
        """
        if target['type'] == 'method':
            method_name = target['method']
            args = target.get('args')

            method = getattr(element, method_name)
            if args:
                return partial(method, args)
            else:
                return method
                
        elif target['type'] == 'information':
            val: dict = target['value']
            method_name = val['method']
            args = val.get('args')

            method = getattr(element, method_name)
            # 针对配置中的 method 是属性或者方法采取不同处理
            if callable(method):
                if args:
                    target_info = method(args)
                else:
                    target_info = method()
            else:
                target_info = method
            return target_info

    @staticmethod
    def _locate_elements(element, locator_method: dict, locate_timeout, load_timeout) -> list:
        """
        使用 locator_method 字典中的查找方法，从 element 的子元素中找出目标元素\n
        如果使用get_frame(s)查找, 就会等待其加载完毕(读取其 contentDocument 的 readyState 以判定其是否加载完毕)\n
        返回符合目标的元素列表，没有返回空列表
        """
        
        method_types: list = locator_method['type']
        locators: list = locator_method['locator']

        is_repeatable = bool(set(method_types) & {'eles','children','s_eles','get_frames'})
        is_iframe = bool(set(method_types) & {'get_frame','get_iframe'}) 
        is_exist = False

        for method_type in method_types:
            if not is_exist:
                method = getattr(element, method_type)
                for locator in locators:
                    child_elements = method(locator, timeout = locate_timeout)
                    if child_elements:
                        is_exist = True
                        break
            else:
                break
        
        results: list = child_elements if is_repeatable else [child_elements]
        
        if is_exist:
            if is_iframe:
                # 若为 iframe(s) 则进行等待所有的 contentDocument 的 readState 为 'complete'
                for iframe in results:
                    start_time = time.monotonic()
                    is_loaded = False
                    while time.monotonic() - start_time <= load_timeout:
                        is_loaded = iframe.run_js("return document.readyState === 'complete'")
                        if is_loaded:
                            break
                        time.sleep(0.2)
                    if not is_loaded:
                        raise TimeoutError(f"等待时间{load_timeout}之后iframe元素仍未加载完毕\n 元素：{iframe}")
            # 按照查找方式的不同，child_elements可能是列表或者元素，只用eles/children会返回列表
            return results
        else:
            return []

    def _process_level(self, level: dict):
        """
        处理单个字典\n
        """
        parent_element = self._element_stack.top()
        
        for node in level.values():
            node: dict
            # ================1================ 找出满足条件的元素列表
            locator_method: dict = node['locator_method']
            child_elements: list = ElementLocator._locate_elements(parent_element, locator_method, self._locate_timeout, self._load_timeout)

            if child_elements:
                
                # 正常查找到的情况
                for child_element in child_elements:
                    
                    self._element_stack.push(child_element) # 压入元素栈中
                    is_container_pushed = False
                    try:
                        # ================2================ 若有 virtual_dict 则创建空字典并压入栈顶
                        virtual_dict = node.get('virtual_dict')
                        if virtual_dict:
                            key = virtual_dict['key']
                            node_virtual_dict = {}
                            
                            # 更新result，在result_dict下指定位置 **新增** node_virtual_dict
                            container_name = virtual_dict['container']
                            virtual_container: dict = self._container_stack.resolve(container_name)
                            # 处理新增：字典列表追加
                            if virtual_container.get(key) is None:
                                # 更新结果
                                virtual_container[key] = [node_virtual_dict]
                                # 压入栈中
                                self._container_stack.push(name=key, ref=node_virtual_dict)
                                is_container_pushed = True
                            elif isinstance(virtual_container.get(key), list):
                                # 更新结果
                                virtual_container_list: list = virtual_container.get(key)
                                virtual_container_list.append(node_virtual_dict)
                                # 压入栈中
                                self._container_stack.push(name=key, ref=node_virtual_dict)
                                is_container_pushed = True
                            else:
                                # TODO 报错，不允许将原来的信息/方法键覆盖成容器
                                pass

                        # ================3================ 若有 targets 则逐个调用get_targets获取信息
                        targets: dict = node.get('targets')

                        if targets:
                            for target_name in targets:
                                target: dict = targets.get(target_name) # 剪枝后其不会是 None
                                target_object = ElementLocator._get_target(child_element, target) # 获取到目标，字符串或者回调函数
                                if target_object is not None:
                                    # 目标存在
                                    container_name = target['container']
                                    
                                    target_container: dict = self._container_stack.resolve(container_name) #使用键名从栈中找出对应的容器
                                    target_key = target['key']
                                    if target_container.get(target_key) is None:
                                        target_container[target_key] = target_object
                                    else:
                                        # TODO 设计报错，不允许(1)覆盖已有的信息/方法键，(2)覆盖已经存了虚拟容器或者虚拟容器列表的键
                                        pass
                        # ================4================ 若有 sub_elements 则处理sub_elements

                        sub_elements: list = node.get('sub_elements')
                        if sub_elements:
                            self._process_level(sub_elements)
                        
                    finally:
                        # ================5================ sub_elements全部处理完后弹出虚拟容器栈和元素栈

                        if is_container_pushed:
                            self._container_stack.pop()    
                        self._element_stack.pop()

            else:
                # TODO 未找到的情况，按照不同的presence处理
                presence = node.get('presence')
                if presence:
                    if presence == 'required':
                        pass
                    elif presence == 'optional':
                        continue
                    elif presence == 'unknown':
                        pass
                else:
                    # TODO 设计这一步所的逻辑
                    pass

    def extract_info(self, required_set: set = None) -> dict:
        """
        根据层级配置从 ChromiumPage 中提取信息。\n
        先对配置树剪枝，仅保留 required_fields 需要的数据路径。\n
        """
        result = {}
        # 从根节点开始剪枝
        pruned_config = ElementLocator._prune_sublevel(self._config, required_set)
        # 初始化容器栈和元素栈
        self._element_stack = ElementStack(self.page)
        self._container_stack = ContainerStack(result)
        # 启动递归
        self._process_level(pruned_config)
        
        return result

if __name__ == "__main__":
    import json
    with open(r"F:\coding\临时工具、临时测试\UCAS_MOOC\config.json",'r',encoding='utf-8') as f:
        config = json.load(f)

    page = ChromiumPage(9444)
    el = ElementLocator(page, config)
    result = el.extract_info({"stem","name_and_content","my_answer","is_answer_correct"})

    with open(r"F:\coding\临时工具、临时测试\UCAS_MOOC\剪枝测试\1.json",'w',encoding='utf-8') as f:
        json.dump(result,f,ensure_ascii=False,indent=4)
