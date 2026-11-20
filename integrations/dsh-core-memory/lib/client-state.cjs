'use strict'

/** Settle a user control operation without mistaking a lost reply for revoked access. */
function settleControlResult(previous, sessionId, method, result) {
  if (result && result.ok === true) {
    return { sessionId, snapshot: result.value, busy: null, uncertain: false, error: null }
  }
  const same = previous?.sessionId === sessionId
  const snapshot = same ? previous.snapshot : null
  const uncertain = Boolean((same && previous.uncertain) || snapshot?.authorized || method === 'authorize' || method === 'revoke')
  return { sessionId, snapshot, busy: null, uncertain, error: method === 'revoke' ? 'stopUnconfirmed' : 'unavailable' }
}

exports.settleControlResult = settleControlResult
