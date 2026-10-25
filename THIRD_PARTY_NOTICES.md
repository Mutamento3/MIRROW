# 第三方来源与许可

MIRROW 自有代码适用根目录 LICENSE。以下派生代码、工具和素材遵循各自许可；本包不是纯 MIT 分发，也不能整体宣称可自由商用。

## AionsHome — MIT

上游：[death34018-hue/AionsHome](https://github.com/death34018-hue/AionsHome)。版权 Copyright (c) 2026 death34018-hue，完整许可见 [LICENSES/AionsHome-MIT.txt](LICENSES/AionsHome-MIT.txt)。

本次按公开范围保留并注明来源的内容包括：

- ai-chat-backend/netease_api.py：网易云接口，沿用已公开音乐模块。
- ai-chat-backend/silicon_perception/collection/ 中的手机应用映射、传感器上下文与通知分类派生部分。
- ai-chat-backend/miband/store.py 的睡眠阶段／分段派生逻辑，以及 Android 中注明来源的手环、传感器和通知采集实现。
- ai-chat-backend/memory_v2/periods.py、recall_evidence.py、recall_routing.py 的日期、排序与证据展开派生逻辑；这不表示整个记忆模块由上游提供。
- 前端普通录音中注明来源的 WAV 编码部分。

会客与留言板保留未接入的扩展边界和来源说明，不附会客执行引擎。淘宝、QQ、群聊／桌游等本轮排除模块不随本包分发。共域沿用既有公开宿主接口范围，不因本次更新扩大为完整会客认证服务。

## eryu — CC BY-NC-SA 4.0

上游：[sebastianevan200-stack/eryu](https://github.com/sebastianevan200-stack/eryu)。ai-chat-backend/music_cochlea/analyze_song.py、enricher.py 为既有公开音乐包中的派生频谱分析文件，按 CC BY-NC-SA 4.0 提供，不适用根目录 MIT 商业授权。

完整许可：[LICENSES/eryu-CC-BY-NC-SA-4.0.txt](LICENSES/eryu-CC-BY-NC-SA-4.0.txt)。本次沿用此前公开包的来源记录和许可，不主张上游其他历史版本许可。

## netease-music-mcp — MIT

ai-chat-backend/music_mcp/ 沿用既有公开可选服务，版权作者 luuu-h，完整许可在该目录 LICENSE。它不是网易云 App 控制失败时的自动回退。不推断未核实的同名上游仓库。

## Agent Tools — MIT

上游：[justinlietz93/agent_tools](https://github.com/justinlietz93/agent_tools)。ai-chat-backend/behavior_scheduler/agent_tools_lib/ 保留其工具实现及 LICENSE，版权 Copyright (c) 2024 Justin Lietz。原公开包的许可位于 README 末尾，本副本单列 LICENSE 防止来源文档筛选时遗漏。

## mubai-ears — MIT

ai-chat-backend/voice_manager/mubai_ears/ 保留随源代码提供的 MIT LICENSE，版权 Copyright (c) 2026 HMH323。该本地听觉处理模块与音乐频谱派生文件分别适用各自许可，不混同归属。

## 共域通用装饰资源

ai-chat-backend/social_feed/decor_presets/ 恢复此前已公开的通用资源，不含生产相册或私人装饰图片。Great Vibes、马善政字体及 Kenney 素材的具体来源、OFL／CC0 许可保留在该目录的 SOURCES、README 和许可文件中。

## Android 与其他依赖

Gradle Wrapper 的 Apache-2.0 许可沿用既有公开包，见 [LICENSES/gradle-Apache-2.0.txt](LICENSES/gradle-Apache-2.0.txt)。本候选不附 Wrapper 二进制、SDK、签名、APK 或模型权重。React、Vite、Capacitor、FastAPI、HTTPX、音频分析等外部依赖由包管理器安装，遵循其各自许可；不能因依赖存在就推断其代码已内嵌。
