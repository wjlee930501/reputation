import assert from 'node:assert/strict'
import {
  ADMIN_SCHEDULE_OBSERVER_LABELS,
  RESOURCE_404_CONSOLE_TEXT,
  classifyBrowserErrors,
  recordExpectedOptionalScheduleAbsence,
} from './browser_error_classifier.mjs'

const hospitalId = '16000000-0000-0000-0000-000000000001'
const otherHospitalId = '16000000-0000-0000-0000-000000000002'
const adminOrigin = 'http://localhost:3901'
const expectedUrl = `${adminOrigin}/api/admin/hospitals/${hospitalId}/schedule`
const config = { adminOrigin, hospitalId }
const response = (overrides = {}) => ({
  label: 'admin', method: 'GET', status: 404, url: expectedUrl, ...overrides,
})
const consoleError = (overrides = {}) => ({
  label: 'admin', type: 'console', text: RESOURCE_404_CONSOLE_TEXT,
  location: { url: expectedUrl, lineNumber: 0, columnNumber: 0 }, ...overrides,
})

assert.deepEqual([...ADMIN_SCHEDULE_OBSERVER_LABELS], ['admin', 'admin-pass'])

for (const label of ['admin', 'admin-pass']) {
  const expected = new Map()
  assert.equal(recordExpectedOptionalScheduleAbsence(response({ label }), expected, config), true)
  const result = classifyBrowserErrors([consoleError({ label })], expected)
  assert.equal(result.classified.length, 1)
  assert.equal(result.unexpected.length, 0)
  assert.equal(expected.size, 0)
}

for (const label of ['public', 'admin-auth-preflight']) {
  const expected = new Map()
  assert.equal(recordExpectedOptionalScheduleAbsence(response({ label }), expected, config), false)
  assert.equal(classifyBrowserErrors([consoleError({ label })], expected).unexpected.length, 1)
}

const rejectedResponses = [
  response({ url: `http://admin-new:3001/api/admin/hospitals/${hospitalId}/schedule` }),
  response({ url: `${adminOrigin}/api/admin/hospitals/${otherHospitalId}/schedule` }),
  response({ url: `${adminOrigin}/api/admin/hospitals/${hospitalId}/content` }),
  response({ url: `${expectedUrl}?include=inactive` }),
  response({ method: 'POST' }),
  response({ status: 200 }),
]
for (const rejected of rejectedResponses) {
  assert.equal(recordExpectedOptionalScheduleAbsence(rejected, new Map(), config), false)
}

const expectedForWrongConsole = new Map()
assert.equal(recordExpectedOptionalScheduleAbsence(response(), expectedForWrongConsole, config), true)
const unrelatedErrors = [
  consoleError({ text: 'unrelated application console failure' }),
  consoleError({ location: { url: `${adminOrigin}/favicon.ico`, lineNumber: 0, columnNumber: 0 } }),
  consoleError({ label: 'public' }),
  { label: 'admin', type: 'pageerror', text: RESOURCE_404_CONSOLE_TEXT, location: { url: expectedUrl } },
]
for (const error of unrelatedErrors) {
  assert.equal(classifyBrowserErrors([error], new Map(expectedForWrongConsole)).unexpected.length, 1)
}

const singleResponse = new Map()
recordExpectedOptionalScheduleAbsence(response(), singleResponse, config)
const oneToOne = classifyBrowserErrors([consoleError(), consoleError()], singleResponse)
assert.equal(oneToOne.classified.length, 1)
assert.equal(oneToOne.unexpected.length, 1)
assert.equal(singleResponse.size, 0)

console.log(JSON.stringify({
  status: 'passed',
  coveredObserverLabels: ['public', 'admin-auth-preflight', 'admin-pass', 'admin'],
  classifiedLabels: [...ADMIN_SCHEDULE_OBSERVER_LABELS],
  rejectedDimensions: ['origin', 'tenant', 'path', 'query', 'method', 'status', 'console-text', 'console-location', 'label', 'type'],
  oneToOneCorrelation: true,
}))
