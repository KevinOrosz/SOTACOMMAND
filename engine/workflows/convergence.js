export const meta = {
  name: 'convergence',
  description: 'Independent passes over the same input, then one synthesizer that lists where they converge and where they contradict',
  whenToUse: 'A claim, plan or decision worth checking from several independent angles before acting on it',
  phases: [
    { title: 'Passes', detail: 'one agent per lens, same input, no shared context' },
    { title: 'Synthesis', detail: 'one agent compares the passes without forcing agreement' },
  ],
}

// Source: adapters/templates/engine/workflows/convergence.js, copied into
// engine/workflows/ by adapters/generate.py. Edit the template, then regenerate.
//
// Run as /sotacommand-engine:convergence. Pass args as a string (the material to
// examine) or as an object:
//   { input: string, lenses?: string[], passes?: number, agentType?: string }
// lenses: one pass per lens (default: evidence, risk, alternatives).
// passes: that many identical independent passes, used when lenses is absent.
// agentType: run the passes as this subagent type instead of the default.

const DEFAULT_LENSES = [
  'evidence: what the input directly supports, and the evidence for each claim',
  'risk: what in the input could be wrong, missing or fragile',
  'alternatives: which other reading or approach fits the same input as well',
]
const MIN_PASSES = 2
const MAX_PASSES = 8

const CONFIDENCE = { type: 'string', enum: ['low', 'moderate', 'high'] }

const PASS_SCHEMA = {
  type: 'object',
  required: ['claims', 'open_questions', 'summary'],
  properties: {
    claims: {
      type: 'array',
      items: {
        type: 'object',
        required: ['claim', 'evidence', 'confidence'],
        properties: {
          claim: { type: 'string' },
          evidence: { type: 'string' },
          confidence: CONFIDENCE,
        },
      },
    },
    open_questions: { type: 'array', items: { type: 'string' } },
    summary: { type: 'string' },
  },
}

const SYNTHESIS_SCHEMA = {
  type: 'object',
  required: ['converged', 'contradictions', 'unique', 'open_questions', 'summary'],
  properties: {
    converged: {
      type: 'array',
      items: {
        type: 'object',
        required: ['claim', 'passes', 'confidence'],
        properties: {
          claim: { type: 'string' },
          passes: { type: 'array', items: { type: 'string' } },
          confidence: CONFIDENCE,
        },
      },
    },
    contradictions: {
      type: 'array',
      items: {
        type: 'object',
        required: ['topic', 'positions'],
        properties: {
          topic: { type: 'string' },
          positions: {
            type: 'array',
            items: {
              type: 'object',
              required: ['passes', 'position'],
              properties: {
                passes: { type: 'array', items: { type: 'string' } },
                position: { type: 'string' },
              },
            },
          },
        },
      },
    },
    unique: {
      type: 'array',
      items: {
        type: 'object',
        required: ['claim', 'pass'],
        properties: { claim: { type: 'string' }, pass: { type: 'string' } },
      },
    },
    open_questions: { type: 'array', items: { type: 'string' } },
    summary: { type: 'string' },
  },
}

function readArgs(value) {
  if (typeof value === 'string') {
    return { input: value.trim(), lenses: DEFAULT_LENSES, agentType: undefined }
  }
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return { input: '', lenses: DEFAULT_LENSES, agentType: undefined }
  }
  const input = typeof value.input === 'string' ? value.input.trim() : ''
  let lenses = DEFAULT_LENSES
  if (Array.isArray(value.lenses)) {
    lenses = value.lenses.filter(lens => typeof lens === 'string' && lens.trim()).map(lens => lens.trim())
  } else if (Number.isInteger(value.passes)) {
    lenses = Array.from({ length: value.passes }, (_, i) =>
      `independent pass ${i + 1}: assess the input on its own terms`)
  }
  const agentType = typeof value.agentType === 'string' && value.agentType.trim() ? value.agentType.trim() : undefined
  return { input, lenses, agentType }
}

function passPrompt(input, lens) {
  return [
    'You are one of several independent reviewers. You cannot see the other reviews, and nobody will reconcile your view with theirs for you.',
    `Your lens: ${lens}`,
    'Work read-only: read files or sources the input points to, but do not create, edit or delete anything.',
    'List the claims you can make about the input through your lens. Give the evidence for each and an honest confidence. Leave out anything you cannot support.',
    'Material to examine:',
    '<<<INPUT',
    input,
    'INPUT>>>',
  ].join('\n\n')
}

function synthesisPrompt(input, passes) {
  return [
    'You are the single synthesizer for independent reviews of the same material. Compare them; do not review the material again yourself.',
    'Rules:',
    '- converged: a claim that two or more passes support independently. Name every pass that supports it.',
    '- contradictions: claims that cannot both hold. Give each position and the passes that hold it. Do not resolve or average them.',
    '- unique: a claim only one pass makes. It is not agreement, however plausible it is.',
    '- Never manufacture agreement and never add claims of your own. When the passes disagree, say so.',
    'Material the passes examined:',
    '<<<INPUT',
    input,
    'INPUT>>>',
    'Passes, as JSON:',
    JSON.stringify(passes, null, 2),
  ].join('\n\n')
}

const { input, lenses: requested, agentType } = readArgs(args)
if (!input) {
  throw new Error('convergence: pass the material to examine as args (a string) or as args.input')
}
let lenses = requested
if (lenses.length > MAX_PASSES) {
  log(`convergence: ${lenses.length} lenses requested; running the first ${MAX_PASSES}, dropping ${lenses.length - MAX_PASSES}`)
  lenses = lenses.slice(0, MAX_PASSES)
}
if (lenses.length < MIN_PASSES) {
  throw new Error(`convergence: needs at least ${MIN_PASSES} independent passes; got ${lenses.length}`)
}

phase('Passes')
// A stage receives (prevResult, originalItem, index); originalItem is the lens.
const results = await pipeline(lenses, (value, lens, index) => {
  const opts = { label: `pass-${index + 1}`, phase: 'Passes', schema: PASS_SCHEMA }
  if (agentType) opts.agentType = agentType
  return agent(passPrompt(input, typeof lens === 'string' ? lens : value), opts)
})

const passes = []
results.forEach((result, index) => {
  if (result) passes.push({ pass: `pass-${index + 1}`, lens: lenses[index], ...result })
})
const dropped = lenses.length - passes.length
if (dropped > 0) {
  log(`convergence: ${dropped} of ${lenses.length} passes returned nothing and are left out`)
}
if (passes.length < MIN_PASSES) {
  return { status: 'insufficient', reason: `only ${passes.length} pass(es) completed; convergence needs ${MIN_PASSES}`, passes }
}

phase('Synthesis')
const synthesis = await agent(synthesisPrompt(input, passes), {
  label: 'synthesizer',
  phase: 'Synthesis',
  schema: SYNTHESIS_SCHEMA,
})

return { status: synthesis ? 'complete' : 'synthesis-missing', passes, dropped, synthesis }
