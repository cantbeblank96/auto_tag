import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from './ThemeContext'
import Layout from './components/Layout'
import Home from './pages/Home'
import Tasks from './pages/Tasks'
import ImageQuery from './pages/ImageQuery'
import Database from './pages/Database'
import Settings from './pages/Settings'

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
              <Route path="/" element={<Home />} />
              <Route path="/tasks" element={<Tasks />} />
              <Route path="/tasks/history" element={<Navigate to="/tasks" replace />} />
              <Route path="/image-query" element={<ImageQuery />} />
              <Route path="/database" element={<Database />} />
              <Route path="/settings" element={<Settings />} />
              <Route path="/tutorial" element={<Navigate to="/" replace />} />
              <Route path="/about" element={<Navigate to="/" replace />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </ThemeProvider>
    </QueryClientProvider>
  )
}

export default App
