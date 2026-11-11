"""内置 User-Agent 伪装配置。

嗅探和下载时带上目标浏览器的完整请求头（UA + 现代浏览器的 ``sec-ch-ua`` 客户端提示），
以便通过按 UA 判断的站点，或拿到移动端/桌面端不同版本的页面。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class UAProfile:
    key: str
    label: str            # 下拉列表里显示的名字
    ua: str               # User-Agent 头
    platform: str = "Windows"   # sec-ch-ua-platform
    mobile: bool = False        # sec-ch-ua-mobile
    brand: str = ""             # sec-ch-ua 里的品牌串（Chrome 系才有）
    extra: dict = field(default_factory=dict, compare=False)

    def headers(self) -> dict:
        """返回该 UA 需要附带的完整请求头。"""
        out = {"User-Agent": self.ua}
        if self.brand:
            out["sec-ch-ua"] = self.brand
            out["sec-ch-ua-mobile"] = "?1" if self.mobile else "?0"
            out["sec-ch-ua-platform"] = f'"{self.platform}"'
        out.update(self.extra)
        return out


_CHROME_BRAND = ('"Chromium";v="131", "Not_A Brand";v="24", '
                 '"Google Chrome";v="131"')
_EDGE_BRAND = ('"Microsoft Edge";v="131", "Chromium";v="131", '
               '"Not_A Brand";v="24"')

# “自动”不指定固定 UA，沿用内置的随机桌面 UA 池
AUTO = UAProfile(
    key="auto",
    label="默认（随机桌面）",
    ua="",
)

WINDOWS_CHROME = UAProfile(
    key="win_chrome",
    label="Windows Chrome",
    ua=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
    platform="Windows", brand=_CHROME_BRAND,
)

WINDOWS_FIREFOX = UAProfile(
    key="win_firefox",
    label="Windows Firefox",
    ua=("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) "
        "Gecko/20100101 Firefox/133.0"),
    platform="Windows",
)

ANDROID_CHROME = UAProfile(
    key="android_chrome",
    label="Android Chrome",
    ua=("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"),
    platform="Android", mobile=True, brand=_CHROME_BRAND,
)

IOS_SAFARI = UAProfile(
    key="ios_safari",
    label="iOS Safari",
    ua=("Mozilla/5.0 (iPhone; CPU iPhone OS 17_6 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 "
        "Mobile/15E148 Safari/604.1"),
    platform="iOS", mobile=True,
)

MAC_CHROME = UAProfile(
    key="mac_chrome",
    label="Mac Chrome",
    ua=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
    platform="macOS", brand=_CHROME_BRAND,
)

LINUX_FIREFOX = UAProfile(
    key="linux_firefox",
    label="Linux Firefox",
    ua=("Mozilla/5.0 (X11; Linux x86_64; rv:133.0) Gecko/20100101 "
        "Firefox/133.0"),
    platform="Linux",
)

LINUX_CHROME = UAProfile(
    key="linux_chrome",
    label="Linux Chrome",
    ua=("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
    platform="Linux", brand=_CHROME_BRAND,
)

PROFILES: list[UAProfile] = [
    AUTO,
    WINDOWS_CHROME,
    WINDOWS_FIREFOX,
    ANDROID_CHROME,
    IOS_SAFARI,
    MAC_CHROME,
    LINUX_FIREFOX,
    LINUX_CHROME,
]

_BY_KEY = {p.key: p for p in PROFILES}

# 桌面 UA 池（"默认"模式沿用，保持原有行为）
DESKTOP_UA_POOL = [
    WINDOWS_CHROME.ua,
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
     "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 Edg/129.0.0.0"),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
     "(KHTML, like Gecko) Version/17.4 Safari/605.1.15"),
]


def get(key: str) -> UAProfile:
    """按 key 取配置，找不到时返回“默认”。"""
    return _BY_KEY.get((key or "").strip(), AUTO)


def label_of(key: str) -> str:
    return get(key).label


def keys() -> list[str]:
    return [p.key for p in PROFILES]
