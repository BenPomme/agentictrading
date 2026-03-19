import React from 'react';
import type { Zone } from '../types/nav';
import { NAV_ITEMS } from '../types/nav';
import './NavSidebar.css';

interface NavSidebarProps {
  activeZone: Zone;
  onNavigate: (zone: Zone) => void;
  badges?: Partial<Record<Zone, number>>;
  criticalCount?: number;
}

export const NavSidebar: React.FC<NavSidebarProps> = ({
  activeZone,
  onNavigate,
  badges = {},
  criticalCount = 0,
}) => {
  return (
    <nav className="nav-sidebar" aria-label="Control Room Navigation">
      <ul className="nav-sidebar__list">
        {NAV_ITEMS.map((item) => {
          const isActive = item.zone === activeZone;
          const badgeValue = badges[item.zone] ?? 0;
          const showBadge = badgeValue > 0;
          return (
            <li key={item.zone} className="nav-sidebar__item-wrap">
              <button
                className={`nav-sidebar__item${isActive ? ' nav-sidebar__item--active' : ''}`}
                onClick={() => onNavigate(item.zone)}
                title={item.description}
                aria-current={isActive ? 'page' : undefined}
              >
                <span className="nav-sidebar__icon" aria-hidden="true">
                  {item.icon}
                </span>
                <span className="nav-sidebar__label">{item.label}</span>
                {showBadge && (
                  <span
                    className={`nav-sidebar__badge${
                      item.zone === 'alerts' && criticalCount > 0
                        ? ' nav-sidebar__badge--crit'
                        : item.zone === 'promotion-queue'
                        ? ' nav-sidebar__badge--info'
                        : ''
                    }`}
                  >
                    {badgeValue > 99 ? '99+' : badgeValue}
                  </span>
                )}
              </button>
            </li>
          );
        })}
      </ul>
    </nav>
  );
};
