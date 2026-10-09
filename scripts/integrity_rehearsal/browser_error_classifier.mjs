export const ADMIN_SCHEDULE_OBSERVER_LABELS = new Set(['admin', 'admin-pass'])

export const RESOURCE_404_CONSOLE_TEXT =
  'Failed to load resource: the server responded with a status of 404 (Not Found)'

export function recordExpectedOptionalScheduleAbsence(observation, expectedResponses, config) {
  const responseUrl = new URL(observation.url)
  const expectedPath = `/api/admin/hospitals/${config.hospitalId}/schedule`
  if (
    !ADMIN_SCHEDULE_OBSERVER_LABELS.has(observation.label)
    || observation.method !== 'GET'
    || observation.status !== 404
    || responseUrl.origin !== config.adminOrigin
    || responseUrl.pathname !== expectedPath
    || responseUrl.search !== ''
  ) return false

  expectedResponses.set(observation.url, {
    ...observation,
    reason: 'The mixed-version fixture deliberately deactivates its schedule after old-writer coverage.',
  })
  return true
}

export function classifyBrowserErrors(errors, expectedResponses) {
  const classified = []
  const unexpected = errors.filter((error) => {
    if (
      !ADMIN_SCHEDULE_OBSERVER_LABELS.has(error.label)
      || error.type !== 'console'
      || error.text !== RESOURCE_404_CONSOLE_TEXT
    ) return true

    const locationUrl = error.location?.url
    const response = locationUrl && expectedResponses.get(locationUrl)
    if (!response || response.label !== error.label) return true

    classified.push({ ...response, consoleLocation: error.location })
    expectedResponses.delete(locationUrl)
    return false
  })
  return { classified, unexpected }
}
