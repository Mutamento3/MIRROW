import { Component } from 'react';
import type { ErrorInfo, ReactNode } from 'react';

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('ErrorBoundary caught:', error, info.componentStack);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div style={{
          display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
          height: '100vh', padding: '2rem', fontFamily: 'system-ui, sans-serif',
          color: '#e0e0e0', backgroundColor: '#1a1a2e', textAlign: 'center'
        }}>
          <h2 style={{ marginBottom: '1rem' }}>MIRROW 遇到了一个错误</h2>
          <p style={{ color: '#888', marginBottom: '1.5rem', maxWidth: '500px' }}>
            应用发生了未预期的错误。请尝试刷新页面。如果问题持续出现，请联系开发者。
          </p>
          <pre style={{
            fontSize: '0.8rem', color: '#e06060', backgroundColor: '#111',
            padding: '1rem', borderRadius: '8px', maxWidth: '600px', overflow: 'auto',
            whiteSpace: 'pre-wrap', wordBreak: 'break-all'
          }}>
            {this.state.error?.message || '未知错误'}
          </pre>
          <button
            onClick={() => window.location.reload()}
            style={{
              marginTop: '1.5rem', padding: '0.6rem 1.5rem', fontSize: '1rem',
              border: 'none', borderRadius: '8px', backgroundColor: '#3b82f6',
              color: '#fff', cursor: 'pointer'
            }}
          >
            刷新页面
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
