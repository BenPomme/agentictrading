import { Component, type ReactNode } from 'react';

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
  name?: string;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error) {
    console.error(`[ErrorBoundary${this.props.name ? ':' + this.props.name : ''}]`, error);
  }

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) return this.props.fallback;
      return (
        <div style={{
          padding: '1rem',
          background: 'var(--crit-dim)',
          border: '1px solid var(--crit)',
          borderRadius: 'var(--radius)',
          color: 'var(--crit)',
          fontFamily: 'var(--font-mono)',
          fontSize: '0.75rem',
        }}>
          <strong>{this.props.name ?? 'Component'} error</strong>
          <br />
          {this.state.error?.message}
        </div>
      );
    }
    return this.props.children;
  }
}
