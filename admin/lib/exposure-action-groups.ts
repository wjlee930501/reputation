import type { ExposureAction } from '../types/index.ts'

export interface ExposureActionGroup {
  readonly key: string
  readonly representative: ExposureAction
  readonly actions: readonly ExposureAction[]
  readonly questionCount: number
  readonly commonOwner: string | null
  readonly commonDueMonth: string | null
}

/** Merge repeated cards by diagnosis/action type while retaining every question task. */
export function groupExposureActions(actions: readonly ExposureAction[]): ExposureActionGroup[] {
  const grouped = new Map<string, ExposureAction[]>()
  for (const action of actions) {
    const key = `${action.action_type}:${action.gap_type ?? 'UNGROUPED'}`
    const members = grouped.get(key) ?? []
    members.push(action)
    grouped.set(key, members)
  }
  return [...grouped.entries()].map(([key, members]) => {
    const representative = members.find((action) => action.status !== 'COMPLETED') ?? members[0]
    const owners = new Set(members.map((action) => action.owner ?? ''))
    const dueMonths = new Set(members.map((action) => action.due_month ?? ''))
    return {
      key,
      representative,
      actions: members,
      questionCount: new Set(
        members.map((action) => action.query_target?.id ?? action.query_target_id ?? action.id),
      ).size,
      commonOwner: owners.size === 1 ? (members[0].owner ?? null) : null,
      commonDueMonth: dueMonths.size === 1 ? (members[0].due_month ?? null) : null,
    }
  })
}
