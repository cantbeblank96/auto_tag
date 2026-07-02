import { NavLink, Outlet } from 'react-router-dom'
import { useTheme } from '../ThemeContext'
import { formatAppVersion } from '../constants/version'

const navItems = [
  { to: '/tasks', label: '任务' },
  { to: '/database', label: '数据库' },
  { to: '/image-query', label: '图片查询' },
  { to: '/settings', label: '设置' },
]

export default function Layout() {
  const { theme, toggle } = useTheme()

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-950 flex">
      {/* Sidebar */}
      <aside className="w-56 bg-white dark:bg-gray-900 border-r border-gray-200 dark:border-gray-800 flex flex-col shrink-0">
        <NavLink
          to="/"
          end
          className={({ isActive }) =>
            `block p-4 border-b border-gray-200 dark:border-gray-800 transition-colors ${
              isActive
                ? 'bg-blue-50 dark:bg-blue-900/20'
                : 'hover:bg-gray-50 dark:hover:bg-gray-800/50'
            }`
          }
        >
          {({ isActive }) => (
            <>
              <h1
                className={`text-lg font-bold ${
                  isActive ? 'text-blue-700 dark:text-blue-300' : 'text-gray-800 dark:text-gray-100'
                }`}
              >
                Auto Tag
              </h1>
              <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">标注流水线控制台</p>
            </>
          )}
        </NavLink>
        <nav className="flex-1 p-2 space-y-1">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) =>
                `block px-3 py-2 rounded-md text-sm font-medium transition-colors ${
                  isActive
                    ? 'bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300'
                    : 'text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800'
                }`
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="p-3 border-t border-gray-200 dark:border-gray-800 flex items-center justify-between">
          <button onClick={toggle} className="text-xs text-gray-500 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-200 px-2 py-1 rounded border border-gray-200 dark:border-gray-700 hover:border-gray-400 dark:hover:border-gray-500 transition-colors">
            {theme === 'dark' ? '☀️ 明亮' : '🌙 暗黑'}
          </button>
          <span className="text-xs text-gray-400 dark:text-gray-600">{formatAppVersion()}</span>
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 overflow-auto p-6 text-gray-800 dark:text-gray-200">
        <Outlet />
      </main>
    </div>
  )
}
