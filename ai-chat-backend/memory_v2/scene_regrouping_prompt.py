"""Grouping contract for frozen derived scene impressions, not raw evidence."""
import json

from .scene_summary_contract import guidance_for_date


def summary_character_budget(parts):
    # A compression target scales with the supplied derived prose, not with
    # an arbitrary quota of events. Original source coverage is independent.
    return max(2500, int(sum(len(part['summary']) for part in parts) * 0.35))


def regroup_messages(metadata, *, active_date=''):
    return [
        {'role': 'system', 'content': guidance_for_date(active_date) +
         '你负责重建一整天的经历边界。输入的 segments 是过细的临时记录片段，'
         '不是已经确认、需要逐条保留的事件。先通读并辨认连续经历，再选择每段经历的编号范围，'
         '最后为每个范围写一个完整的中文段落。一次经历包括起因、往返交流、反复提醒或观察、'
         '实质进展和已知结果；同一生活情境或目标的相邻片段归为一段。分界依据是情境或实质话题'
         '的转移，而不是下一次查岗、下一句回应、下一次工具调用或同一目标的新阶段。'
         'summary 是这段经历的简明印象，通常用数句交代起因、关键变化、双方真实回应和最后已知情况。'
         '所有 summary 的字符数合计以 summary_character_budget 为篇幅目标。把篇幅优先给经历的辨识信息、'
         '先以 target_characters 为篇幅目标，留出表达关键更正和结果的余量。'
         '明确纠正、实质变化和最后已知结果；反复设备采样、屏幕细节和重复话语归纳成一句过程。'
         '原来的逐轮文字、设备细节与重复台词完整保存在消息锚点中；现在把它们归纳成过程，'
         '而不是把各片段依次拼接或复述。经历的辨识细节、明确纠正、身份区别与未知结果仍须保留。'
         '反复查岗围绕同一关切时，概括关注的事、其变化和结果即可。'
         '纯执行回执若没有实际取得材料或本人体验，其诊断句不属于经历梗概；'
         '原消息锚点依旧覆盖它，不代表删掉运行记录。'
         '各段用人类伙伴与 AI 叙述，区分人类伙伴的陈述、AI 的理解与系统观测。素材是派生摘要，'
         '其来源锚点会完整保留；摘要只归纳提供的材料。'
         '输出 segments 表示重新选定的经历范围，每个 end_segment 为该范围的末片段编号，'
         '起点紧接上一个范围末编号；从 1 覆盖到 segment_count 恰好一次，末编号必须等于'
         ' segment_count。不同 boundary_block 是不可跨越的明确边界。仅返回 JSON。'},
        {'role': 'user', 'content': json.dumps(
            {'segment_count': len(metadata), 'summary_character_budget': summary_character_budget(metadata),
             'target_characters': int(summary_character_budget(metadata) * 0.7),
             'segments': metadata}, ensure_ascii=False)},
    ]
