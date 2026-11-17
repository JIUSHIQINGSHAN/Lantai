import { startBridge } from './bridge.js';
export { Config } from './config.js';

export const name = 'lantai-attribution';
export const inject = ['tools'];

/** Async apply lets Cordis await initial discovery before admitting model turns. */
export async function apply(ctx, config) {
  const bridge = startBridge(ctx, config);
  ctx.effect(() => () => bridge.dispose(), 'lantai-attribution.connection');
  // Same early-unload hook as the installed dsh-mcp-client: interrupt startup
  // before Cordis waits for an unfinished async apply. This event is internal,
  // verified in the pinned release's source, not in the public Inspect catalog.
  ctx.on('internal/plugin', fiber => {
    if (fiber === ctx.fiber && fiber.uid === null) return bridge.dispose();
  }, { global: true });
  await bridge.ready;
}
