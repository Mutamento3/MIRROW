"""App 包名 → 中文名映射（服务端兜底）

Android getApplicationLabel() 在部分 ROM 上会失败返回包名，
此映射表作为 fallback 将包名转为可读中文名。

来源：AionsHome 项目 (activity.py KNOWN_APPS)，已清洗 MIRROW 无关条目。
"""

# ── 包名 → 中文名映射 ──────────────────────────────────
KNOWN_APPS: dict[str, str | None] = {
    # 社交 / 通讯
    "com.tencent.mm": "微信",
    "com.tencent.mobileqq": "QQ",
    "com.tencent.tim": "TIM",
    "com.xingin.xhs": "小红书",
    "com.sina.weibo": "微博",
    "com.immomo.momo": "陌陌",
    "com.tencent.wework": "企业微信",
    "com.alibaba.android.rimet": "钉钉",
    "com.lark.messenger": "飞书",

    # 视频 / 直播
    "com.ss.android.ugc.aweme": "抖音",
    "com.kuaishou.nebula": "快手",
    "com.smile.gifmaker": "快手",
    "tv.danmaku.bili": "哔哩哔哩",
    "com.youku.phone": "优酷",
    "com.tencent.qqlive": "腾讯视频",
    "com.qiyi.video": "爱奇艺",
    "com.hunantv.imgo.activity": "芒果TV",

    # 音乐
    "com.netease.cloudmusic": "网易云音乐",
    "com.netease.uuremote": "UU远程",
    "com.tencent.qqmusic": "QQ音乐",
    "com.kugou.android": "酷狗音乐",
    "com.spotify.music": "Spotify",

    # 购物
    "com.taobao.taobao": "淘宝",
    "com.jingdong.app.mall": "京东",
    "com.xunmeng.pinduoduo": "拼多多",
    "com.achievo.vipshop": "唯品会",

    # 工具 / 效率
    "com.tencent.mtt": "QQ浏览器",
    "com.UCMobile": "UC浏览器",
    "com.android.chrome": "Chrome",
    "com.microsoft.emmx": "Edge",
    "com.baidu.searchbox": "百度",
    "com.larus.nova": "豆包",
    "com.ss.android.lark.alchemy": "豆包",
    "com.openai.chatgpt": "ChatGPT",
    "com.autonavi.minimap": "高德地图",
    "com.baidu.BaiduMap": "百度地图",

    # 支付 / 金融
    "com.eg.android.AlipayGphone": "支付宝",

    # 外卖 / 生活
    "com.sankuai.meituan": "美团",
    "me.ele": "饿了么",
    "com.dianping.v1": "大众点评",
    "com.Qunar": "去哪儿",

    # 阅读 / 知识
    "com.zhihu.android": "知乎",
    "com.douban.frodo": "豆瓣",
    "com.ss.android.article.news": "今日头条",

    # 游戏
    "com.miHoYo.Yuanshen": "原神",
    "com.miHoYo.hkrpg": "崩坏：星穹铁道",
    "com.tencent.tmgp.sgame": "王者荣耀",
    "com.tencent.tmgp.pubgmhd": "和平精英",

    # MIRROW
    "com.mirrow.app": "MIRROW",

    # 系统 → 应过滤（返回 None）
    "com.android.systemui": None,
    "com.android.launcher": None,
    "com.android.launcher3": None,
    "com.oplus.launcher": None,
    "com.bbk.launcher2": None,
    "com.vivo.launcher": None,
    "com.huawei.android.launcher": None,
    "com.miui.home": None,
    "com.oppo.launcher": None,
    "com.sec.android.app.launcher": None,

    # 屏幕状态
    "screen_off": "锁屏",
    "screen_on": "亮屏",
}


def resolve_app_name(package: str) -> str | None:
    """将 Android 包名解析为中文名。

    - 包名格式（含 '.'）→ 查映射表
    - 映射值为 None → 应过滤（系统桌面等），返回 None
    - 未知包名 → 返回 None，让设备端真实应用标签或原包名兜底
    - 已经是中文名 → 直接返回
    """
    if not package:
        return None
    # 已经是可读名称（非包名格式）
    if "." not in package:
        return package
    # 查映射表
    if package in KNOWN_APPS:
        return KNOWN_APPS[package]
    # 不做厂商级前缀猜测：同一厂商可同时拥有音乐、远控和游戏应用。
    return None


CONFIRMED_GAME_PACKAGES = frozenset({
    "com.miHoYo.Yuanshen",
    "com.miHoYo.hkrpg",
    "com.tencent.tmgp.sgame",
    "com.tencent.tmgp.pubgmhd",
})


def is_confirmed_game_package(package: str | None) -> bool:
    """Only exact, reviewed package identities can assert a mobile game."""
    return bool(package and package in CONFIRMED_GAME_PACKAGES)


def is_filtered_package(package: str | None) -> bool:
    """Whether an exact package is infrastructure rather than user content."""
    return bool(package and package in KNOWN_APPS and KNOWN_APPS[package] is None)
