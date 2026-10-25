import React, { useState, useCallback, useRef, useEffect } from 'react';

// ── Types ────────────────────────────────────────────

export interface CalendarEventData {
  id: string;
  title: string;
  description: string;
  event_type: 'memorial' | 'period' | 'rest_day';
  date_string: string;  // 0000-MM-DD or YYYY-MM-DD
  creator: 'AI' | '人类伙伴';
  emotion?: string;
  is_active: boolean;
}

export interface NewEventFormData {
  title: string;
  description: string;
  event_type: 'memorial' | 'period' | 'rest_day';
  date_string: string;
  creator: 'AI' | '人类伙伴';
}

// ── Helpers ───────────────────────────────────────────

const WEEKDAY_LABELS = ['一', '二', '三', '四', '五', '六', '日'];

function getDaysInMonth(year: number, month: number): number {
  return new Date(year, month, 0).getDate();
}

function getFirstDayOfWeek(year: number, month: number): number {
  const d = new Date(year, month - 1, 1).getDay();
  return d === 0 ? 6 : d - 1;
}

function formatMonthYear(year: number, month: number): string {
  return `${year}年${month}月`;
}

// ── Props ─────────────────────────────────────────────

interface CalendarGridProps {
  year: number;
  month: number;
  eventsByDay: Record<number, CalendarEventData[]>;
  onMonthChange: (year: number, month: number) => void;
  onDateClick: (date: string, isEmpty: boolean) => void;
  onPeriodDrag: (range: { start: string; end: string }) => void;
  onDeleteEvent: (eventId: string) => void;
  onCreatePeriodDay: (dateStr: string) => void;
}

// ── Context Menu ──────────────────────────────────────

interface ContextMenuState {
  x: number;
  y: number;
  dateStr: string;
  hasPeriod: boolean;
  periodEventId?: string;
  memorialEventId?: string;
  restEventId?: string;
}

// ── Component ─────────────────────────────────────────

const CalendarGrid: React.FC<CalendarGridProps> = ({
  year,
  month,
  eventsByDay,
  onMonthChange,
  onDateClick,
  onPeriodDrag,
  onDeleteEvent,
  onCreatePeriodDay,
}) => {
  const today = new Date();
  const todayStr = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`;

  // Period drag state
  const [dragging, setDragging] = useState(false);
  const [dragStart, setDragStart] = useState<string | null>(null);
  const [dragEnd, setDragEnd] = useState<string | null>(null);
  const [tooltip, setTooltip] = useState<{ x: number; y: number; events: CalendarEventData[] } | null>(null);

  // Right-click context menu
  const [contextMenu, setContextMenu] = useState<ContextMenuState | null>(null);

  const dragRef = useRef<{ startDate: string; startDay: number } | null>(null);
  const longPressTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Navigation
  const goPrev = () => {
    if (month === 1) onMonthChange(year - 1, 12);
    else onMonthChange(year, month - 1);
  };
  const goNext = () => {
    if (month === 12) onMonthChange(year + 1, 1);
    else onMonthChange(year, month + 1);
  };

  // Build calendar grid
  const daysInMonth = getDaysInMonth(year, month);
  const firstDay = getFirstDayOfWeek(year, month);

  const cells: (number | null)[] = [];
  for (let i = 0; i < firstDay; i++) cells.push(null);
  for (let d = 1; d <= daysInMonth; d++) cells.push(d);

  const rows: (number | null)[][] = [];
  for (let i = 0; i < cells.length; i += 7) {
    rows.push(cells.slice(i, i + 7));
  }

  const getDateString = (day: number): string => {
    return `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
  };

  const getEventsForDay = (day: number): CalendarEventData[] => {
    return eventsByDay[day] || [];
  };

  // Cancel drag
  const cancelDrag = useCallback(() => {
    if (longPressTimer.current) {
      clearTimeout(longPressTimer.current);
      longPressTimer.current = null;
    }
    setDragging(false);
    setDragStart(null);
    setDragEnd(null);
    dragRef.current = null;
  }, []);

  // Mouse handlers for period drag
  const handleMouseDown = useCallback((day: number) => {
    const dateStr = getDateString(day);
    dragRef.current = { startDate: dateStr, startDay: day };
    longPressTimer.current = setTimeout(() => {
      setDragging(true);
      setDragStart(dateStr);
      setDragEnd(dateStr);
    }, 500);
  }, [year, month]);

  const handleMouseEnter = useCallback((day: number) => {
    const dateStr = getDateString(day);
    if (dragging && dragRef.current) {
      const startDay = dragRef.current.startDay;
      const endDay = day;
      const start = startDay < endDay ? dragRef.current.startDate : dateStr;
      const end = startDay < endDay ? dateStr : dragRef.current.startDate;
      setDragStart(start);
      setDragEnd(end);
    }
  }, [dragging, year, month]);

  const handleMouseUp = useCallback(() => {
    if (longPressTimer.current) {
      clearTimeout(longPressTimer.current);
      longPressTimer.current = null;
    }
    if (dragging && dragStart && dragEnd) {
      const start = dragStart < dragEnd ? dragStart : dragEnd;
      const end = dragStart < dragEnd ? dragEnd : dragStart;
      onPeriodDrag({ start, end });
    }
    setDragging(false);
    setDragStart(null);
    setDragEnd(null);
    dragRef.current = null;
  }, [dragging, dragStart, dragEnd, onPeriodDrag]);

  const handleMouseLeave = useCallback(() => {
  }, []);

  // Right-click handler
  const handleContextMenu = useCallback((e: React.MouseEvent, day: number) => {
    e.preventDefault();
    e.stopPropagation();
    const dateStr = getDateString(day);
    const events = getEventsForDay(day);
    const periodEvent = events.find(ev => ev.event_type === 'period');
    const memorialEvent = events.find(ev => ev.event_type === 'memorial');
    const restEvent = events.find(ev => ev.event_type === 'rest_day');
    setContextMenu({
      x: e.clientX,
      y: e.clientY,
      dateStr,
      hasPeriod: !!periodEvent,
      periodEventId: periodEvent?.id,
      memorialEventId: memorialEvent?.id,
      restEventId: restEvent?.id,
    });
  }, [year, month, eventsByDay]);

  // Close context menu on any click outside
  useEffect(() => {
    if (!contextMenu) return;
    const close = () => setContextMenu(null);
    window.addEventListener('click', close);
    window.addEventListener('scroll', close);
    return () => {
      window.removeEventListener('click', close);
      window.removeEventListener('scroll', close);
    };
  }, [contextMenu]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (longPressTimer.current) clearTimeout(longPressTimer.current);
    };
  }, []);

  // Tooltip handlers
  const showTooltip = (e: React.MouseEvent, day: number) => {
    const evts = getEventsForDay(day);
    if (evts.length > 0 && !dragging) {
      const rect = (e.target as HTMLElement).getBoundingClientRect();
      setTooltip({ x: rect.right + 8, y: rect.bottom + 4, events: evts });
    }
  };
  const hideTooltip = () => setTooltip(null);

  const isInDragRange = (day: number): boolean => {
    if (!dragging || !dragStart || !dragEnd) return false;
    const dateStr = getDateString(day);
    return dateStr >= dragStart && dateStr <= dragEnd;
  };

  const isToday = (day: number): boolean => {
    return getDateString(day) === todayStr;
  };

  return (
    <div className="calendar-container" onMouseUp={handleMouseUp} onMouseLeave={handleMouseLeave}>
      {/* Header */}
      <div className="calendar-header">
        <button className="calendar-nav-btn" onClick={goPrev}>‹</button>
        <span className="calendar-month-label">{formatMonthYear(year, month)}</span>
        <button className="calendar-nav-btn" onClick={goNext}>›</button>
      </div>

      {/* Weekday labels */}
      <div className="calendar-weekdays">
        {WEEKDAY_LABELS.map((label) => (
          <div key={label} className="calendar-weekday">{label}</div>
        ))}
      </div>

      {/* Day grid */}
      <div className="calendar-grid">
        {rows.map((row, ri) => (
          <div key={ri} className="calendar-row">
            {row.map((day, di) => {
              if (day === null) {
                return <div key={`empty-${di}`} className="calendar-cell empty" />;
              }

              const events = getEventsForDay(day);
              const hasEvents = events.length > 0;
              const hasMemorial = events.some(e => e.event_type === 'memorial');
              const periodEvent = events.find(e => e.event_type === 'period');
              const periodDayNum = periodEvent ? parseInt(periodEvent.emotion || '0', 10) : 0;
              const isFirstTwo = periodDayNum >= 1 && periodDayNum <= 2;
              const isLaterPeriod = periodDayNum >= 3;
              const isRestDay = events.some(e => e.event_type === 'rest_day');
              const inRange = isInDragRange(day);
              const todayClass = isToday(day) ? ' today' : '';
              const dateStr = getDateString(day);

              return (
                <div
                  key={dateStr}
                  className={`calendar-cell${todayClass}${inRange ? ' period-dragging' : ''}${hasEvents ? ' has-events' : ''}${dragging ? ' drag-mode' : ''}`}
                  onMouseDown={() => handleMouseDown(day)}
                  onMouseEnter={(e) => { handleMouseEnter(day); showTooltip(e, day); }}
                  onMouseMove={(e) => showTooltip(e, day)}
                  onMouseLeave={hideTooltip}
                  onClick={() => {
                    if (!dragging) {
                      onDateClick(dateStr, !hasMemorial);
                    }
                  }}
                  onContextMenu={(e) => handleContextMenu(e, day)}
                >
                  <span className="calendar-day-num">{day}</span>
                  {hasEvents && <>
                    {hasMemorial && <span className="calendar-dot dot-memorial" />}
                    {isFirstTwo && <span className="calendar-dot dot-period-deep" />}
                    {isLaterPeriod && <span className="calendar-dot dot-period-light" />}
                    {isRestDay && <span className="calendar-dot dot-rest" />}
                  </>}
                </div>
              );
            })}
          </div>
        ))}
      </div>

      {/* Tooltip */}
      {tooltip && (
        <div
          className="calendar-tooltip"
          style={{ left: tooltip.x, top: tooltip.y }}
          onMouseEnter={() => setTooltip(tooltip)}
          onMouseLeave={hideTooltip}
        >
          {tooltip.events.map((ev) => (
            <div key={ev.id} className="tooltip-event">
              <div className="tooltip-event-header">
                <span className={`tooltip-type-badge ${ev.event_type}`}>
                  {ev.event_type === 'memorial' ? '纪念日' : ev.event_type === 'period' ? '生理期' : '休息日'}
                </span>
                <span className="tooltip-creator">{ev.creator === 'AI' ? 'AI创建' : '人类伙伴创建'}</span>
              </div>
              <div className="tooltip-title">{ev.title}</div>
              {ev.description && <div className="tooltip-desc">{ev.description}</div>}
            </div>
          ))}
        </div>
      )}

      {/* Drag bar */}
      {dragging && (
        <div className="calendar-drag-bar">
          <span className="calendar-drag-hint">拖动选择生理期范围，松手确认</span>
          <button className="calendar-drag-cancel" onClick={cancelDrag}>取消</button>
        </div>
      )}

      {/* Right-click context menu */}
      {contextMenu && (
        <div
          className="context-menu"
          style={{ left: contextMenu.x, top: contextMenu.y }}
        >
          <div className="context-menu-header">{contextMenu.dateStr}</div>
          {contextMenu.memorialEventId && (
            <button
              className="context-menu-item danger"
              onClick={() => {
                onDeleteEvent(contextMenu.memorialEventId!);
                setContextMenu(null);
              }}
            >
              🗓️ 删除纪念日
            </button>
          )}
          {contextMenu.restEventId && (
            <button
              className="context-menu-item danger"
              onClick={() => {
                onDeleteEvent(contextMenu.restEventId!);
                setContextMenu(null);
              }}
            >
              🌙 删除休息日
            </button>
          )}
          {contextMenu.hasPeriod ? (
            <button
              className="context-menu-item danger"
              onClick={() => {
                if (contextMenu.periodEventId) onDeleteEvent(contextMenu.periodEventId);
                setContextMenu(null);
              }}
            >
              🩸 取消生理期
            </button>
          ) : (
            <button
              className="context-menu-item"
              onClick={() => {
                onCreatePeriodDay(contextMenu.dateStr);
                setContextMenu(null);
              }}
            >
              🩸 标记生理期
            </button>
          )}
        </div>
      )}
    </div>
  );
};

export default CalendarGrid;
