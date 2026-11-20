import { open as nodeOpen } from 'node:fs/promises'
import path from 'node:path'
import { CoreMemoryError } from './core.js'
import { requireOwnerInvocation } from './access.js'

const fail = code => { throw new CoreMemoryError(code) }
const MAX_SOURCE_BYTES = 16384

/** Read one operator-selected local legacy key only inside an authorized owner operation.
 * The returned value is Host-only and must go straight to the credential store.
 */
export async function readAuthorizedCredentialSource(options, deps = {}) {
  const { invocation, authorize, signal = invocation?.signal } = options
  const check = () => {
    requireOwnerInvocation(invocation)
    let permitted = false
    try { permitted = typeof authorize === 'function' && authorize() === true } catch { /* Unknown grant is denial. */ }
    if (!permitted) fail('authorization_required')
    if (signal?.aborted) fail('aborted')
  }
  check()
  const { file, expectedHost, remotePort } = options
  if (typeof file !== 'string' || file.length > 4096 || !path.isAbsolute(file) || file.startsWith('\\\\') || /[\u0000-\u001f\u007f]/u.test(file) || typeof expectedHost !== 'string' || !Number.isSafeInteger(remotePort)) fail('credential_source_invalid')
  let handle
  try {
    handle = await (deps.open ?? nodeOpen)(file, 'r')
    check()
    const info = await handle.stat()
    if (!info.isFile() || info.size < 0 || info.size > MAX_SOURCE_BYTES) fail('credential_source_invalid')
    const buffer = Buffer.alloc(MAX_SOURCE_BYTES + 1)
    let size = 0
    while (size <= MAX_SOURCE_BYTES) {
      check()
      const part = await handle.read(buffer, size, buffer.length - size, null)
      if (part.bytesRead === 0) break
      size += part.bytesRead
    }
    if (size > MAX_SOURCE_BYTES) fail('credential_source_invalid')
    check()
    let data
    try { data = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(buffer.subarray(0, size))) }
    catch { fail('credential_source_invalid') }
    const port = typeof data?.port === 'number' || typeof data?.port === 'string' && /^\d+$/u.test(data.port) ? Number(data.port) : NaN
    if (!data || typeof data !== 'object' || Array.isArray(data) || data.host !== expectedHost || port !== remotePort || typeof data.api_key !== 'string' || !/^[\x21-\x7e]{1,8192}$/u.test(data.api_key)) fail('credential_source_invalid')
    check()
    return data.api_key
  } catch (error) {
    if (error instanceof CoreMemoryError) throw error
    fail('credential_source_unavailable')
  } finally {
    if (handle) {
      try { await handle.close() } catch { fail('credential_source_unavailable') }
    }
  }
}
