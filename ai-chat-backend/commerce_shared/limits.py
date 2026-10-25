"""Hard, decimal-safe commerce limits and an idempotent local ledger."""
from __future__ import annotations
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone, timedelta, tzinfo
from pathlib import Path
from typing import Any, Mapping
try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

def _shanghai_tz() -> tzinfo:
    if ZoneInfo is not None:
        try:
            return ZoneInfo('Asia/Shanghai')
        except Exception:
            pass
    return timezone(timedelta(hours=8))

def _day(value: str | datetime | date | None=None) -> str:
    if value is None:
        current = datetime.now(_shanghai_tz())
        return current.date().isoformat()
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(_shanghai_tz()).date().isoformat()
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]

def money_cents(value: int | float | str) -> int:
    """Convert a user/config amount to cents without binary float rounding."""
    from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
    try:
        amount = Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError('amount_invalid') from exc
    cents = int(amount * 100)
    if cents < 0:
        raise ValueError('amount_must_not_be_negative')
    return cents

@dataclass(frozen=True)
class LimitConfig:
    """One domain's per-order, daily amount and order-count caps."""
    per_order: int | float | str
    daily_total: int | float | str
    daily_orders: int | None = None

    def __post_init__(self) -> None:
        per = money_cents(self.per_order)
        total = money_cents(self.daily_total)
        if per <= 0 or total <= 0:
            raise ValueError('limits_must_be_positive')
        if self.daily_orders is not None and int(self.daily_orders) < 1:
            raise ValueError('daily_orders_invalid')
        object.__setattr__(self, 'per_order', per / 100)
        object.__setattr__(self, 'daily_total', total / 100)
        object.__setattr__(self, 'daily_orders', int(self.daily_orders) if self.daily_orders is not None else None)

    @property
    def per_order_cents(self) -> int:
        return money_cents(self.per_order)

    @property
    def daily_total_cents(self) -> int:
        return money_cents(self.daily_total)

    def to_dict(self) -> dict[str, Any]:
        return {'per_order': self.per_order, 'daily_total': self.daily_total, 'daily_orders': self.daily_orders}
DEFAULT_LIMITS: dict[str, LimitConfig] = {'meituan': LimitConfig(per_order=50, daily_total=200, daily_orders=5)}

@dataclass(frozen=True)
class LimitDecision:
    allowed: bool
    reason: str
    service: str
    amount_cents: int
    daily_spent_cents: int
    daily_orders: int
    config: LimitConfig

    def to_dict(self) -> dict[str, Any]:
        return {'allowed': self.allowed, 'reason': self.reason, 'service': self.service, 'amount': self.amount_cents / 100, 'daily_spent': self.daily_spent_cents / 100, 'daily_orders': self.daily_orders, 'limits': self.config.to_dict()}

class LimitLedger:
    """SQLite ledger counting reserved and submitted orders.

    A reservation is included in the day total immediately, so concurrent
    requests cannot both pass a limit check.  Failed reservations are released;
    an unresolved submitting request remains reserved until reconciliation.
    """

    def __init__(self, db_path: str | Path='data/commerce.db', configs: Mapping[str, LimitConfig] | None=None):
        self.db_path = Path(db_path)
        self.configs = dict(configs or DEFAULT_LIMITS)
        self.initialize()

    def _conn(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _connection(self, *, immediate: bool=False):
        conn = self._conn()
        try:
            if immediate:
                conn.execute('BEGIN IMMEDIATE')
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        with self._connection() as conn:
            conn.execute('\n                CREATE TABLE IF NOT EXISTS commerce_limit_ledger (\n                    order_id TEXT PRIMARY KEY,\n                    service TEXT NOT NULL,\n                    amount_cents INTEGER NOT NULL,\n                    status TEXT NOT NULL,\n                    logical_day TEXT NOT NULL,\n                    created_at TEXT NOT NULL,\n                    finalized_at TEXT,\n                    expires_at TEXT\n                )\n            ')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_commerce_limit_day ON commerce_limit_ledger(service, logical_day, status)')
            try:
                conn.execute('ALTER TABLE commerce_limit_ledger ADD COLUMN expires_at TEXT')
            except sqlite3.OperationalError as exc:
                if 'duplicate column' not in str(exc).lower():
                    raise
            expiry = (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()
            conn.execute("UPDATE commerce_limit_ledger SET expires_at=? WHERE status='reserved' AND (expires_at IS NULL OR expires_at='')", (expiry,))
            conn.execute('\n                CREATE TABLE IF NOT EXISTS commerce_limit_config (\n                    service TEXT PRIMARY KEY,\n                    per_order_cents INTEGER NOT NULL,\n                    daily_total_cents INTEGER NOT NULL,\n                    daily_orders INTEGER,\n                    updated_at TEXT NOT NULL\n                )\n            ')
            for service, config in self.configs.items():
                conn.execute('INSERT OR IGNORE INTO commerce_limit_config(service,per_order_cents,daily_total_cents,daily_orders,updated_at) VALUES(?,?,?,?,?)', (str(service).lower(), config.per_order_cents, config.daily_total_cents, config.daily_orders, datetime.now(timezone.utc).isoformat()))

    def _config(self, service: str) -> LimitConfig:
        key = str(service or '').strip().lower()
        if key not in self.configs:
            raise ValueError('unknown_commerce_service')
        with self._connection() as conn:
            row = conn.execute('SELECT per_order_cents,daily_total_cents,daily_orders FROM commerce_limit_config WHERE service=?', (key,)).fetchone()
        if row is None:
            return self.configs[key]
        return LimitConfig(row['per_order_cents'] / 100, row['daily_total_cents'] / 100, row['daily_orders'])

    def get_config(self, service: str) -> LimitConfig:
        return self._config(service)

    def set_config(self, service: str, *, per_order: int | float | str, daily_total: int | float | str, daily_orders: int | None=None) -> LimitConfig:
        """Persist a reviewed local cap update."""
        key = str(service or '').strip().lower()
        if key not in self.configs:
            raise ValueError('unknown_commerce_service')
        config = LimitConfig(per_order, daily_total, daily_orders)
        with self._connection() as conn:
            conn.execute('UPDATE commerce_limit_config SET per_order_cents=?,daily_total_cents=?,daily_orders=?,updated_at=? WHERE service=?', (config.per_order_cents, config.daily_total_cents, config.daily_orders, datetime.now(timezone.utc).isoformat(), key))
        self.configs[key] = config
        return config
    update_config = set_config

    def _purge_expired(self, conn: sqlite3.Connection) -> None:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("UPDATE commerce_limit_ledger SET status='expired',finalized_at=? WHERE status='reserved' AND expires_at IS NOT NULL AND expires_at<=?", (now, now))

    def _totals(self, conn: sqlite3.Connection, service: str, logical_day: str) -> tuple[int, int]:
        self._purge_expired(conn)
        row = conn.execute("SELECT COALESCE(SUM(amount_cents), 0) AS amount, COUNT(*) AS orders FROM commerce_limit_ledger WHERE service=? AND logical_day=? AND status IN ('reserved','submitted')", (service, logical_day)).fetchone()
        return (int(row['amount'] or 0), int(row['orders'] or 0))

    def check(self, service: str, amount: int | float | str, *, logical_day: str | date | datetime | None=None, include_order: bool=True) -> LimitDecision:
        service = str(service or '').strip().lower()
        config = self._config(service)
        amount_cents = money_cents(amount)
        day = _day(logical_day)
        with self._connection() as conn:
            spent, orders = self._totals(conn, service, day)
        reason = 'ok'
        allowed = True
        if amount_cents > config.per_order_cents:
            allowed, reason = (False, 'per_order_limit_exceeded')
        elif include_order and spent + amount_cents > config.daily_total_cents:
            allowed, reason = (False, 'daily_total_limit_exceeded')
        elif include_order and config.daily_orders is not None and (orders + 1 > config.daily_orders):
            allowed, reason = (False, 'daily_order_count_limit_exceeded')
        return LimitDecision(allowed, reason, service, amount_cents, spent, orders, config)

    def reserve(self, service: str, order_id: str, amount: int | float | str, *, logical_day: str | date | datetime | None=None, reservation_ttl_seconds: int | float=900) -> LimitDecision:
        service = str(service or '').strip().lower()
        config = self._config(service)
        order_id = str(order_id or '').strip()
        if not order_id:
            raise ValueError('order_id_required')
        amount_cents = money_cents(amount)
        day = _day(logical_day)
        created = datetime.now(timezone.utc).isoformat()
        try:
            ttl = max(1, min(86400, int(float(reservation_ttl_seconds))))
        except (TypeError, ValueError):
            ttl = 900
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=ttl)).isoformat()
        with self._connection(immediate=True) as conn:
            self._purge_expired(conn)
            existing = conn.execute('SELECT service, amount_cents, status, logical_day FROM commerce_limit_ledger WHERE order_id=?', (order_id,)).fetchone()
            if existing is not None:
                if existing['service'] != service:
                    return LimitDecision(False, 'order_id_already_bound', service, amount_cents, 0, 0, config)
                active_day = existing['logical_day'] if existing['status'] in {'reserved', 'submitted'} else day
                spent, orders = self._totals(conn, service, active_day)
                if existing['status'] == 'reserved':
                    if int(existing['amount_cents']) != amount_cents:
                        return LimitDecision(False, 'order_id_already_bound', service, amount_cents, spent, orders, config)
                    return LimitDecision(True, 'already_reserved', service, amount_cents, spent, orders, config)
                if existing['status'] == 'submitted':
                    if int(existing['amount_cents']) != amount_cents:
                        return LimitDecision(False, 'order_id_already_bound', service, amount_cents, spent, orders, config)
                    return LimitDecision(True, 'already_finalized', service, amount_cents, spent, orders, config)
                if amount_cents > config.per_order_cents:
                    return LimitDecision(False, 'per_order_limit_exceeded', service, amount_cents, spent, orders, config)
                if spent + amount_cents > config.daily_total_cents:
                    return LimitDecision(False, 'daily_total_limit_exceeded', service, amount_cents, spent, orders, config)
                if config.daily_orders is not None and orders + 1 > config.daily_orders:
                    return LimitDecision(False, 'daily_order_count_limit_exceeded', service, amount_cents, spent, orders, config)
                conn.execute("UPDATE commerce_limit_ledger SET amount_cents=?,status='reserved',logical_day=?,created_at=?,finalized_at=NULL,expires_at=? WHERE order_id=?", (amount_cents, day, created, expires_at, order_id))
                return LimitDecision(True, 're_reserved', service, amount_cents, spent + amount_cents, orders + 1, config)
            spent, orders = self._totals(conn, service, day)
            reason = 'ok'
            allowed = True
            if amount_cents > config.per_order_cents:
                allowed, reason = (False, 'per_order_limit_exceeded')
            elif spent + amount_cents > config.daily_total_cents:
                allowed, reason = (False, 'daily_total_limit_exceeded')
            elif config.daily_orders is not None and orders + 1 > config.daily_orders:
                allowed, reason = (False, 'daily_order_count_limit_exceeded')
            if allowed:
                conn.execute('INSERT INTO commerce_limit_ledger(order_id,service,amount_cents,status,logical_day,created_at,expires_at) VALUES (?,?,?,?,?,?,?)', (order_id, service, amount_cents, 'reserved', day, created, expires_at))
            return LimitDecision(allowed, reason, service, amount_cents, spent, orders, config)

    def adjust(self, service: str, order_id: str, amount: int | float | str, *, logical_day: str | date | datetime | None=None, reservation_ttl_seconds: int | float=900) -> LimitDecision:
        """Atomically resize a reserved order, normally after a price drop."""
        service = str(service or '').strip().lower()
        config = self._config(service)
        order_id = str(order_id or '').strip()
        if not order_id:
            raise ValueError('order_id_required')
        amount_cents = money_cents(amount)
        day = _day(logical_day)
        try:
            ttl = max(1, min(86400, int(float(reservation_ttl_seconds))))
        except (TypeError, ValueError):
            ttl = 900
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=ttl)).isoformat()
        with self._connection(immediate=True) as conn:
            self._purge_expired(conn)
            current = conn.execute('SELECT service,amount_cents,status,logical_day FROM commerce_limit_ledger WHERE order_id=?', (order_id,)).fetchone()
            if current is None:
                spent, orders = self._totals(conn, service, day)
                if amount_cents > config.per_order_cents:
                    return LimitDecision(False, 'per_order_limit_exceeded', service, amount_cents, spent, orders, config)
                if spent + amount_cents > config.daily_total_cents:
                    return LimitDecision(False, 'daily_total_limit_exceeded', service, amount_cents, spent, orders, config)
                if config.daily_orders is not None and orders + 1 > config.daily_orders:
                    return LimitDecision(False, 'daily_order_count_limit_exceeded', service, amount_cents, spent, orders, config)
                conn.execute('INSERT INTO commerce_limit_ledger(order_id,service,amount_cents,status,logical_day,created_at,expires_at) VALUES (?,?,?,?,?,?,?)', (order_id, service, amount_cents, 'reserved', day, datetime.now(timezone.utc).isoformat(), expires_at))
                return LimitDecision(True, 'ok', service, amount_cents, spent + amount_cents, orders + 1, config)
            if current['service'] != service:
                return LimitDecision(False, 'order_id_already_bound', service, amount_cents, 0, 0, config)
            if current['status'] != 'reserved':
                spent, orders = self._totals(conn, service, current['logical_day'])
                return LimitDecision(current['status'] == 'submitted', 'reservation_not_adjustable', service, amount_cents, spent, orders, config)
            old = int(current['amount_cents'])
            spent, orders = self._totals(conn, service, current['logical_day'])
            available = spent - old
            if amount_cents > config.per_order_cents:
                return LimitDecision(False, 'per_order_limit_exceeded', service, amount_cents, spent, orders, config)
            if available + amount_cents > config.daily_total_cents:
                return LimitDecision(False, 'daily_total_limit_exceeded', service, amount_cents, spent, orders, config)
            conn.execute("UPDATE commerce_limit_ledger SET amount_cents=?,logical_day=?,expires_at=? WHERE order_id=? AND status='reserved'", (amount_cents, day, expires_at, order_id))
            return LimitDecision(True, 'reservation_adjusted', service, amount_cents, available + amount_cents, orders, config)

    def finalize(self, order_id: str) -> bool:
        with self._connection(immediate=True) as conn:
            self._purge_expired(conn)
            cur = conn.execute("UPDATE commerce_limit_ledger SET status='submitted', finalized_at=? WHERE order_id=? AND status='reserved'", (datetime.now(timezone.utc).isoformat(), str(order_id or '')))
            return cur.rowcount > 0

    def release(self, order_id: str) -> bool:
        with self._connection(immediate=True) as conn:
            self._purge_expired(conn)
            cur = conn.execute("UPDATE commerce_limit_ledger SET status='released', finalized_at=? WHERE order_id=? AND status='reserved'", (datetime.now(timezone.utc).isoformat(), str(order_id or '')))
            return cur.rowcount > 0

    def reconcile(self, order_id: str, *, found: bool) -> bool:
        """Finalize a submitting reservation only when reconciliation found it."""
        return self.finalize(order_id) if found else self.release(order_id)

    def reservation(self, order_id: str) -> dict[str, Any] | None:
        """Return non-sensitive reservation state for an internal adapter."""
        with self._connection(immediate=True) as conn:
            self._purge_expired(conn)
            row = conn.execute('SELECT service,amount_cents,status,logical_day,created_at,finalized_at,expires_at FROM commerce_limit_ledger WHERE order_id=?', (str(order_id or ''),)).fetchone()
        if row is None:
            return None
        return {'service': row['service'], 'amount': int(row['amount_cents']) / 100, 'status': row['status'], 'logical_day': row['logical_day'], 'created_at': row['created_at'], 'finalized_at': row['finalized_at'], 'expires_at': row['expires_at']}

    def record(self, service: str, order_id: str, amount: int | float | str, **kwargs: Any) -> LimitDecision:
        """Compatibility alias: reserve then finalize only on success by caller."""
        return self.reserve(service, order_id, amount, **kwargs)

    def daily_usage(self, service: str, *, logical_day: str | date | datetime | None=None) -> dict[str, Any]:
        service = str(service or '').strip().lower()
        config = self._config(service)
        day = _day(logical_day)
        with self._connection() as conn:
            amount, orders = self._totals(conn, service, day)
        return {'service': service, 'logical_day': day, 'amount': amount / 100, 'orders': orders, 'limits': config.to_dict()}
__all__ = ['DEFAULT_LIMITS', 'LimitConfig', 'LimitDecision', 'LimitLedger', 'money_cents']
