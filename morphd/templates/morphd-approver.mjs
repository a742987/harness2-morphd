// @morphd/approver — a controlled dsh approval answerer.
// Loaded only in offline morphd profiles. It never enters a candidate harness tree.
import { appendFileSync, readFileSync } from 'node:fs'

export const name = 'morphd-approver'

function readConfig() {
  const path = process.env.MORPHD_APPROVER_CONFIG
  if (!path) return {}
  try {
    return JSON.parse(readFileSync(path, 'utf8'))
  } catch {
    return {}
  }
}

function commandOf(req) {
  try {
    const session = req.agent?.session
    if (!session || !req.callId) return ''
    for (let seq = session.seq - 1; seq >= 0; seq -= 1) {
      const event = session.eventAt(seq)
      if (event?.type === 'tool/call' && event.data?.callId === req.callId) {
        return String(event.data.arguments ?? '')
      }
    }
  } catch {
    return ''
  }
  return ''
}

function log(record) {
  const path = process.env.MORPHD_APPROVALS_LOG
  if (!path) return
  try {
    appendFileSync(path, JSON.stringify(record) + '\n')
  } catch {
    // Logging must never turn a decision into a crash.
  }
}

export function apply(ctx) {
  const cfg = readConfig()
  const allowTools = new Set((cfg.allow_tools ?? []).map((s) => String(s).toLowerCase()))
  const denyPatterns = (cfg.deny_command_patterns ?? []).map((s) => String(s).toLowerCase())
  const allowPrefixes = (cfg.allow_command_prefixes ?? []).map((s) => String(s).toLowerCase())
  const denyNetwork = cfg.deny_network !== false
  const networkPatterns = ['http://', 'https://', 'curl ', 'wget ', 'socket', 'ssh ', 'scp ', 'nc ']

  const decide = (req) => {
    const tool = String(req.toolName ?? '').toLowerCase()
    const reason = String(req.reason ?? '')
    const command = commandOf(req)
    const haystack = `${reason}\n${command}`.toLowerCase()
    if (!allowTools.has(tool)) {
      return { outcome: 'rejected', rule: 'tool-not-allowlisted' }
    }
    for (const pattern of denyPatterns) {
      if (pattern && haystack.includes(pattern)) {
        return { outcome: 'rejected', rule: 'deny-pattern' }
      }
    }
    if (denyNetwork && networkPatterns.some((p) => haystack.includes(p))) {
      return { outcome: 'rejected', rule: 'network-denied' }
    }
    if (allowPrefixes.length > 0 && command) {
      const first = command.trim().toLowerCase()
      if (!allowPrefixes.some((prefix) => first.startsWith(prefix))) {
        return { outcome: 'rejected', rule: 'prefix-not-allowlisted' }
      }
    }
    return { outcome: 'allowed-once', rule: 'allowlisted' }
  }

  ctx.on('approval/request', async (req) => {
    let decision
    try {
      decision = decide(req)
    } catch {
      decision = { outcome: 'rejected', rule: 'decider-error' }
    }
    log({
      ts: new Date().toISOString(),
      tool: String(req.toolName ?? ''),
      reason: String(req.reason ?? '').slice(0, 2000),
      call_id: req.callId ?? null,
      command: commandOf(req).slice(0, 4000),
      decision: decision.outcome,
      rule: decision.rule,
    })
    return decision.outcome
  })
}
