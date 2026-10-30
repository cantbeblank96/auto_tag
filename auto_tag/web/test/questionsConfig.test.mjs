import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  createQuestionsDocument, detailToQuestion, parseQuestionsDocument,
  questionsFromConfig, questionsToObject, saveImportedQuestions,
  QuestionsImportSavedError,
} from '../src/utils/questionsConfig.ts'

const entry = (key = 'scene') => detailToQuestion(key, {
  description: '判断场景，保留中文', type: 'category', choices: ['室内', '室外'],
  tools: ['face_detect'], examples: { '室内': '/data/参考图.jpg' },
})

test('readable document round-trips disabled questions, tools, examples and mode', () => {
  const question = { ...entry(), enabled: false, _mode: 'freeform' }
  question.freeformJson = JSON.stringify({
    description: '自定义定义', type: 'string', nested: { arbitrary: [1, true] },
    examples: { sample: '/data/a.jpg' }, tools: ['head_pose'],
  })
  const document = createQuestionsDocument([question])
  const text = JSON.stringify(document, null, 2)
  assert.match(text, /\n  "questions"/)
  assert.match(text, /自定义定义/)
  assert.equal(document.questions.scene.nested.arbitrary[1], true)
  const loaded = parseQuestionsDocument(text)
  assert.equal(loaded[0].enabled, false)
  assert.equal(loaded[0]._mode, 'freeform')
  assert.deepEqual(createQuestionsDocument(loaded), document)
  assert.deepEqual(Object.keys(questionsToObject(loaded)), [])
})

test('template export retains custom fields and exports current form edits', () => {
  const question = detailToQuestion('count', {
    description: '旧说明', type: 'int', min: 0, max: 20, custom: { note: '保留' },
  })
  question.description = '新说明'
  question.max = '30'
  const document = createQuestionsDocument([question])
  assert.deepEqual(document.questions.count, {
    description: '新说明', type: 'int', min: 0, max: 30, custom: { note: '保留' },
  })
  assert.deepEqual(createQuestionsDocument(parseQuestionsDocument(JSON.stringify(document))), document)
})

test('invalid inputs reject rather than silently skipping questions', () => {
  const valid = createQuestionsDocument([entry()])
  for (const value of [null, [], {}, { ...valid, version: 2 },
    { ...valid, questions: [] }, { ...valid, questions: { scene: null } },
    { ...valid, questions: { scene: { type: 'string', tools: [3] } } },
    { ...valid, editor: { scene: { enabled: 'yes', mode: 'template' } } },
  ]) assert.throws(() => parseQuestionsDocument(JSON.stringify(value)))
  assert.throws(() => parseQuestionsDocument('{bad json'))
  assert.throws(() => createQuestionsDocument([entry('')]))
  assert.throws(() => createQuestionsDocument([entry(), entry(' scene ')]))
  assert.throws(() => createQuestionsDocument([{ ...entry(), _mode: 'freeform', freeformJson: '[' }]))
  assert.throws(() => createQuestionsDocument([{ ...entry(), _mode: 'freeform', freeformJson: '[]' }]))
  assert.throws(() => createQuestionsDocument([{ ...entry(), type: 'float', min: 'NaN' }]))
})

test('BOM and empty Questions document are supported', () => {
  const document = createQuestionsDocument([])
  assert.deepEqual(parseQuestionsDocument('\uFEFF' + JSON.stringify(document)), [])
})

test('template inputs that cannot round-trip exactly reject before changing definitions', () => {
  for (const patch of [
    { choices: ['a\nb', ' c '] },
    { examples: { a: ['/a.jpg', '/b.jpg'] } },
  ]) {
    const document = JSON.parse(JSON.stringify(createQuestionsDocument([entry()])))
    Object.assign(document.questions.scene, patch)
    assert.throws(() => parseQuestionsDocument(JSON.stringify(document)), /自由/)
    document.editor.scene.mode = 'freeform'
    const loaded = parseQuestionsDocument(JSON.stringify(document))
    assert.deepEqual(
      JSON.parse(JSON.stringify(createQuestionsDocument(loaded))).questions.scene,
      document.questions.scene,
    )
  }
})

test('refresh restores complete editor document and legacy config still loads', () => {
  const question = { ...entry(), enabled: false }
  const document = createQuestionsDocument([question])
  assert.deepEqual(createQuestionsDocument(questionsFromConfig({ questions: {}, questions_editor: document })), document)
  const legacy = questionsFromConfig({ questions: { scene: { description: '旧配置', type: 'string' } } })
  assert.equal(legacy[0].description, '旧配置')
  assert.equal(legacy[0].enabled, true)
})

function fakeServer(t, { writeFails = false, reloadFails = false } = {}) {
  const config = {
    questions: { old: { type: 'string' } }, batch_size: 17,
    vlm_models: [{ api_key: 'unchanged-secret' }],
    annotation_tools: { face_detect: { enabled: false } },
  }
  let saved
  const requests = []
  t.mock.method(globalThis, 'fetch', async (url, init) => {
    requests.push(url)
    if (url.startsWith('/api/utils/read_file?')) return Response.json({ content: JSON.stringify(config) })
    if (url === '/api/utils/write_file') {
      if (writeFails) return Response.json({ detail: 'disk full' }, { status: 500 })
      saved = JSON.parse(JSON.parse(init.body).content)
      return Response.json({ written: true })
    }
    if (url === '/api/utils/reload_config') {
      if (reloadFails) return Response.json({ detail: 'reload failed' }, { status: 500 })
      return Response.json({ reloaded: true })
    }
    throw new Error(`Unexpected request ${url}`)
  })
  return { config, requests, get saved() { return saved } }
}

test('import saves only Questions and hot reloads, retaining full tool bindings in editor', async t => {
  const server = fakeServer(t)
  const result = await saveImportedQuestions('/app/config.json', [entry()])
  assert.equal(server.saved.batch_size, 17)
  assert.deepEqual(server.saved.vlm_models, server.config.vlm_models)
  assert.deepEqual(server.saved.annotation_tools, server.config.annotation_tools)
  assert.equal(server.saved.questions.old, undefined)
  assert.equal(server.saved.questions.scene.tools, undefined)
  assert.deepEqual(server.saved.questions_editor.questions.scene.tools, ['face_detect'])
  assert.deepEqual(JSON.parse(JSON.stringify(result.document)), server.saved.questions_editor)
  assert.deepEqual(JSON.parse(JSON.stringify(result.questions)), server.saved.questions)
  assert.equal(server.requests.at(-1), '/api/utils/reload_config')
})

test('invalid Questions never write or reload', async t => {
  const server = fakeServer(t)
  await assert.rejects(saveImportedQuestions('/app/config.json', [entry('')]))
  assert.deepEqual(server.requests, [])
})

test('write failure does not reload or report a saved import', async t => {
  const server = fakeServer(t, { writeFails: true })
  await assert.rejects(saveImportedQuestions('/app/config.json', [entry()]), /disk full/)
  assert.equal(server.saved, undefined)
  assert.equal(server.requests.includes('/api/utils/reload_config'), false)
})

test('reload failure clearly reports disk success and exposes the saved Questions', async t => {
  const server = fakeServer(t, { reloadFails: true })
  await assert.rejects(saveImportedQuestions('/app/config.json', [entry()]), error => {
    assert.ok(error instanceof QuestionsImportSavedError)
    assert.match(error.message, /已保存.*未生效/)
    assert.deepEqual(JSON.parse(JSON.stringify(error.saved.questions)), server.saved.questions)
    return true
  })
})
