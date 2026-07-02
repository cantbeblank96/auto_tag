import TutorialContent from '../components/TutorialContent'
import SystemInfoSection from '../components/SystemInfoSection'

const chapterTitleCls =
  'text-lg font-semibold text-gray-800 dark:text-gray-100 border-b border-gray-200 dark:border-gray-700 pb-2 mb-4'

export default function Home() {
  return (
    <div>
      <header className="mb-8 max-w-3xl">
        <h2 className="text-2xl font-semibold text-gray-800 dark:text-gray-100 mb-2">欢迎使用 Auto Tag</h2>
        <p className="text-sm text-gray-600 dark:text-gray-400 leading-relaxed">
          这是图像自动标注流水线的控制台入口。你可以从左侧导航进入「任务」提交标注、「数据库」维护索引与导出数据、「图片查询」检索结果、「设置」调整
          VLM 与聚类参数。下方整理了使用教程与系统信息，按需展开阅读。
        </p>
      </header>

      <section className="mb-10">
        <h3 className={chapterTitleCls}>使用教程</h3>
        <TutorialContent />
      </section>

      <section className="mb-6">
        <h3 className={chapterTitleCls}>系统与其他</h3>
        <SystemInfoSection />
      </section>
    </div>
  )
}
