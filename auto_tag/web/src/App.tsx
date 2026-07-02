import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from './ThemeContext'
import Layout from './components/Layout'
import Tasks from './pages/Tasks'
import TaskHistory from './pages/TaskHistory'
import ImageQuery from './pages/ImageQuery'
import Database from './pages/Database'
import Settings from './pages/Settings'
import Tutorial from './pages/Tutorial'
import About from './pages/About'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
})

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <BrowserRouter>
          <Routes>
            <Route element={<Layout />}>
              <Route path="/" element={<Tasks />} />
              <Route path="/tasks" element={<Tasks />} />
              <Route path="/tasks/history" element={<TaskHistory />} />
              <Route path="/image-query" element={<ImageQuery />} />
              <Route path="/database" element={<Database />} />
              <Route path="/settings" element={<Settings />} />
              <Route path="/tutorial" element={<Tutorial />} />
              <Route path="/about" element={<About />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </ThemeProvider>
    </QueryClientProvider>
  )
}

export default App