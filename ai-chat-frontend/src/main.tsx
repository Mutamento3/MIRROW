import './index.css'
import './mobile.css'
import { initializeBackendEndpoint } from './config'

async function bootstrap() {
  // 先确定手机后端端点，再导入会在模块级缓存 API_BASE 的业务模块。
  await initializeBackendEndpoint()

  const [react, reactDom, appModule, errorModule, chatModule, overlayModule] = await Promise.all([
    import('react'),
    import('react-dom/client'),
    import('./App.tsx'),
    import('./ErrorBoundary.tsx'),
    import('./context/chat'),
    import('./components/MirrorLaunchOverlay'),
  ])
  const { StrictMode } = react
  const { createRoot } = reactDom
  const App = appModule.default
  const { ErrorBoundary } = errorModule
  const { ChatProvider } = chatModule
  const MirrorLaunchOverlay = overlayModule.default

  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <ErrorBoundary>
        <ChatProvider>
          <MirrorLaunchOverlay />
          <App />
        </ChatProvider>
      </ErrorBoundary>
    </StrictMode>,
  )
}

void bootstrap()
