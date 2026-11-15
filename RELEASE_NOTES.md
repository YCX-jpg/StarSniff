# StarSniff 星嗅 3.1.1-rc4

本轮修两个问题：**检查更新把 rc 版本当成同一个版本**，以及**本地开着 VPN / 代理时不会自动走**。
（rc3 的圆角、性能主题、输入框提示修复都还在。）

## 检查更新

- **rc 版本能正确比较了**：以前只比主版本号，`3.1.1-rc2` 和 `3.1.1-rc3` 都解析成
  `3.1.1`，所以 rc2 会认为 rc3「已是最新」。现在把后缀也算进排序：
  `3.1.1-rc2 < 3.1.1-rc3 < 3.1.1`，`snapshot < beta < rc < 正式版`，同后缀按数字比
  （rc9 < rc10）。
- **检查更新会自动找代理**：先看环境变量，再读 Windows「Internet 选项」里的系统代理
  （Clash / v2rayN 开启系统代理时写在这里），最后扫本地常见代理端口
  （7890、7897、10809、1080、8118…），逐个尝试，全部失败才直连。
- 代理候选也用于 API 限流后的 `releases/latest` 兜底，避免「能上网但读不到版本号」。

## 代理 / 智能线路

- **自动使用本地 VPN**：以前只读 `HTTP(S)_PROXY` 环境变量，本地开着 Clash 之类的
  图形客户端（只在系统设置或本地端口上）时完全用不上。现在
  `system_proxy()` 会依次尝试环境变量 → Windows 系统代理 → 本地代理端口，
  嗅探、下载、更新检查都走这套。
- 只有本地端口确实能完成 CONNECT 握手才算「可用」，不会把普通网页端口当代理；
  探测结果缓存 60 秒，不会每次请求都扫端口。
- 设置页「网络」里的说明同步更新：留空代理 = 自动检测系统代理 / 本地端口。

## 版本规则

- 基线版本 **3.1.1**，每次更新都是下一个 rc / snapshot（`-rc1` → `-rc2` → `-rc3` → `-rc4`），
  稳定后才摘掉 `-rc`。

## 已知情况

- 单文件便携版在开发机上启动不了（`Could not create temporary directory`），
  属于本机 PyInstaller 单文件限制；CI 上是干净的 windows-latest，正常构建。
  下载仍然推荐 zip 目录版。
- 程序没有代码签名，安装时 SmartScreen 会提示「无法验证发布者」，
  点「更多信息」→「仍要运行」即可。

## 校验

- `python tests/selftest.py`：**123 项全部通过**
- `python -m compileall resource_sniffer`：通过
- `tools/verify_radius.py` / `verify_perf.py` / `verify_placeholder.py`：通过
- `tools/verify_anim.py` / `verify_settings.py` / `verify_scroll.py`：通过
- 真机验证：本机系统代理 `http://127.0.0.1:65532` 被自动识别，
  `check_latest()` 返回 `3.1.1-rc3` 且判定为比 `3.1.1-rc2` 新
