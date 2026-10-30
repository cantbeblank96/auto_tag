import assert from 'node:assert/strict'
import { test } from 'node:test'

test('the configuration macro resolves to the active backend config, not a different default file', async t => {
  t.mock.method(globalThis, 'fetch', async () => Response.json({
    project_path: '/app/auto_tag', config_path: '/app/auto_tag/custom/config.json',
  }))
  const config = await import('../src/constants/config.ts?active-path-test')
  assert.equal(await config.resolveMacroPath(config.DEFAULT_CONFIG_PATH), '/app/auto_tag/custom/config.json')
  assert.equal(await config.resolveMacroPath('{PROJECT_PATH}/work_dir'), '/app/auto_tag/work_dir')
})

test('older backend paths responses retain the default config path', async t => {
  t.mock.method(globalThis, 'fetch', async () => Response.json({ project_path: '/app/auto_tag' }))
  const config = await import('../src/constants/config.ts?default-path-test')
  assert.equal(await config.resolveMacroPath(config.DEFAULT_CONFIG_PATH), '/app/auto_tag/config.json')
})
