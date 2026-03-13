import { useState, useRef, useEffect, type ReactNode } from 'react';
import './SectionPanel.css';

interface SectionPanelProps {
  title: string;
  tag?: string;
  tagColor?: string;
  count?: number;
  children: ReactNode;
  collapsible?: boolean;
  defaultCollapsed?: boolean;
}

export default function SectionPanel({
  title,
  tag,
  tagColor = 'var(--info)',
  count,
  children,
  collapsible = false,
  defaultCollapsed = false,
}: SectionPanelProps) {
  const [collapsed, setCollapsed] = useState(defaultCollapsed);
  const bodyRef = useRef<HTMLDivElement>(null);
  const [bodyHeight, setBodyHeight] = useState<number | 'auto'>('auto');

  useEffect(() => {
    if (!bodyRef.current) return;
    const ro = new ResizeObserver(() => {
      if (!collapsed && bodyRef.current) {
        setBodyHeight(bodyRef.current.scrollHeight);
      }
    });
    ro.observe(bodyRef.current);
    return () => ro.disconnect();
  }, [collapsed]);

  const toggle = () => {
    if (!collapsible) return;
    if (!collapsed && bodyRef.current) {
      setBodyHeight(bodyRef.current.scrollHeight);
      requestAnimationFrame(() => setCollapsed(true));
    } else {
      setCollapsed(false);
    }
  };

  const heightStyle = collapsed ? 0 : bodyHeight === 'auto' ? undefined : bodyHeight;

  return (
    <section className="sp">
      <header
        className={`sp__header ${collapsible ? 'sp__header--clickable' : ''}`}
        onClick={toggle}
      >
        <div className="sp__title-group">
          <h3 className="sp__title">{title}</h3>
          {tag && (
            <span className="sp__tag" style={{ background: tagColor, color: '#000' }}>
              {tag}
            </span>
          )}
          {count !== undefined && <span className="sp__count">{count}</span>}
        </div>
        {collapsible && (
          <span className={`sp__chevron ${collapsed ? 'sp__chevron--down' : ''}`}>‹</span>
        )}
      </header>
      <div
        className="sp__body-wrap"
        style={{ height: heightStyle, overflow: 'hidden', transition: 'height 250ms ease' }}
      >
        <div ref={bodyRef} className="sp__body">
          {children}
        </div>
      </div>
    </section>
  );
}
