window.__ModuleLoader__.load({
  id: '@lantai/dsh-core-memory',
  factory(require) {
    const React = require('react')
    const h = React.createElement
    const contractModule = { exports: {} }
;(function(module, exports) {
'use strict'

// Shared by Host ESM and the Client lazy factory. Keep this file dependency-free.
function createControlContribution() {
  const sessionId = {
    mode: 'strict', typeSymbol: '@lantai/dsh-core-memory.control.sessionId.string',
    create: () => ({ parse(value) {
      if (typeof value !== 'string' || value.length < 1 || value.length > 1024 || /[\u0000-\u001f\u007f-\u009f]/u.test(value)) {
        throw new TypeError('Invalid sessionId')
      }
      return value
    } }),
  }
  const metadata = {
    mode: 'strict', typeSymbol: '@lantai/dsh-core-memory.control.metadata',
    create: () => ({ parse(value) {
      const invalid = () => { throw new TypeError('Invalid control metadata') }
      const object = (input, required, optional = []) => {
        if (input === null || typeof input !== 'object' || ![Object.prototype, null].includes(Object.getPrototypeOf(input))) invalid()
        const fields = Object.getOwnPropertyDescriptors(input)
        if (Reflect.ownKeys(fields).some(key => typeof key !== 'string' || ![...required, ...optional].includes(key) || !Object.hasOwn(fields[key], 'value') || !fields[key].enumerable)) invalid()
        if (required.some(key => !Object.hasOwn(fields, key))) invalid()
        return Object.fromEntries(Object.entries(fields).map(([key, field]) => [key, field.value]))
      }
      const result = object(value, ['state', 'reason', 'authorized', 'blockCount', 'contextBytes', 'versions'], ['expiresAt', 'outcome'])
      if (typeof result.state !== 'string' || typeof result.reason !== 'string' || typeof result.authorized !== 'boolean') invalid()
      if (![result.blockCount, result.contextBytes].every(count => Number.isSafeInteger(count) && count >= 0)) invalid()
      if (Object.hasOwn(result, 'expiresAt') && !Number.isSafeInteger(result.expiresAt)) invalid()
      if (Object.hasOwn(result, 'outcome') && typeof result.outcome !== 'string') invalid()
      if (!Array.isArray(result.versions)) invalid()
      const entries = Object.getOwnPropertyDescriptors(result.versions)
      if (Reflect.ownKeys(entries).length !== result.versions.length + 1) invalid()
      result.versions = Array.from({ length: result.versions.length }, (_, index) => {
        const entry = entries[index]
        if (!entry || !Object.hasOwn(entry, 'value') || !entry.enumerable) invalid()
        const version = object(entry.value, ['block', 'version'])
        if (!['policy', 'identity', 'task'].includes(version.block) || !Number.isSafeInteger(version.version) || version.version < 1) invalid()
        return version
      })
      return result
    } }),
  }
  return {
    package: '@lantai/dsh-core-memory',
    descriptors: ['authorize', 'revoke', 'status'].map(method => ({
      id: `@lantai/dsh-core-memory#lantaiCoreMemory/${method}`,
      service: 'lantaiCoreMemory', namespace: 'lantaiCoreMemory', method,
      invocation: { kind: 'direct' },
      parameters: [{ name: 'sessionId', wire: 'sessionId', source: 'json', codec: sessionId }],
      cancellation: { parameter: 'signal' }, result: metadata,
    })),
  }
}

exports.createControlContribution = createControlContribution

})(contractModule, contractModule.exports)
const { createControlContribution } = contractModule.exports
const stateModule = { exports: {} }
;(function(module, exports) {
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

})(stateModule, stateModule.exports)
const { settleControlResult } = stateModule.exports
const clientDictionaries = {
  "zh": {
    "groupLabel": "当前对话的兰台记忆权限",
    "unknown": "状态待确认",
    "authorized": "已允许读取",
    "notAuthorized": "尚未允许",
    "loaded": "记忆已载入",
    "empty": "暂无可读资料",
    "allow": "允许记忆",
    "reload": "更新记忆",
    "stop": "停止",
    "allowDetail": "允许当前对话读取已配置的兰台长期规则和个人资料",
    "stopDetail": "停止后续读取；已经出现的聊天内容仍会保留",
    "busy": "正在处理…",
    "unavailable": "连接需要检查，请让助手继续核对",
    "noAgent": "请先在此对话发一条消息，再点允许",
    "notBound": "当前文件夹尚未配置记忆连接",
    "needsSetup": "连接信息需要核对",
    "identityMismatch": "资料归属需要核对，暂未载入",
    "scopeChanged": "已换文件夹，请重新允许",
    "stopped": "已停止读取",
    "stopUnconfirmed": "停止尚未确认，请再点停止重试",
    "cancelled": "本次读取已取消",
    "timeout": "读取超时，可重试",
    "hint": "权限只作用于当前对话。允许后，指定的长期规则和个人资料会交给此对话所用的模型；停止读取不会删除聊天历史。"
  },
  "en": {
    "groupLabel": "Lantai memory access for this conversation",
    "unknown": "Status unconfirmed",
    "authorized": "Access allowed",
    "notAuthorized": "Not allowed yet",
    "loaded": "Memory loaded",
    "empty": "No readable memory",
    "allow": "Allow memory",
    "reload": "Reload memory",
    "stop": "Stop",
    "allowDetail": "Allow this conversation to read the configured Lantai user policies and profile",
    "stopDetail": "Stop further reads; content already in chat history remains",
    "busy": "Working…",
    "unavailable": "The connection needs checking. Ask the assistant to continue setup.",
    "noAgent": "Send one message in this conversation, then allow memory.",
    "notBound": "Memory is not configured for this folder.",
    "needsSetup": "Connection settings need checking",
    "identityMismatch": "Memory ownership needs checking; nothing was loaded.",
    "scopeChanged": "Folder changed; allow access again.",
    "stopped": "Further reads stopped",
    "stopUnconfirmed": "Stopping is not confirmed. Press Stop to retry.",
    "cancelled": "This read was cancelled",
    "timeout": "Read timed out; try again.",
    "hint": "Permission applies only to this conversation. The configured user policies and profile will be sent to its model. Stopping access does not delete chat history."
  }
}
    const namespace = 'lantai-core-memory.client'
    const slotName = 'conversation.input.right'
    const authorizeAction = 'lantaiCoreMemory/authorize'
    const validId = value => typeof value === 'string' && value.length > 0

    function messageKey(snapshot) {
      if (!snapshot) return 'unknown'
      if (snapshot.state === 'session_not_active' || snapshot.reason === 'open_session_first') return 'noAgent'
      if (snapshot.state === 'not_bound' || snapshot.reason === 'workspace_not_configured') return 'notBound'
      if (snapshot.authorized) return snapshot.state === 'empty' ? 'empty' : snapshot.state === 'ready' ? 'loaded' : 'authorized'
      const reasons = {
        authorization_required: 'notAuthorized', authorization_revoked: 'stopped', cancelled: 'cancelled',
        scope_changed: 'scopeChanged', timeout: 'timeout', missing_credentials: 'needsSetup',
        credential_source_invalid: 'needsSetup', credential_source_unavailable: 'needsSetup',
        authentication_failed: 'needsSetup', ownership_mismatch: 'identityMismatch',
        ambiguous_blocks: 'identityMismatch', invalid_response: 'identityMismatch',
      }
      return reasons[snapshot.reason] || 'unavailable'
    }

    function ConsentControl({ sessionId, t, remote }) {
      const [stored, setStored] = React.useState(() => ({ sessionId, snapshot: null, busy: 'status', uncertain: false, error: null }))
      const request = React.useRef(null)
      const mounted = React.useRef(false)
      const view = stored.sessionId === sessionId ? stored : { sessionId, snapshot: null, busy: 'status', uncertain: false, error: null }
      React.useEffect(() => {
        if (!validId(sessionId)) return undefined
        mounted.current = true
        const controller = new AbortController()
        request.current?.abort()
        request.current = controller
        setStored({ sessionId, snapshot: null, busy: 'status', uncertain: false, error: null })
        Promise.resolve().then(() => remote.status(sessionId, controller.signal)).then(result => {
          if (!mounted.current || controller.signal.aborted || request.current !== controller) return
          setStored(previous => settleControlResult(previous, sessionId, 'status', result))
        }).catch(() => {
          if (mounted.current && !controller.signal.aborted && request.current === controller) setStored({ sessionId, snapshot: null, busy: null, error: 'unavailable' })
        }).finally(() => { if (request.current === controller) request.current = null })
        return () => {
          mounted.current = false
          const pending = request.current
          request.current = null
          pending?.abort()
          controller.abort()
        }
      }, [sessionId, remote])

      async function run(method) {
        if (!mounted.current || view.busy !== null || !validId(sessionId)) return
        const controller = new AbortController()
        request.current?.abort()
        request.current = controller
        setStored(previous => ({ sessionId, snapshot: previous.sessionId === sessionId ? previous.snapshot : null, uncertain: previous.sessionId === sessionId && previous.uncertain, busy: method, error: null }))
        try {
          const result = await remote[method](sessionId, controller.signal)
          if (!mounted.current || controller.signal.aborted || request.current !== controller) return
          setStored(previous => settleControlResult(previous, sessionId, method, result))
        } catch {
          if (mounted.current && !controller.signal.aborted && request.current === controller) setStored(previous => settleControlResult(previous, sessionId, method, { ok: false }))
        } finally { if (request.current === controller) request.current = null }
      }

      if (!validId(sessionId)) return null
      const snapshot = view.snapshot
      const label = view.error || (view.busy ? 'busy' : messageKey(snapshot))
      const buttonStyle = { color: 'inherit', background: 'transparent', border: '1px solid currentColor', borderRadius: '0.3rem', padding: '0.2rem 0.4rem', font: 'inherit' }
      return h('span', {
        role: 'group', 'aria-label': t('groupLabel'), 'aria-busy': view.busy !== null,
        title: t('hint'),
        style: { display: 'inline-flex', alignItems: 'center', gap: '0.35rem', maxWidth: '100%', color: 'inherit', flexWrap: 'wrap' },
      },
      h('span', { role: view.error ? 'alert' : 'status', 'aria-live': 'polite' }, t(label)),
      h('button', {
        type: 'button', disabled: view.busy !== null, title: t('allowDetail'),
        onClick: () => { void run('authorize') }, style: buttonStyle,
      }, t(snapshot?.authorized ? 'reload' : 'allow')),
      (snapshot?.authorized || view.uncertain) && h('button', {
        type: 'button', disabled: view.busy !== null, title: t('stopDetail'),
        onClick: () => { void run('revoke') }, style: buttonStyle,
      }, t('stop')))
    }

    return {
      inject: ['slots', 'remote', 'remoteAccess', 'locale'],
      async apply(ctx) {
        ctx.effect(() => ctx.locale.register(namespace, clientDictionaries), 'lantai-core-memory.client.locale')
        const unmount = await ctx.remote.$mount(createControlContribution())
        ctx.effect(() => async () => { await unmount() }, 'lantai-core-memory.client.remote')
        ctx.inject(['remote.lantaiCoreMemory'], remoteCtx => {
          remoteCtx.effect(() => remoteCtx.remoteAccess.whenAccess(authorizeAction, () =>
            remoteCtx.slots.inject(slotName, () => remoteCtx.slots.register({
              name: slotName, id: 'lantai-core-memory-consent', order: 0, locale: namespace,
            }, props => validId(props.sessionId) ? h(ConsentControl, {
              key: props.sessionId, sessionId: props.sessionId, t: props.t, remote: remoteCtx.remote.lantaiCoreMemory,
            }) : null)),
          ), 'lantai-core-memory.client.owner-consent')
        })
      },
    }
  },
})
