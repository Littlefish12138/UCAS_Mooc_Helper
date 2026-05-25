"""
读取注册表，找用户安装的edge和chrome的位置
"""
import os
import winreg
import subprocess
from typing import Optional

from DrissionPage import ChromiumPage, ChromiumOptions

# 浏览器对应的注册表 App Paths 键名
BROWSER_EXE_MAP = {
    "chrome": "chrome.exe",
    "edge": "msedge.exe",
}

# 默认用户数据目录（基于 LocalAppData）
DEFAULT_USER_DATA_MAP = {
    "chrome": os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "User Data"),
    "edge": os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Edge", "User Data"),
}

# 策略注册表路径（可能覆盖用户数据目录）
POLICY_PATHS_MAP = {
    "chrome": r"SOFTWARE\Policies\Google\Chrome",
    "edge": r"SOFTWARE\Policies\Microsoft\Edge",
}


def _read_registry_value(
    hive: int,
    subkey: str,
    value_name: Optional[str] = None,
    use_32bit: bool = False,
) -> Optional[str]:
    """
    读取注册表指定键值。\n
    :param hive: 注册表根键，如 winreg.HKEY_LOCAL_MACHINE
    :param subkey: 子键路径
    :param value_name: 值名称，None 表示读默认值
    :param use_32bit: 是否使用 32 位视图 (KEY_WOW64_32KEY)
    :return: 字符串值，若不存在则返回 None
    """
    access = winreg.KEY_READ
    if use_32bit:
        access |= winreg.KEY_WOW64_32KEY
    else:
        access |= winreg.KEY_WOW64_64KEY  # 默认尝试 64 位视图

    try:
        with winreg.OpenKey(hive, subkey, 0, access) as key:
            value, _ = winreg.QueryValueEx(key, value_name if value_name else "")
            if isinstance(value, str):
                return value
    except FileNotFoundError:
        pass
    return None


def get_browser_path(browser_type: str) -> Optional[str]:
    """
    获取浏览器的可执行文件路径。\n
    :param browser_type: "chrome" 或 "edge" (大小写不敏感)
    :return: 完整路径字符串，找不到则返回 None
    """
    browser_type = browser_type.lower()
    if browser_type not in BROWSER_EXE_MAP:
        raise ValueError(f"不支持的浏览器类型: {browser_type}，仅支持 'chrome' 或 'edge'")

    exe_name = BROWSER_EXE_MAP[browser_type]
    app_paths_subkey = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe_name}"

    # 1. 尝试从 HKCU 的 App Paths 读取
    path = _read_registry_value(winreg.HKEY_CURRENT_USER, app_paths_subkey)
    if path:
        return path

    # 2. 尝试从 HKLM 的 64 位视图读取
    path = _read_registry_value(winreg.HKEY_LOCAL_MACHINE, app_paths_subkey, use_32bit=False)
    if path:
        return path

    # 3. 尝试从 HKLM 的 32 位视图读取（兼容 32 位浏览器）
    path = _read_registry_value(winreg.HKEY_LOCAL_MACHINE, app_paths_subkey, use_32bit=True)
    if path:
        return path

    return None


def get_user_data_path(browser_type: str) -> Optional[str]:
    """
    获取浏览器的默认用户数据目录。\n
    :param browser_type: "chrome" 或 "edge" (大小写不敏感)
    :return: 目录路径字符串，若无法确定则返回 None
    """
    browser_type = browser_type.lower()
    if browser_type not in DEFAULT_USER_DATA_MAP:
        raise ValueError(f"不支持的浏览器类型: {browser_type}，仅支持 'chrome' 或 'edge'")

    # 1. 检查策略指定的路径（HKLM）
    policy_subkey = POLICY_PATHS_MAP[browser_type]
    for use_32bit in (False, True):
        path = _read_registry_value(
            winreg.HKEY_LOCAL_MACHINE,
            policy_subkey,
            "UserDataDir",
            use_32bit=use_32bit,
        )
        if path:
            return path

    # 2. 检查策略指定的路径（HKCU）
    path = _read_registry_value(
        winreg.HKEY_CURRENT_USER,
        policy_subkey,
        "UserDataDir",
    )
    if path:
        return path

    # 3. 返回默认路径（基于 LOCALAPPDATA 环境变量）
    default_path = DEFAULT_USER_DATA_MAP[browser_type]
    if os.path.isdir(default_path):
        return default_path
    # 即使目录不存在也返回预期路径，方便调用者创建
    return default_path

# ================== 浏览器启动器函数 ==================

def kill_browser_process(browser_type='edge'):
    """强制结束浏览器进程"""
    if browser_type == 'edge':
        subprocess.run(['taskkill', '/F', '/IM', 'msedge.exe'], capture_output=True)
    elif browser_type == 'chrome':
        subprocess.run(['taskkill', '/F', '/IM', 'chrome.exe'], capture_output=True)


def launch_browser(browser_type: str, user_data_dir: str = None, is_incognito = True, port: int = 9444):
    """启动浏览器, 提醒不能使用Chrome的默认数据目录, 当使用Edge的默认数据目录时自动将后台进程结束"""
    browser_type = browser_type.lower()

    co = ChromiumOptions()
    if browser_type in {'edge', 'chrome'}:
        co.set_browser_path(get_browser_path(browser_type))
    else:
        raise ValueError(f"错误的浏览器类型{browser_type}")
    
    if is_incognito is False:
        if user_data_dir:
            co.set_user_data_path(user_data_dir)
        else:
            if browser_type == 'chrome':
                raise ValueError("当浏览器类型为chrome时, 不允许使用默认数据目录")
            co.set_user_data_path(get_user_data_path(browser_type))
        
        if user_data_dir == get_user_data_path(browser_type):
            kill_browser_process(browser_type)
    else:
        co.incognito(is_incognito)

    
    co.set_local_port(port)

    page = ChromiumPage(co)

    return page

if __name__ == "__main__":
    print(f"Chrome: {get_browser_path('chrome')}")
    print(f"Edge: {get_browser_path('edge')}")
    print(f"Chrome数据目录: {get_user_data_path('chrome')}")
    print(f"Edge数据目录: {get_user_data_path('edge')}")