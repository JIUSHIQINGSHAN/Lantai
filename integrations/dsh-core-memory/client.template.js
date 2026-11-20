window.__ModuleLoader__.load({
  id: '@lantai/dsh-core-memory',
  factory(require) {
    const React = require('react')
    const h = React.createElement
    /*__DSH_CORE_MEMORY_INLINE_SOURCES__*/
    const namespace = 'lantai-core-memory.client'
    const slotName = 'settings.general.item'
    const writeAction = 'lantaiCoreMemory/setEnabled'
    const initialView = { snapshot: null, busy: 'status', feedback: null, retry: null }
    const styles = `
      .lantai-memory-setting {
        display: grid;
        grid-template-columns: minmax(0, 1fr) auto;
        align-items: start;
        gap: 8px clamp(12px, 4%, 24px);
        padding: 16px 0;
        border-block-end: .5px solid var(--dsw-alias-border-l2);
      }
      .lantai-memory-setting__title { font-size: 14px; line-height: 20px; }
      .lantai-memory-setting__detail {
        margin: 4px 0 0;
        color: var(--dsw-alias-label-secondary);
        font-size: 12px;
        line-height: 18px;
        overflow-wrap: anywhere;
      }
      .lantai-memory-setting__switch {
        position: relative;
        inline-size: 44px;
        block-size: 44px;
        padding: 0;
        border: 0;
        border-radius: 8px;
        background: transparent;
        cursor: pointer;
      }
      .lantai-memory-setting__track {
        position: absolute;
        inset-block-start: 6px;
        inset-inline-start: 2px;
        box-sizing: border-box;
        inline-size: 40px;
        block-size: 20px;
        border: 1px solid var(--dsw-alias-label-tertiary);
        border-radius: 12px;
        background: var(--dsw-alias-state-idle-primary);
      }
      .lantai-memory-setting__track::before {
        content: '';
        position: absolute;
        inset-block-start: 1px;
        inset-inline-start: 1px;
        box-sizing: border-box;
        inline-size: 16px;
        block-size: 16px;
        border: 1px solid var(--dsw-alias-border-l2);
        border-radius: 50%;
        background: var(--dsw-alias-switch-thumb);
      }
      .lantai-memory-setting__switch[aria-checked='true'] .lantai-memory-setting__track {
        background: var(--dsw-alias-state-business-primary);
        border-color: var(--dsw-alias-state-business-primary);
      }
      .lantai-memory-setting__switch[aria-checked='true'] .lantai-memory-setting__track::before {
        inset-inline-start: auto;
        inset-inline-end: 1px;
      }
      .lantai-memory-setting__retry {
        align-self: start;
        min-block-size: 24px;
        max-inline-size: 100%;
        margin-block-start: 4px;
        padding: 0;
        border: 0;
        border-radius: 4px;
        background: transparent;
        color: var(--dsw-alias-link);
        font: inherit;
        font-size: 12px;
        text-align: start;
        text-decoration: underline;
        overflow-wrap: anywhere;
        cursor: pointer;
      }
      .lantai-memory-setting [role='status']:empty { margin: 0; }
      .lantai-memory-setting button[aria-disabled='true'] { cursor: default; opacity: .65; }
      .lantai-memory-setting button:focus-visible {
        outline: 2px solid var(--dsw-alias-state-business-primary);
        outline-offset: 2px;
      }
    `

    async function invoke(operation, controller) {
      try {
        const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(8000)])
        return await operation(signal)
      } catch { return { ok: false } }
    }

    function MemorySetting({ t, remote }) {
      const titleId = React.useId()
      const detailsId = React.useId()
      const request = React.useRef(null)
      const control = React.useRef(null)
      const retryButton = React.useRef(null)
      const [view, setView] = React.useState(initialView)
      const run = React.useCallback(async action => {
        if (request.current !== null || !['status', true, false].includes(action)) return
        const controller = new AbortController()
        request.current = controller
        setView(previous => ({ ...previous, busy: action }))
        try {
          const result = await invoke(signal => action === 'status'
            ? remote.status(signal)
            : remote.setEnabled(action, signal), controller)
          if (controller.signal.aborted || request.current !== controller) return
          let next = settleControlResult(action, result)
          if (typeof action === 'boolean' && next.retry !== null) {
            const observed = await invoke(signal => remote.status(signal), controller)
            if (controller.signal.aborted || request.current !== controller) return
            next = settleControlResult(action, result, observed)
          }
          if (next.retry === null && retryButton.current?.matches(':focus')) control.current?.focus()
          setView({ ...next, busy: null })
        } finally {
          if (request.current === controller) request.current = null
        }
      }, [remote])

      React.useEffect(() => {
        setView(initialView)
        void run('status')
        return () => {
          request.current?.abort()
          request.current = null
        }
      }, [run])

      const busy = view.busy !== null
      const blocked = busy || view.snapshot?.writable !== true || view.snapshot.reason === 'unavailable' || view.retry === false
      const feedback = busy ? (view.busy === 'status' ? 'reading' : 'saving') : view.feedback ||
        (view.snapshot?.reason === 'scope_changed' ? 'scopeChanged' : view.snapshot?.writable === false ? 'readOnly' : null)
      return h('div', { className: 'lantai-memory-setting' },
        h('style', null, styles),
        h('div', null,
          h('div', { id: titleId, className: 'lantai-memory-setting__title' }, t('title')),
          h('div', { id: detailsId },
            h('p', { className: 'lantai-memory-setting__detail' }, t('description')),
            view.snapshot && h('p', { className: 'lantai-memory-setting__detail' }, t(view.snapshot.includesTask ? 'scopeWithTask' : 'scopeCore'))),
          h('div', { className: 'lantai-memory-setting__detail', role: 'status', 'aria-atomic': 'true' }, feedback && t(feedback)),
          view.retry !== null && h('button', {
            ref: retryButton, type: 'button', className: 'lantai-memory-setting__retry', 'aria-disabled': busy,
            onClick: () => { if (!busy) void run(view.retry) },
          }, t(view.retry === false ? 'retryStop' : 'retryRead'))),
        h('button', {
          ref: control, type: 'button', role: 'switch', className: 'lantai-memory-setting__switch',
          'aria-labelledby': titleId, 'aria-describedby': detailsId,
          'aria-checked': view.snapshot?.enabled === true, 'aria-disabled': blocked,
          onClick: () => { if (!blocked) void run(!view.snapshot?.enabled) },
        }, h('span', { className: 'lantai-memory-setting__track', 'aria-hidden': 'true' })))
    }

    return {
      inject: ['slots', 'remote', 'remoteAccess', 'locale'],
      async apply(ctx) {
        ctx.effect(() => ctx.locale.register(namespace, clientDictionaries), 'lantai-core-memory.client.locale')
        const unmount = await ctx.remote.$mount(createControlContribution())
        ctx.effect(() => async () => { await unmount() }, 'lantai-core-memory.client.remote')
        ctx.inject(['remote.lantaiCoreMemory'], remoteCtx => {
          remoteCtx.effect(() => remoteCtx.remoteAccess.whenAccess(writeAction, () =>
            remoteCtx.slots.inject(slotName, () => remoteCtx.slots.register({
              name: slotName, id: 'lantai-core-memory-setting', order: 20, locale: namespace,
            }, ({ t }) => h(MemorySetting, { t, remote: remoteCtx.remote.lantaiCoreMemory }))),
          ), 'lantai-core-memory.client.settings')
        })
      },
    }
  },
})
