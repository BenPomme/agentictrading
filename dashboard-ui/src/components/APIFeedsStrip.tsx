import React from 'react';
import type { APIFeeds, ConnectorHealth } from '../types/snapshot';
import { venueIcon, relativeTime, formatNumber } from '../utils/format';
import './APIFeedsStrip.css';

interface APIFeedsStripProps {
  feeds: APIFeeds | undefined;
}

const STATUS_LABEL: Record<string, string> = {
  healthy: 'CONNECTED',
  warning: 'STALE',
  critical: 'DISCONNECTED',
};

function summaryClass(feeds: APIFeeds): string {
  if (feeds.critical_count > 0) return 'afs__summary afs__summary--critical';
  if (feeds.warning_count > 0) return 'afs__summary afs__summary--warning';
  return 'afs__summary afs__summary--healthy';
}

function dotClass(status: string): string {
  if (status === 'healthy') return 'afs__dot afs__dot--healthy';
  if (status === 'warning') return 'afs__dot afs__dot--warning';
  return 'afs__dot afs__dot--critical';
}

function labelClass(status: string): string {
  if (status === 'healthy') return 'afs__status-label afs__status-label--healthy';
  if (status === 'warning') return 'afs__status-label afs__status-label--warning';
  return 'afs__status-label afs__status-label--critical';
}

const ConnectorCard: React.FC<{ c: ConnectorHealth }> = ({ c }) => (
  <div className="afs__card">
    <span className="afs__card-icon">{venueIcon(c.venue)}</span>
    <div className="afs__card-body">
      <span className="afs__card-venue">{c.venue}</span>
      <div className="afs__card-meta">
        <span className={dotClass(c.status)} />
        <span className={labelClass(c.status)}>
          {STATUS_LABEL[c.status] ?? c.status.toUpperCase()}
        </span>
        <span>{relativeTime(c.latest_data_ts)}</span>
        <span className="afs__card-records">{formatNumber(c.record_count, 0)} rec</span>
      </div>
    </div>
  </div>
);

export const APIFeedsStrip: React.FC<APIFeedsStripProps> = ({ feeds }) => {
  if (!feeds) {
    return (
      <div className="afs">
        <span className="afs__empty">Waiting for feed data…</span>
      </div>
    );
  }

  return (
    <div className="afs">
      <span className={summaryClass(feeds)}>
        {feeds.healthy_count}/{feeds.total_count} HEALTHY
      </span>

      <span className="afs__divider" />

      <div className="afs__cards">
        {feeds.connectors.map((c) => (
          <ConnectorCard key={c.connector_id} c={c} />
        ))}
      </div>
    </div>
  );
};
