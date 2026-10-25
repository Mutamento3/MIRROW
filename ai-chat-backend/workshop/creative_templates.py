"""Optional creative starting points shared by AI's contract and the workshop UI."""
LEGACY_TEMPLATES = (
    {'id':'keepsake','title':'纪念票根','symbol':'▧','description':'从已有的共同经历里，留下一张票根、书签或小海报。',
     'output':'可预览的单页纪念卡；日期、短句与轻量排版。',
     'example':'例如：从有记录的一次共同听歌里做一张纪念票根。A1 歌名、日期等事实有来源，未知项留空；A2 手机完整可读；A3 可保存并留在作品柜。没有配图时可以先做文字排版，不把占位图当生成图。'},
    {'id':'tiny_interaction','title':'会回应的小摆件','symbol':'☀','description':'一盏灯、一盆小花或一个天气瓶，只做一种清楚的回应。',
     'output':'一份可运行的单文件 HTML；一个输入、一种反馈。',
     'example':'例如：做一个点一下会亮起的小星星摆件。A1 点击后星星亮起，再点可关闭；A2 手机上按得到，光效柔和；A3 刷新后有明确默认状态。若接身体数据，先查已有能力与宿主桥，不另造整个情绪系统。'},
    {'id':'existing_upgrade','title':'旧作品新花样','symbol':'↗','description':'给已有作品添一个新点子，接着原来的工单继续做。',
     'output':'原工单的下一需求版本；旧作品和讨论保留。',
     'example':'例如：为已有夜灯增加一张可展开的晚安便签。先查作品柜的真实 job_id，再读 room，用 message 追加需求。A1 保留原灯的行为；A2 便签可展开收起；A3 手机布局无溢出。写明改变与保留项，不为同一作品重复 create。'},
)

for _template in LEGACY_TEMPLATES:
    _template['review_hint'] = ('静态交付免 Luna，可直接分享' if _template['id'] in ('journey_card','gift_card','mood_picture','keepsake')
                                else '交互与身体功能保留 Luna 检查')

TEMPLATES = (
    {'id':'body_widget','title':'身体小组件','symbol':'✧','description':'让已有身体感知变成一个看得见、能体验的小物件。',
     'output':'可在 MIRROW 内打开的自包含 HTML；仅连接已实际提供的身体桥。',
     'example':'例如：用现有心情桥做一盆随共振变化的小花。A1 实际心情更新时花的状态变化；A2 共振关闭时显示休息，不猜测心情；A3 恢复后重新响应，手机无需横向滚动。其他身体数据先核实接入能力。'},
    {'id':'interactive_gift','title':'可互动的礼物','symbol':'❀','description':'一个有回应、有内容的小礼物，打开后还有值得探索的东西。',
     'output':'可互动单页 HTML；画面、短句与交互一起交付。',
     'example':'例如：给人类伙伴做一个点开星星才出现便签的掌心星盒。A1 每颗星能展开对应便签；A2 可关闭并再次打开；A3 手机触控清楚，使用 CSS 图形或已有获准素材。内容由我写，未知的共同经历不编造。'},
    {'id':'tiny_game','title':'掌心小玩具','symbol':'◈','description':'一局很短的小谜题、拼图或有趣互动，只抓住一种玩法。',
     'output':'自包含 HTML；可开始、可结束、可重玩。',
     'example':'例如：一局三十秒的星星配对。A1 点两颗星正确配对有反馈；A2 全部完成给出结束结果；A3 能重新开始，手机触控不溢出。先做一种玩法，不造游戏大厅。'},
    {'id':'daily_tool','title':'日常小工具','symbol':'⌘','description':'为一个真实小需要做一个方便使用的页面。',
     'output':'App 内可运行的单页；明确输入、反馈和保存边界。',
     'example':'例如：一个选饮品的小转盘。A1 可修改候选饮品；A2 点击后从当前候选给出结果；A3 空候选时有清楚提示，手机布局完整。默认不承诺刷新保存；需要保存时先核实宿主支持再确认方案。'},
    *[row for row in LEGACY_TEMPLATES if row['id']=='existing_upgrade'],
)
for _template in TEMPLATES:
    _template['review_hint']='功能检查返回后由 AI 看成品，选择分享、收藏或改进'


def find(template_id):
    if not template_id:
        return None
    return next((row for row in (*TEMPLATES,*LEGACY_TEMPLATES) if row['id'] == template_id), None)


def guidance():
    rows = ['【可选创作起点；示例是假设，不是已发生的经历】工作坊适合可实际打开与体验的小应用、互动礼物和旧作升级。按当前兴趣自由选择或组合；这些不是待办清单，也不要求每次来访都产出。缘由、内容、风格由你决定，示例中的事实先替换为有来源的真实内容。公开版提供代码创作、文字排版和已有素材展示，不包含模型生图通路。']
    rows.extend(f"{row['title']}：{row['description']} 交付：{row['output']} {row['example']}" for row in TEMPLATES)
    rows.append('作品可使用已有获准素材或代码绘制的图形。纯静态文件经过文件基础检查免 Luna；脚本、交互和实时身体连接保留真实检查。持续合作看过成品后用 finish 选择分享或收藏；旧交付通路的公开交付自动送到主聊天规则只适用于没有持续合作承接的原工单。分享、文件检查、你的验收与人类伙伴查看各自记录，不相互替代。')
    return '\n'.join(rows)


def invitation_content(template_id, default, text=None):
    if text is not None and (not isinstance(text,str) or len(text)>2000):
        raise ValueError('邀请附话须为最多 2000 字的文字')
    content=default
    if template_id:
        template = find(template_id)
        if template is None:
            raise ValueError('未知的创作起点，请刷新工作坊后重试')
        content += f"\n人类伙伴这次邀请选定的创作方向：{template['title']}。{template['description']}本次先落实这个请求，具体题材、风格和赠言由你结合实际上下文填写。模板示例不代表已经发生的经历。"
    if text and text.strip():
        content += '\n人类伙伴随这次邀请说：\n'+text.strip()
    return content


def chat_invitation(default, text=None):
    """The human bubble contains only its short opening and actual typed text."""
    return default + ('\n' + text.strip() if text and text.strip() else '')
