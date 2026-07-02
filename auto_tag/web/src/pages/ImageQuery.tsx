import { useState } from 'react'
import { api, type ByPathResponse } from '../api/client'

export default function ImageQuery() {
  const [imagePath, setImagePath] = useState('')
  const [workDir, setWorkDir] = useState('')
  const [yuvW, setYuvW] = useState(640)
  const [yuvH, setYuvH] = useState(480)
  const [yuvType, setYuvType] = useState('nv21')
  const [bYuvPreview, setBYuvPreview] = useState(false)

  const [result, setResult] = useState<ByPathResponse | null>(null)
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const [labelText, setLabelText] = useState('')
  const [mode, setMode] = useState<'image_only' | 'with_cluster'>('image_only')
  const [loading, setLoading] = useState(false)
  const [msg, setMsg] = useState('')

  const showMsg = (text: string) => {
    setMsg(text)
    setTimeout(() => setMsg(''), 5000)
  }

  const handleQuery = async () => {
    if (!imagePath.trim()) { showMsg('请填写路径'); return }
    setLoading(true)
    setPreviewUrl(null)
    try {
      const res = await api.recordByPath({
        image_path: imagePath.trim(),
        ...(workDir.trim() ? { work_dir: workDir.trim() } : {}),
      })
      setResult(res)
      // Auto-populate label text
      if (res.found && res.effective_labels) {
        setLabelText(JSON.stringify(res.effective_labels, null, 2))
      } else {
        setLabelText('{}')
      }
    } catch (e: any) {
      showMsg(`查询失败: ${e.message}`)
    } finally {
      setLoading(false)
    }
  }

  const handlePreview = async () => {
    const path = result?.matched_path || imagePath.trim()
    if (!path) { showMsg('请先查询或填写路径'); return }
    try {
      const blob = await api.previewImage({
        image_path: path,
        ...(workDir.trim() ? { work_dir: workDir.trim() } : {}),
        image_width: yuvW,
        image_height: yuvH,
        yuv_type: yuvType,
        b_yuv_image: bYuvPreview,
      })
      setPreviewUrl(URL.createObjectURL(blob))
    } catch (e: any) {
      showMsg(`预览失败: ${e.message}`)
    }
  }

  const handleSaveLabels = async () => {
    const path = result?.matched_path || imagePath.trim()
    if (!path) { showMsg('请先查询'); return }
    try {
      const labels = JSON.parse(labelText)
      const res = await api.updateRecordLabels({
        image_path: path,
        labels,
        mode,
        ...(workDir.trim() ? { work_dir: workDir.trim() } : {}),
        image_width: yuvW,
        image_height: yuvH,
        yuv_type: yuvType,
        b_yuv_image: bYuvPreview,
      })
      showMsg(`已更新：${JSON.stringify(res)}`)
      setResult(null)
      setPreviewUrl(null)
    } catch (e: any) {
      showMsg(`保存失败: ${e.message}`)
    }
  }

  const handleInsert = async () => {
    if (!imagePath.trim()) { showMsg('请填写路径'); return }
    try {
      const labels = JSON.parse(labelText)
      const res = await api.updateRecordLabels({
        image_path: imagePath.trim(),
        labels,
        mode: 'image_only',
        ...(workDir.trim() ? { work_dir: workDir.trim() } : {}),
        image_width: yuvW,
        image_height: yuvH,
        yuv_type: yuvType,
        b_yuv_image: bYuvPreview,
      })
      showMsg(`已处理：${JSON.stringify(res)}`)
      setResult(null)
      setPreviewUrl(null)
    } catch (e: any) {
      showMsg(`插入失败: ${e.message}`)
    }
  }

  return (
    <div>
      <h2 className="text-2xl font-semibold text-gray-800 dark:text-gray-100 mb-2">图片查询</h2>
      <p className="text-xs text-gray-500 dark:text-gray-400 mb-4">
        按 image_path 查询：优先向量索引；若无记录再查 log 下近重复侧车表（不占索引空间）。
      </p>

      {msg && <div className="mb-4 px-4 py-2 rounded text-sm bg-blue-50 text-blue-700 border border-blue-200">{msg}</div>}

      <section className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4 mb-6 max-w-2xl">
        <div className="space-y-3">
          <div>
            <label className="block text-sm text-gray-600 mb-1">图片绝对路径</label>
            <input type="text" value={imagePath} onChange={e => setImagePath(e.target.value)} className="w-full border rounded px-3 py-2 text-sm font-mono" />
          </div>
          <div>
            <label className="block text-sm text-gray-600 mb-1">work_dir（可选，不填则使用服务端默认配置）</label>
            <input type="text" value={workDir} onChange={e => setWorkDir(e.target.value)} placeholder="/path/to/work_dir" className="w-full border rounded px-3 py-2 text-sm font-mono" />
          </div>
          <p className="text-xs text-gray-400">若未入库且为 YUV，可在下方填写解码参数后再点预览。</p>
          <div className="flex gap-4">
            <div>
              <label className="block text-xs text-gray-500">预览 YUV 宽</label>
              <input type="number" value={yuvW} onChange={e => setYuvW(Number(e.target.value))} className="border rounded px-2 py-1 text-sm w-20" />
            </div>
            <div>
              <label className="block text-xs text-gray-500">预览 YUV 高</label>
              <input type="number" value={yuvH} onChange={e => setYuvH(Number(e.target.value))} className="border rounded px-2 py-1 text-sm w-20" />
            </div>
            <div>
              <label className="block text-xs text-gray-500">预览 YUV 类型</label>
              <select value={yuvType} onChange={e => setYuvType(e.target.value)} className="border rounded px-2 py-1 text-sm">
                <option value="nv21">nv21</option>
                <option value="nv12">nv12</option>
                <option value="yuv420p">yuv420p</option>
              </select>
            </div>
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={bYuvPreview} onChange={e => setBYuvPreview(e.target.checked)} />
            强制按 YUV 解码预览
          </label>
          <button onClick={handleQuery} disabled={loading} className="px-4 py-2 text-sm bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50">
            {loading ? '查询中...' : '查询'}
          </button>
        </div>
      </section>

      {/* Query Result */}
      {result && result.found && (
        <section className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4 mb-6 max-w-3xl">
          {result.source === 'stage1_duplicate_only' ? (
            <>
              <h3 className="text-sm font-medium text-green-700 mb-3">近重复侧车（未写入向量索引）</h3>
              {result.note && <p className="text-xs text-gray-500 mb-2">{result.note}</p>}
              <pre className="text-xs bg-gray-50 dark:bg-gray-900 p-3 rounded border border-gray-200 dark:border-gray-600 overflow-x-auto mb-4">
                {JSON.stringify({
                  source: result.source,
                  matched_path: result.matched_path,
                  duplicate_links: result.duplicate_links,
                }, null, 2)}
              </pre>
              {result.anchor_embedding_records && result.anchor_embedding_records.length > 0 && (
                <>
                  <h4 className="text-sm font-medium text-gray-700 mb-2">锚点图在索引中的结果（自动查询）</h4>
                  {result.anchor_embedding_records.map((a: any, i: number) => (
                    <pre key={i} className="text-xs bg-gray-50 dark:bg-gray-900 p-3 rounded border border-gray-200 dark:border-gray-600 overflow-x-auto mb-2">
                      {JSON.stringify({
                        anchor_path: a.anchor_path,
                        ...a.embedding_record,
                      }, null, 2)}
                    </pre>
                  ))}
                </>
              )}
              <p className="text-xs text-gray-400 mb-3">
                此类路径不写入向量索引。编辑簇标签请使用锚点查询结果中的 effective_labels；
                若要将本路径作为新中心入库，请用下方「无索引记录」流程插入。
              </p>
            </>
          ) : (
            <>
              <h3 className="text-sm font-medium text-gray-700 mb-3">索引记录摘要</h3>
              <pre className="text-xs bg-gray-50 dark:bg-gray-900 p-3 rounded border border-gray-200 dark:border-gray-600 overflow-x-auto mb-4">
                {JSON.stringify({
                  matched_path: result.matched_path,
                  cluster_id: result.cluster_id,
                  is_cluster_center: result.is_cluster_center,
                  cluster_center_path: result.cluster_center_path,
                  own_labels: result.own_labels,
                  effective_labels: result.effective_labels,
                  cluster_center_labels: result.cluster_center_labels,
                }, null, 2)}
              </pre>

              <h4 className="text-sm font-medium text-gray-700 mb-2">编辑 labels 并写回库</h4>
              <p className="text-xs text-gray-400 mb-2">
                整簇同步：更新该图片及其所属 cluster 内全部文档的 labels；
                仅本图：只更新该路径对应文档的 labels。
              </p>
              <div className="flex gap-4 mb-3">
                <label className="flex items-center gap-2 text-sm">
                  <input type="radio" name="mode" checked={mode === 'image_only'} onChange={() => setMode('image_only')} />
                  仅本图（无记录则新插入）
                </label>
                <label className="flex items-center gap-2 text-sm">
                  <input type="radio" name="mode" checked={mode === 'with_cluster'} onChange={() => setMode('with_cluster')} />
                  整簇同步
                </label>
              </div>
            </>
          )}

          {/* Preview button */}
          <button onClick={handlePreview} className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50 mb-3">
            加载预览图
          </button>

          {/* Label editor */}
          <div>
            <label className="block text-sm text-gray-600 mb-1">labels（JSON）</label>
            <textarea value={labelText} onChange={e => setLabelText(e.target.value)} className="w-full border rounded px-3 py-2 text-sm font-mono mb-3" rows={8} />
            <button onClick={handleSaveLabels} className="px-4 py-2 text-sm bg-blue-600 text-white rounded hover:bg-blue-700">
              保存到数据库
            </button>
          </div>
        </section>
      )}

      {/* Not found */}
      {result && !result.found && (
        <section className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4 mb-6 max-w-3xl">
          <p className="text-sm text-gray-500 mb-3">
            向量索引与近重复侧车均未找到该路径；若文件在服务端磁盘上存在，仍可按下方参数尝试预览或插入。
          </p>
          <button onClick={handlePreview} className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50 mb-4">
            加载预览图（仅磁盘）
          </button>
          <h4 className="text-sm font-medium text-gray-700 mb-2">无索引记录时直接写入 labels</h4>
          <p className="text-xs text-gray-400 mb-2">
            后端会读磁盘图片、CLIP 提特征后新增一条索引记录（新簇中心）。
          </p>
          <textarea value={labelText} onChange={e => setLabelText(e.target.value)} className="w-full border rounded px-3 py-2 text-sm font-mono mb-3" rows={6} />
          <button onClick={handleInsert} className="px-4 py-2 text-sm bg-blue-600 text-white rounded hover:bg-blue-700">
            插入带 labels 的新条目
          </button>
        </section>
      )}

      {/* Preview image */}
      {previewUrl && (
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4 max-w-3xl">
          <img src={previewUrl} alt="预览" className="max-w-full h-auto" />
        </div>
      )}
    </div>
  )
}