// Consume the authoritative topic read view. Names and search keywords are not classifications.
export type InterestTopic = {
  domain: string; subject_id: string; entry_id: string; types: string[];
  areas: string[]; classification: string;
};
export const ALL_INTERESTS = '__all_interests';
export const UNCLASSIFIED = '__unclassified';
const labels: Record<string, string> = {
  music: '音乐', gaming: '游戏', writing: '创作', reading: '阅读',
  general: '综合兴趣', film: '影视', crafts: '手工',
};
export function interestIndex(topics: InterestTopic[]) {
  const own = topics.filter(t => t.domain === 'self' && t.subject_id === 'k');
  const interests = own.filter(t => t.types.includes('interest'));
  const areas = [...new Set(interests.flatMap(t => t.areas))].sort();
  // Keep music available for the existing experience-record view even with no taste entry.
  if (!areas.includes('music')) areas.unshift('music');
  return [
    { id: ALL_INTERESTS, label: '全部兴趣', entryIds: [...new Set(interests.map(t => t.entry_id))] },
    ...areas.map(area => ({ id: area, label: labels[area] || area,
      entryIds: [...new Set(interests.filter(t => t.areas.includes(area)).map(t => t.entry_id))] })),
    { id: UNCLASSIFIED, label: '未分类书页',
      entryIds: [...new Set(own.filter(t => t.classification === 'unclassified').map(t => t.entry_id))] },
  ];
}
