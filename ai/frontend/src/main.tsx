import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import './style.css'

const root = document.getElementById('root')
if (!root) throw new Error('找不到页面挂载节点')

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
