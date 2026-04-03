# 🎓 UCAS 慕课挂课工具

> 自动播放视频、监控暂停、翻页刷课，解放双手 ✨

![Python](https://img.shields.io/badge/Python-3.8+-blue.svg) ![License](https://img.shields.io/badge/License-MIT-green.svg)

## 📦 项目组成与原理

本工具基于 **DrissionPage** 库（一个整合了 Selenium 和 Requests 优势的浏览器自动化框架），通过 **浏览器远程调试端口** 控制浏览器行为，无需 WebDriver 配置。

- **`course_listener.py`** – 核心业务逻辑
  - 浏览器启动器（支持普通/无痕/连接已打开的调试浏览器）
  - 课程页面处理器（章节展开、视频点击、播放状态监控）
  - 任务队列管理（失败自动重试）

- **`main.py`** – 图形界面（tkinter）
  - 提供操作面板，支持两种启动模式
  - 显示运行日志

- **`utils.py`** – 工具函数
  - 自动从注册表读取 Edge/Chrome 安装路径和默认数据目录

## 🖥️ 系统要求

- **操作系统**：Windows 7 / 10 / 11（32 位或 64 位）
- **浏览器**：Microsoft Edge 或 Google Chrome
- **Python**环境：3.8+

## 🎯 适用范围 
- 2026年4月份作用mooc的网页端UI，

### 🔍 主要工作流程

1. 通过调试端口连接或启动浏览器，打开课程页面
2. 解析页面章节结构，生成待播放视频任务队列
3. 依次进入每个视频，检测是否已完成（检查“任务点已完成”字样）
4. 自动播放视频，并启动后台线程监控暂停状态（如有暂停立即恢复）
5. 监听网络请求中的完成图片，判定视频播放完毕，切换下一个

---

## 🚀 操作方法

### 1️⃣ 输入课程链接

在图形界面顶部的输入框中粘贴课程页面的 URL（例如：`http://mooc.ucas.edu.cn/mycourse/studentstudy？chapterId=xxx`）。

### 2️⃣ 选择启动模式

#### 🤖 自动启动（推荐新手）

##### 🔏 使用用户数据目录

- 程序自动使用你的浏览器**用户数据目录**启动一个**带调试端口**的新浏览器实例，你**不必**进行登录
- 完成后会直接打开课程页面，**无需手动登录**（因为复用了你的登录态）
- **注意**：若使用 Edge 且采用默认用户数据目录，程序会强制结束当前所有 Edge 进程（避免端口冲突），**请提前保存网页工作并关闭 Edge**。
- **注意**：Chrome 新版安全策略**不允许**调试默认数据目录，如您使用chrome，请使用非默认数据目录或者使用无痕模式

##### 🕵️ 无痕模式

- 程序以**无痕窗口**启动浏览器，并先打开课程链接
- 然后弹出提示框，**你手动完成登录**，点击“确定”后自动开始刷课

#### 🔌 手动启动（连接打开调试端口的浏览器）

- 适用场景：您**自行**用命令行启动带调试端口的浏览器，再让本工具连接
- 使用示例（把路径替换成你电脑上的）

  **Edge 示例：**

  ```cmd
  "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --remote-debugging-port=9222 --user-data-dir="C:\temp\edge_profile"
  ```

  ```powershell
  & "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --remote-debugging-port=9222 --user-data-dir="C:\temp\edge_profile"
  ```

  **Chrome 示例：**

  ```cmd
  "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\temp\chrome_profile"
  ```

  ```powershell
  & "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\temp\chrome_profile"
  ```

  3. 在工具界面选择“手动启动”，填入端口号（默认 9222），点击“开始挂课”即可

- ⚠️ 由于Google的安全策略，**使用 Chrome 时，不能使用默认的用户数据目录**（如 `--user-data-dir="C:\Users\你的用户名\AppData\Local\Google\Chrome\User Data"`），否则程序无法连接。请务必指定一个**非默认的目录**，或者使用--incognito参数指定无痕模式。

---

## ⚠️ 风险提示与免责声明

### 🔒 远程调试端口有安全风险！

- 开启远程调试端口后，任何能够访问你计算机该端口的程序都可以控制浏览器并读取你的用户数据，可能造成**隐私泄露、账号被盗**等严重后果。
- **使用建议**：
  - 仅在刷课时临时开启，完成后关闭浏览器
  - 使用无痕模式启动，或者指定临时数据目录
  - 不要在不安全的网络环境（如公共 Wi-Fi）下使用
  - 手动启动模式下，请确保没有其他恶意软件监听同一端口

### 📜 免责声明

- 本工具**仅供学习交流**，**严禁**用于任何违反课程平台规则、学校规定或国家法律法规的用途。
- 使用本工具产生的一切后果（包括但不限于账号封禁、成绩无效、法律责任）由使用者自行承担。
- 作者不鼓励也不支持滥用自动化工具刷课，请合理规划学习时间。

---

## 🛠️ 常见问题

**Q: 为什么连接失败**  
A: 可能有如下原因：(1) 使用默认用户数据目录，但是没有关闭浏览器实例导致占用 (2) 尝试使用chrome的默认数据目录

**Q: 视频卡在“等待完成图片”一直不动？**  
A: 可能是网络原因或平台改版。请查看日志窗口，如果长时间无响应，可强制停止后重试。

**Q: 如何停止任务？**  
A: 停止按钮没有用，因为没有写相关代码。你可以直接把本程序关闭

---

## 📄 许可证

MIT License © 2025

Enjoy your automated learning! 🎉
