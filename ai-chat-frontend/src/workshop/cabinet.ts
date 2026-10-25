import type { Artifact } from './types';

export type Work = Artifact & { lightweight_ready?:boolean; job_id: string; job_title: string; is_current: boolean; accepted_by_k: boolean; created_at: string; view_count: number; last_viewed_at: string | null; job_archived: boolean; verification: { status: string; actions_count: number } | null };
export type PrivateArchive = { id: string; state: 'accepted'; visibility: 'private'; created_at: string; updated_at: string; accepted_at: string | null };
export type Catalog = { artifacts: Work[]; total: number; has_more: boolean; visits: { visit_count: number; last_visited_at: string | null }; private_archives?: { jobs: PrivateArchive[]; total: number; offset: number; has_more: boolean } };

export const displayable = (work: {name:string; presentation_kind?:string}) => work.presentation_kind
  ? work.presentation_kind !== 'supporting' : /\.(png|jpe?g|webp|gif|html?|txt|md)$/i.test(work.name);

/** Collections retain every delivery, newest requirement version first. */
export function exhibits(works: Work[]) {
  const groups = new Map<string, Work[]>();
  for (const work of works.filter(displayable)) groups.set(work.job_id, [...(groups.get(work.job_id) || []), work]);
  return [...groups.values()].map(versions => {
    versions.sort((a, b) => b.revision - a.revision || b.created_at.localeCompare(a.created_at) || b.id.localeCompare(a.id));
    return { work: versions[0], versions };
  });
}
