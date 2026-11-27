'use strict';

const assert = require('node:assert/strict');

function installedDshManifest(installed) {
  const manifest = installed('./package.json');
  if (manifest.name === '@deepseek-ai/dsh') return manifest;
  assert.equal(manifest.name, '@deepseek-ai/dsh-desktop-runtime', 'unsupported DSH package');
  // Desktop wraps the CLI package; verify both manifests before using its identity.
  assert.equal(manifest.dependencies?.['@deepseek-ai/dsh'], manifest.version, 'DSH package mismatch');
  const runtime = installed('@deepseek-ai/dsh/package.json');
  assert.equal(runtime.name, '@deepseek-ai/dsh', 'DSH package mismatch');
  assert.equal(runtime.version, manifest.version, 'DSH package mismatch');
  return runtime;
}

module.exports = { installedDshManifest };
