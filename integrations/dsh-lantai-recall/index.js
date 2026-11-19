export const name = 'lantai-recall'

export function apply(_ctx, config = {}) {
  if (config?.enabled !== true) return
  throw new Error('Lantai automatic recall is unavailable: the host does not provide trusted final-admission, input-source, and workspace data boundaries.')
}
