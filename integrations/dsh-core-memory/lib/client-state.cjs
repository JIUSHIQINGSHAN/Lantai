'use strict'

/** A status reread can resolve the effective choice, but cannot acknowledge a write. */
function settleControlResult(action, result, observed) {
  const acknowledged = result?.ok === true ? result.value : null
  const snapshot = observed?.ok === true ? observed.value : acknowledged
  if (action === 'status') {
    if (!snapshot) return { snapshot: null, feedback: 'readFailed', retry: 'status' }
    if (snapshot.reason === 'unavailable') return {
      snapshot, feedback: 'storageUnavailable', retry: snapshot.writable ? false : 'status',
    }
    return { snapshot, feedback: null, retry: null }
  }
  if (acknowledged?.reason === 'ready' && acknowledged.enabled === action) {
    return { snapshot: acknowledged, feedback: action ? 'savedOn' : 'savedOff', retry: null }
  }
  return action
    ? { snapshot, feedback: 'enableUnconfirmed', retry: 'status' }
    : { snapshot, feedback: 'stopUnconfirmed', retry: snapshot?.writable === false ? 'status' : false }
}

exports.settleControlResult = settleControlResult
