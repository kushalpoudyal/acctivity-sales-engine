import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'
import Prospects from './Prospects.jsx'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    {window.location.pathname.replace(/\/$/, '') === '/prospects' ? <Prospects /> : <App />}
  </StrictMode>,
)
