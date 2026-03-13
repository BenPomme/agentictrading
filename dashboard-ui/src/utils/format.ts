export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 0) return 'just now';
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

export function formatNumber(n: number, decimals = 1): string {
  if (Math.abs(n) >= 1_000_000) return `${(n / 1_000_000).toFixed(decimals)}M`;
  if (Math.abs(n) >= 1_000) return `${(n / 1_000).toFixed(decimals)}K`;
  return n.toFixed(decimals);
}

export function formatPnl(n: number): string {
  const sign = n >= 0 ? '+' : '';
  return `${sign}${n.toFixed(2)}`;
}

export function formatPct(n: number): string {
  const sign = n >= 0 ? '+' : '';
  return `${sign}${n.toFixed(2)}%`;
}

export function statusColor(status: string | undefined | null): string {
  switch (status) {
    case 'healthy':
    case 'ok':
    case 'running':
    case 'connected':
      return 'var(--ok)';
    case 'warning':
    case 'degraded':
    case 'stale':
      return 'var(--warn)';
    case 'critical':
    case 'error':
    case 'disconnected':
    case 'stopped':
      return 'var(--crit)';
    default:
      return 'var(--text-muted)';
  }
}

export function statusBg(status: string | undefined | null): string {
  switch (status) {
    case 'healthy':
    case 'ok':
    case 'running':
      return 'var(--ok-dim)';
    case 'warning':
    case 'degraded':
      return 'var(--warn-dim)';
    case 'critical':
    case 'error':
      return 'var(--crit-dim)';
    default:
      return 'var(--surface-alt)';
  }
}

export function venueIcon(venue: string | undefined | null): string {
  switch ((venue ?? '').toLowerCase()) {
    case 'binance': return '₿';
    case 'betfair': return '⚽';
    case 'polymarket': return '🗳';
    default: return '📡';
  }
}

export function taskTypeLabel(t: string): string {
  return t.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

export function providerColor(provider: string): string {
  switch (provider) {
    case 'codex': return 'var(--ok)';
    case 'openai_api': return 'var(--info)';
    case 'deterministic': return 'var(--text-muted)';
    case 'ollama': return 'var(--accent)';
    default: return 'var(--text-dim)';
  }
}
