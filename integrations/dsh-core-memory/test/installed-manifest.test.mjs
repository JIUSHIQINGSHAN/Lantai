import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
import { installedDshManifest } from './installed-manifest.cjs';

async function fixture(t, rootManifest, childManifest) {
  const directory = await mkdtemp(path.join(tmpdir(), 'lantai-manifest-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  await writeFile(path.join(directory, 'package.json'), JSON.stringify(rootManifest));
  if (childManifest) {
    const child = path.join(directory, 'node_modules/@deepseek-ai/dsh');
    await mkdir(child, { recursive: true });
    await writeFile(path.join(child, 'package.json'), JSON.stringify(childManifest));
  }
  return createRequire(path.join(directory, 'package.json'));
}

const dsh = { name: '@deepseek-ai/dsh', version: '0.2.2-rc.5' };
const desktop = {
  name: '@deepseek-ai/dsh-desktop-runtime', version: '0.2.2-rc.5',
  dependencies: { '@deepseek-ai/dsh': '0.2.2-rc.5' },
};

test('the original DSH package keeps its exact manifest identity', async t => {
  assert.deepEqual(installedDshManifest(await fixture(t, dsh)), dsh);
});

test('the desktop wrapper resolves the actual same-version DSH manifest', async t => {
  assert.deepEqual(installedDshManifest(await fixture(t, desktop, dsh)), dsh);
});

test('an unrelated wrapper cannot supply the DSH runtime identity', async t => {
  const installed = await fixture(t, { ...desktop, name: '@example/unrelated' }, dsh);
  assert.throws(() => installedDshManifest(installed), /unsupported DSH package/);
});

test('the desktop declaration and installed DSH must both match the wrapper version', async t => {
  for (const [outer, inner] of [
    [{ ...desktop, dependencies: { '@deepseek-ai/dsh': '0.2.1' } }, dsh],
    [desktop, { ...dsh, version: '0.2.1' }],
    [desktop, { ...dsh, name: '@example/unrelated' }],
  ]) {
    const installed = await fixture(t, outer, inner);
    assert.throws(() => installedDshManifest(installed), /DSH package mismatch/);
  }
});
