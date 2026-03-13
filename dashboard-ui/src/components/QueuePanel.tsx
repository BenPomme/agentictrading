import type { QueueItem } from '../types/snapshot';
import SectionPanel from './SectionPanel';
import './QueuePanel.css';

interface Props {
  queue: QueueItem[] | undefined;
}

export default function QueuePanel({ queue }: Props) {
  const list = [...(queue ?? [])].sort((a, b) => a.priority - b.priority);

  return (
    <SectionPanel
      title="Research Queue"
      count={list.length || undefined}
      collapsible
    >
      {list.length === 0 ? (
        <div className="sp__empty">Queue empty</div>
      ) : (
        <>
          <div className="qup__count">{list.length}</div>
          <div className="qup__scroll">
            {list.slice(0, 10).map((q) => (
              <div key={q.queue_id} className="qup__item">
                <span className="qup__pri">{q.priority}</span>
                <div className="qup__body">
                  <div className="qup__row">
                    <span className="qup__lineage">{q.lineage_id.split('/').pop()}</span>
                    <span className="qup__family">{q.family_id}</span>
                  </div>
                  <div className="qup__row">
                    <span className="qup__status">{q.status}</span>
                    <span className="qup__stage">{q.current_stage}</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </SectionPanel>
  );
}
