import { createRoot } from 'react-dom/client';
import { StrictMode } from 'react';
import WorkshopPage from '../pages/WorkshopPage';
createRoot(document.getElementById('root')!).render(<StrictMode><WorkshopPage onBack={() => { window.location.href = '/'; }} /></StrictMode>);
