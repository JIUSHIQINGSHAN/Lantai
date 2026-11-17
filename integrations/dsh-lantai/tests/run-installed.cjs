// Offline runner: use only the already installed DSH dependencies. No profile reads.
const { createRequire, registerHooks } = require('node:module');
const { pathToFileURL } = require('node:url');
const fs = require('node:fs');
const path = require('node:path');
if (!process.env.DSH_INSTALL_DIR) throw new Error('Run in a DSH shell (DSH_INSTALL_DIR is required)');
const anchor = createRequire(path.join(process.env.DSH_INSTALL_DIR, 'package.json'));
registerHooks({
  resolve(specifier, context, nextResolve) {
    try { return nextResolve(specifier, context); }
    catch (error) {
      if (error.code !== 'ERR_MODULE_NOT_FOUND' || !specifier.startsWith('@')) throw error;
      let file = anchor.resolve(specifier);
      if (file.endsWith('.cjs') && fs.existsSync(file.slice(0, -4) + '.mjs')) file = file.slice(0, -4) + '.mjs';
      return nextResolve(pathToFileURL(file).href, context);
    }
  },
});
// Bound fixtures even when an assertion fails before their cleanup is installed.
const watchdog = setTimeout(() => { console.error('Installed smoke exceeded its 20-second budget'); process.exit(1); }, 20000);
watchdog.unref();
import(pathToFileURL(path.join(__dirname, 'smoke.test.js')).href).catch(error => {
  console.error(error);
  process.exitCode = 1;
});
