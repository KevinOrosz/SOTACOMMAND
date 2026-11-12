// Runs a Claude Code workflow script against stub runtime functions, for tests.
//
// Usage: node workflow_harness.js <script.js> <scenario.json>
// The scenario JSON may set: args, failPasses (pass indexes whose agent returns
// null) and synthesisNull. Prints one JSON object: ok, result or error, meta,
// and every agent(), log() and phase() call the script made.
//
// The stubs follow the workflow authoring reference: pipeline(items, ...stages)
// calls each stage with (prevResult, originalItem, index) and turns a throwing
// stage into null; Date.now() and Math.random() throw, as in the real runtime.
'use strict'

const fs = require('fs')
const vm = require('vm')

const [scriptPath, scenarioPath] = process.argv.slice(2)
const scenario = JSON.parse(fs.readFileSync(scenarioPath, 'utf8'))
const source = fs.readFileSync(scriptPath, 'utf8')
const calls = []
const logs = []
const phases = []

function finish(payload) {
  process.stdout.write(JSON.stringify(Object.assign({ calls, logs, phases }, payload)) + '\n')
}

if (!source.startsWith('export const meta = {')) {
  finish({ ok: false, error: 'script does not begin with export const meta' })
  process.exit(0)
}

async function agent(prompt, opts) {
  calls.push({ prompt, opts: opts || {} })
  const label = (opts && opts.label) || ''
  if (label.startsWith('pass-')) {
    const index = Number(label.slice('pass-'.length)) - 1
    if ((scenario.failPasses || []).includes(index)) return null
    return {
      claims: [{ claim: `claim from ${label}`, evidence: `evidence from ${label}`, confidence: 'moderate' }],
      open_questions: [],
      summary: `summary from ${label}`,
    }
  }
  if (label === 'synthesizer') {
    if (scenario.synthesisNull) return null
    return { converged: [], contradictions: [], unique: [], open_questions: [], summary: 'stub synthesis' }
  }
  return null
}

async function pipeline(items, ...stages) {
  return Promise.all(items.map(async (item, index) => {
    let value = item
    for (const stage of stages) {
      try {
        value = await stage(value, item, index)
      } catch (error) {
        return null
      }
    }
    return value
  }))
}

async function parallel(thunks) {
  return Promise.all(thunks.map(async thunk => {
    try {
      return await thunk()
    } catch (error) {
      return null
    }
  }))
}

const context = vm.createContext({
  agent,
  pipeline,
  parallel,
  log: message => logs.push(String(message)),
  phase: title => phases.push(String(title)),
  args: scenario.args,
})
vm.runInContext(
  "Date.now = () => { throw new Error('Date.now is unavailable in workflows') };" +
  "Math.random = () => { throw new Error('Math.random is unavailable in workflows') };",
  context,
)

const body = source.replace(/^export const meta = /, 'const meta = globalThis.__meta = ')
const wrapped = '(async () => {\n' + body + '\n})()'

let running
try {
  running = vm.runInContext(wrapped, context, { filename: scriptPath })
} catch (error) {
  finish({ ok: false, error: 'syntax: ' + error.message })
  process.exit(0)
}
running.then(
  result => finish({ ok: true, result, meta: context.__meta }),
  error => finish({ ok: false, error: String(error && error.message), meta: context.__meta }),
)
