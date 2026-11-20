window.__ModuleLoader__.load({
  id: '@lantai/dsh-core-memory',
  factory(require) {
    const React = require('react')
    const h = React.createElement
    /*__DSH_CORE_MEMORY_INLINE_SOURCES__*/
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
