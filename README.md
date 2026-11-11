# StarSniff 星嗅

> StarSniff = **Star**（星） + **Sniff**（嗅探）：拿起望远镜，把网页里的剧集与媒体资源一网打尽。

[![Release](https://img.shields.io/github/v/release/YCX-jpg/StarSniff?label=release)](https://github.com/YCX-jpg/StarSniff/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Windows 桌面版的**剧集 / 网页媒体嗅探与下载**工具：输入网址，优先用 yt-dlp 直接拉取
剧集列表；不支持的站点自动回到普通嗅探，抓出页面里的**图片 / 音频 / 视频 / 流媒体**。
列表里可以预览（缩略图、音频时长与码率、视频分辨率与时长）、勾选下载，
并支持多套可切换的界面主题。

纯 Python + Tkinter 实现，**除 Pillow（可选，用于缩略图）和 yt-dlp（可选）外零第三方依赖**，
拷到任意装有 Python 3.9+ 的 Windows 机器上即可运行。

---

## 下载 / 安装

* 普通用户（推荐）：到 [Releases](https://github.com/YCX-jpg/StarSniff/releases)
  下载 `StarSniff-<版本>-win64.zip`，解压后双击文件夹里的 `StarSniff.exe`，
  无需安装 Python。目录版启动快、不依赖系统临时目录。
* 也可以下载单文件 `StarSniff-portable.exe`（首次启动会解压，稍慢；
  若被杀毒软件拦截或提示“无法创建临时目录”，请改用 zip 目录版）。
* 源码运行：安装 Python 3.9+ 后：

```bat
python app.py
```

或双击 `run.bat`（会用 `pythonw` 启动，不弹黑框）。

界面从上到下：

| 区域 | 内容 |
|---|---|
| 标题栏 | 自绘标题栏：logo、标题、版本、使用说明 / 设置、最小化 / 最大化 / 关闭 |
| 顶部 | 地址输入框 + **开始** + **UA 下拉框** + `深度` / `动态` 开关 |
| 中部 | 类型页签 + 排序 + 搜索框 |
| 列表 | 左侧预览，右侧文件名、大小、链接，最左是复选框 |
| 底部 | 保存目录、**下载**（下载中变为**停止下载**） |

操作要点：

* 窗口没有系统标题栏：拖动顶部标题栏移动，双击最大化/还原，四边或四角拖动缩放；
  右上角是最小化 / 最大化 / 关闭。
* 点击列表任意位置即可勾选/取消；`Ctrl+A` 全选可见项，`Ctrl+F` 定位搜索框。
* 筛选栏右侧的「排序」可按 大小 / 类型 / 文件名 重排（只影响显示）。
* 双击资源在浏览器打开；已下载的文件双击直接打开。
* 右键菜单可复制链接、导出选中链接为 TXT、展开 m3u8 清单；已下载条目可打开文件 /
  打开所在文件夹 / 复制保存路径。
* 「设置 → 外观」可以换主题、主题色、按钮/窗口圆角，并**实时预览**；
  点「应用」立即生效，点「取消」不保存。

---

## 功能

### 界面与个性化

内置 5 套完整视觉预设。**每套主题不只是换色，组件位置、尺寸、导航方式、
列表排布和交互也不同**，切换时会重建包括自绘标题栏在内的全部界面：

| 预设 | 布局与交互 |
|---|---|
| 默认 | 传统自上而下：顶栏 + 地址栏 + 分段筛选 + 行式列表 + 底栏 |
| 亚克力 | 左侧竖向资源导航，右侧工作区；地址栏、搜索、排序重新排布 |
| 液态玻璃 | Apple 风格：居中标题 + 红黄绿灯、悬浮大胶囊地址栏、居中药丸筛选、卡片列表、悬浮底栏；筛选胶囊有滑动指示动画 |
| 霓虹 | 极简细顶栏、芯片式筛选、发光按钮、底部 Dock 操作区 |
| 复古 2000 | 蓝色标题栏 + 菜单栏（文件/编辑/查看/帮助）+ 3D 按钮 + 行式列表 + 底部状态栏 |

* **主题色**：8 个预设色一键切换，`跟随预设`可恢复；
* **按钮圆角 / 窗口圆角**：独立滑杆 0–28 / 0–30，窗口圆角通过透明色键真正切出圆角；
* **动画**：按钮悬停/按下渐变、玻璃高光、霓虹外发光、胶囊指示器滑动、主题预览脉动；
* 预览区实时绘制迷你真窗口（标题栏、列表卡片、按钮、进度条），改动立即反映；
* 设置窗口为分页结构：外观 / 嗅探 / 网络 / 下载 / 关于，底部「取消」「应用」；
* 窗口位置与大小会记在 `config.json`，最大化不覆盖普通尺寸；
* 高分屏自动缩放（字体、行高、缩略图、间距一起放大）。

### yt-dlp（剧集解析）

StarSniff 不强制安装 yt-dlp，但检测到时会优先使用它：

* 查找顺序：环境变量 `YTDLP_PATH` → 软件目录的 `yt-dlp.exe` → 系统 PATH →
  当前 Python 的 `yt_dlp` 模块；
* 对支持的站点用 `--flat-playlist --dump-single-json` 拉取**剧集/分P/清晰度列表**，
  每一集作为一行资源加入列表（标题、时长、大小），可直接勾选下载；
* 下载由 yt-dlp 子进程完成，解析百分比进度，支持停止/取消；
* 不支持的站点会**自动回退**到普通嗅探，不影响原有能力；
* **设置 → 嗅探 → yt-dlp** 提供「一键下载 yt-dlp」：从 GitHub 拉取官方
  `yt-dlp.exe` 到软件目录，校验 MZ 头和大小后原子替换，下载过程不阻塞界面；
* 设置「嗅探 → yt-dlp」里可以关闭优先使用。

### 嗅探

* **静态解析**：`<img>` / `<video>` / `<audio>` / `<source>` / `srcset` / `poster` /
  `data-src` 等懒加载属性 / `<meta og:image>` / 内联 `background-image`。
* **深度解析**（默认开）：CSS 里的 `url()`、JS 文件里的直链、JSON 接口里的媒体地址
  （自动补协议、还原 `\/` 转义）。
* **动态嗅探**（可选）：启动系统 Edge/Chrome 无头实例，通过 CDP 监听 `Network` 域，
  抓 JS 异步加载、懒加载、播放器媒体；自动滚动触发加载。
  无需 Selenium/Playwright，内置最小 WebSocket + CDP 客户端。
* **格式识别不迷信扩展名**：按文件头魔术字节判断真实类型。
* 支持 `?format=jpg`、`?name=large` 这类查询参数形式的媒体地址；
  同一图片的多尺寸/多格式变体自动合并，保留最大最清晰的一张。
* HLS（`.m3u8`）主清单自动展开成各清晰度；MPEG-DASH（`.mpd`）解析 `Representation`。
* 去重合并、并发探测（默认 10 线程）、404/无效链接过滤、最小图片尺寸过滤、结果上限。
* 探测有总时限（默认 35 秒），慢站点不会卡死；超时资源仍会列出并标注。

### 预览

* 图片：真实缩略图（居中、不拉伸小图标），显示 `宽×高`。
* 音频：时长与码率（MP3 帧头/ID3、FLAC STREAMINFO、WAV fmt、M4A moov）。
* 视频：分辨率与时长（MP4 `moov`、WebM EBML、FLV onMetaData）。

### 下载

* **并行下载**：「设置 → 下载」可调 1–8 个文件并发（默认 4），底栏显示队列进度与总速度。
* **大文件多连接**：8MB 以上、服务器支持 `Range` 时，自动用 1–8 个连接分段下载
  （默认 4），显著提升单文件速度；不支持 Range 时自动回退单连接。
* **HLS 分片并发**：分片抓取线程提升到最多 8 个；yt-dlp 使用
  `--concurrent-fragments 4` 加速分片下载。
* **随时停止**：底部按钮变为「停止下载」，可停整条队列；右键单个任务只取消它。
* **失败重试**：右键失败条目，会沿用半截文件续传，不生成 `(1)` 副本。
* 断点续传（`Range`）、进度、取消；服务端提前断流也能识别并续传。
* **HLS 自动合并分片**为 `.ts`/`.mp4`，分片并发抓取、有序写盘，
  支持 `#EXT-X-KEY:AES-128`（纯 Python AES-CBC 解密）。
* yt-dlp 条目走 yt-dlp 下载引擎；普通资源走内置下载器。
* 默认保存到软件目录下的 `Download`，可在底栏「更改」。

### 检查更新

* 启动后自动检查 GitHub 最新 Release（可在「设置 → 关于」立即关闭，不用点应用）；
* 自动检查只在后台进行，**不会弹窗抢焦点、不会卡住设置**；发现更高版本时
  标题栏出现 `↑ v2.1.0` 徽标，点击徽标才展开更新说明；
* 支持「前往下载」「稍后」「跳过此版本」；跳过后可在设置里恢复提醒；
* 只读取公开的 GitHub Releases，不需要登录，也不会自动覆盖正在运行的程序；
* GitHub API 限流时自动退回 `releases/latest` 跳转地址读取版本号。

### 智能代理

设置「网络」里填代理后，每次开始嗅探前会同时探测直连与代理延迟：

* 哪条快走哪条，两条都正常时直连优先（代理需快 5ms 以上）；
* 代理不可用自动直连；直连不通自动走代理；都失败时按直连续试；
* 可选「强制走代理 / 不走代理」（不走代理会绕过系统环境变量代理）；
* 选中的线路对页面获取、深度解析、无头浏览器、下载全部生效；
* 「测速」按钮可手动比较延迟，不影响保存。

### 请求头伪装（User-Agent）

「开始」右侧 UA 下拉框会把请求伪装成 Windows / Android / iOS / Mac / Linux 浏览器，
静态、动态嗅探与下载的每个请求都会使用同一套 UA 与 `sec-ch-ua` 头。

---

## 文件结构

```
app.py                     启动入口
run.bat                    Windows 启动脚本（pythonw，无控制台）
resource_sniffer/
  gui.py                   主窗口：按主题壳层搭建标题栏、地址栏、列表、底栏
  prefs.py                 设置窗口：外观 / 嗅探 / 网络 / 下载 / 关于 + 更新提示
  updater.py               GitHub Releases 检查更新（API + HTML 兜底）
  frameless.py             无边框窗口：拖动、红黄绿灯、边缘缩放、圆角
  widgets.py               自绘控件：按钮、分段/侧栏/芯片页签、虚拟化列表
  theme.py                 5 套视觉预设 + 每套独立 layout 规格、DPI 缩放
  ytdlp.py                 yt-dlp 探测、剧集解析、条目转换
  engine.py                嗅探引擎：静态/深度/动态 → 并发探测 → 清单展开
  browser.py               Chromium 发现、最小 WebSocket/CDP 客户端
  htmlparse.py             HTML/CSS/JSON 媒体地址提取、文件名推断
  useragents.py            User-Agent 伪装配置
  smartproxy.py            智能代理测速与选路
  streams.py               HLS / DASH 清单解析
  mediainfo.py             魔术字节识别、媒体元数据、缩略图
  net.py                   标准库 HTTP 客户端（Range、代理、Cookie、流式下载）
  downloader.py            并行下载、重试、断点续传、HLS 合并、yt-dlp 引擎
  aes.py                   AES-128-CBC 解密
  models.py                资源条目与线程安全集合
  config.py                配置读写（清洗、原子保存、可用 RS_CONFIG_DIR）
tests/
  site.py / mock_cdp.py    本地测试站点与模拟 DevTools
  selftest.py              引擎/下载/主题/更新/yt-dlp 端到端自检
  uitest.py                界面功能与每套主题布局自检
  guishot.py               截图 + 版式像素校验
assets/                    应用图标
StarSniff.spec             PyInstaller 单文件打包配置
build_exe.ps1              本地构建 exe + Release zip
.github/workflows/         push v* tag 时自动构建并发布 Release
```

---

## 测试

无网络也能跑（yt-dlp 部分用伪可执行文件验证）：

```bat
python tests\selftest.py     :: 118 项：嗅探、清单、并行/分段下载、HLS、主题、图标、更新、yt-dlp 一键下载、解析器
python tests\uitest.py       :: 102 项：布局、DPI、排序、窗口、无边框标题栏、5 套主题布局、更新不阻塞、yt-dlp 按钮、下载、UA、代理、边界
python tests\guishot.py      :: 生成截图并做版式像素校验
```

---

## 构建 Windows exe / 发布 Release

本地需要 Python 3.9+ 与 PyInstaller：

```powershell
python -m pip install pyinstaller pillow
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
```

如果直接运行 `.\build_exe.ps1` 提示“禁止运行脚本”，用上面的
`powershell -ExecutionPolicy Bypass -File` 方式，或先执行
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`。

产物：

```
dist\StarSniff\StarSniff.exe                 目录版（推荐）
dist\StarSniff-<版本>-win64.zip              目录版整包
dist\StarSniff-<版本>-win64.zip.sha256
dist\StarSniff-portable.exe                  单文件便携版
dist\StarSniff-portable.exe.sha256
```

`build_exe.ps1` 会自动读取 `resource_sniffer/__init__.py` 里的版本号，
运行自检（可加 `-SkipTests`），先用 `StarSniff.spec` 打目录版并压成 zip，
再用 `StarSniff-onefile.spec` 打单文件版。单文件版需要系统临时目录可写；
目录版没有这个限制，所以作为 Release 的推荐下载。

**自动发布**：把版本号改好并提交后，推一个 `v<版本>` tag：

```powershell
git tag v2.1.1
git push origin v2.1.1
```

GitHub Actions（`.github/workflows/release.yml`）会在 Windows runner 上
构建目录版 zip 和单文件 exe，自动创建 Release 并附上文件。tag 与代码版本
不一致时会失败，避免发错版本。若已安装并登录 GitHub CLI，也可以本地发布：

```powershell
.\build_exe.ps1 -Publish
```

---

## 常见问题

**yt-dlp 怎么启用？**
打开「设置 → 嗅探 → yt-dlp」，点「一键下载 yt-dlp」即可自动下载到软件目录；
也可以手动把 `yt-dlp.exe` 放到 `StarSniff` 目录（或加入 PATH），重启软件。
设置页会显示检测状态；不安装也能正常使用普通嗅探。

**点「开始」后没走 yt-dlp？**
可能该地址不受支持，或你在设置里关闭了「优先使用 yt-dlp」。
状态栏会提示「yt-dlp 未支持该地址，改用普通嗅探」。

**主题切换后界面没变？**
在「设置 → 外观」里选好预设/主题色/圆角后点「应用」；「取消」只关闭不保存。

**X / Twitter 抓不到？**
先打开「动态」；需要登录的推文在「设置 → 网络」里粘贴浏览器 Cookie
（至少包含 `auth_token` 和 `ct0`）。

**部分资源 403 / 下载失败？**
多半是防盗链，填入该站点 Cookie，必要时配置代理。

**m3u8 下载后打不开？**
合并产物是 MPEG-TS（`.ts`），可用 VLC/PotPlayer 播放；转 MP4：
`ffmpeg -i out.ts -c copy out.mp4`。

**DASH（.mpd）为什么不能直接下载？**
DASH 需要按 `SegmentTemplate` 逐个取分片，成本高且容易出错；本工具只做解析展示。

---

## 说明

* 本工具仅用于抓取你有权访问的公开资源，请遵守目标站点的服务条款与当地法律。
* 仅支持 Windows（界面、无边框窗口与圆角依赖 Win32；其余模块跨平台）。
* yt-dlp 与 Pillow 均为可选依赖，缺失时自动降级，不影响基础嗅探与下载。
