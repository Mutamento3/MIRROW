"""Local desktop task metadata only; never reads dialogue or credentials."""
import math
import sqlite3
import time
from pathlib import Path
from .desktop_writer import writer_owned

TERMINAL = {'completed', 'failed', 'interrupted'}


class DesktopActivity:
    def __init__(self, home=None, *, quiet_seconds=0):
        self.home = Path(home or Path.home()/'.codex').resolve()
        self.quiet_seconds = quiet_seconds
        self.idle_since = None

    def _rows(self, name, query):
        path = self.home/name
        if not path.is_file() or path.resolve().parent != self.home:
            raise ValueError('metadata_unavailable')
        until = time.monotonic()+1
        with sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=.5) as db:
            db.execute('PRAGMA query_only=ON')
            db.set_progress_handler(lambda:int(time.monotonic()>=until),1000)
            return db.execute(query).fetchall()

    def read(self):
        now = time.time()
        try:
            turns = self._rows('thread_history_1.sqlite', '''SELECT t.thread_id,t.status,t.started_at,t.completed_at
                FROM thread_turns t JOIN (SELECT thread_id,MAX(rollout_ordinal) AS latest
                    FROM thread_turns GROUP BY thread_id) n
                ON t.thread_id=n.thread_id AND t.rollout_ordinal=n.latest LIMIT 1001''')
            threads = self._rows('state_5.sqlite', 'SELECT id,updated_at,cli_version FROM threads LIMIT 1001')
            if len(turns)>1000 or len(threads)>1000:
                raise ValueError('metadata_limit')
            known = set()
            active = 0
            unknown = False
            metadata = {row[0]: row[1:] for row in threads}
            for thread_id,status,started,completed in turns:
                known.add(thread_id)
                if status == 'inProgress':
                    if type(started) not in (int,float) or not math.isfinite(started) or started>now+5:
                        unknown = True
                        continue
                    updated, version = metadata.get(thread_id, (None, ''))
                    updated = updated/1000 if isinstance(updated, (int,float)) and updated>1e12 else updated
                    # Only reconcile established records from a writer-lock capable
                    # client. Recent projection changes keep the existing busy guard.
                    try:
                        supported = tuple(int(x) for x in version.split('.')[:3]) >= (0,159,0)
                    except (ValueError, AttributeError):
                        supported = False
                    owner = writer_owned(self.home, thread_id) if supported else None
                    if owner is False and isinstance(updated, (int,float)) and updated < now-120 and started < now-120:
                        continue  # Orphaned persisted turn, not a live desktop writer.
                    if owner is True or now-started<=86400:
                        active += 1
                    else:
                        unknown = True
                elif status not in TERMINAL:
                    unknown = True
            for thread_id,updated,_version in threads:
                if thread_id not in known and type(updated) in (int,float):
                    updated = updated/1000 if updated>1e12 else updated
                    if updated >= now-120:
                        unknown = True  # New task projection may not be committed yet.
            status = 'busy' if active else 'unknown' if unknown else 'idle'
        except (sqlite3.Error, ValueError, OSError):
            status,active = 'unknown',0
        if status != 'idle':
            self.idle_since = None
        elif self.idle_since is None:
            self.idle_since = now
        quiet = 0 if self.idle_since is None else max(0,self.quiet_seconds-(now-self.idle_since))
        return {'status':status, 'active_tasks':active, 'checked_at':now,
                'ready':status=='idle' and quiet==0, 'quiet_seconds_left':round(quiet,1)}
