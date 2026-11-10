# -*- coding: utf-8 -*-
"""SQLite 存储层：usage_records / quota_snapshots / collector_state"""
import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from config import DATA_DIR

DB_PATH = os.path.join(DATA_DIR, "usage.db")
_lock = threading.Lock()
# 额度快照只保留最近 N 天：变化频率低，历史数据只需覆盖今日基线
QUOTA_RETENTION_DAYS = 30
QUOTA_BASE_PREFIX = "quota_day_base_"

SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_records (
    source TEXT NOT NULL,
    record_key TEXT NOT NULL,
    ts TEXT NOT NULL,
    model TEXT DEFAULT '',
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0,
    cache_write_tokens INTEGER DEFAULT 0,
    cost_usd REAL,
    credits REAL DEFAULT 0,
    raw TEXT DEFAULT '',
    PRIMARY KEY (source, record_key)
);
CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage_records (ts);
CREATE TABLE IF NOT EXISTS quota_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    ts TEXT NOT NULL,
    used_usd REAL,
    total_usd REAL,
    label TEXT DEFAULT '',
    raw TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_quota_source_ts ON quota_snapshots (source, ts);
CREATE TABLE IF NOT EXISTS collector_state (
    key TEXT PRIMARY KEY,
    value TEXT DEFAULT ''
);
"""


def _conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    with _lock, _conn() as c:
        c.executescript(SCHEMA)
        # 兼容旧库：补 credits 列
        cols = [r[1] for r in c.execute("PRAGMA table_info(usage_records)")]
        if "credits" not in cols:
            c.execute("ALTER TABLE usage_records ADD COLUMN credits REAL DEFAULT 0")
        # 清理过期额度快照，避免长期运行后库无限增长
        cutoff = (datetime.now() - timedelta(days=QUOTA_RETENTION_DAYS)).isoformat(
            timespec="seconds")
        c.execute("DELETE FROM quota_snapshots WHERE ts < ?", (cutoff,))


def insert_usage(records):
    """幂等插入（重复 record_key 忽略），返回新增条数"""
    if not records:
        return 0
    sql = """INSERT OR IGNORE INTO usage_records
        (source, record_key, ts, model, input_tokens, output_tokens,
         cache_read_tokens, cache_write_tokens, cost_usd, credits, raw)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
    rows = [(
        r["source"], r["record_key"], r["ts"], r.get("model", ""),
        r.get("input_tokens", 0), r.get("output_tokens", 0),
        r.get("cache_read_tokens", 0), r.get("cache_write_tokens", 0),
        r.get("cost_usd"), r.get("credits", 0) or 0, r.get("raw", ""),
    ) for r in records]
    with _lock, _conn() as c:
        cur = c.executemany(sql, rows)
        return cur.rowcount


def insert_quota_snapshot(source, ts, used_usd, total_usd, label="", raw=""):
    """写入额度快照；与上一条数值相同则跳过，返回是否实际写入"""
    with _lock, _conn() as c:
        last = c.execute(
            "SELECT used_usd, total_usd FROM quota_snapshots "
            "WHERE source = ? ORDER BY ts DESC LIMIT 1", (source,)).fetchone()
        if last and last[0] == used_usd and last[1] == total_usd:
            return False
        c.execute(
            "INSERT INTO quota_snapshots (source, ts, used_usd, total_usd, label, raw) VALUES (?,?,?,?,?,?)",
            (source, ts, used_usd, total_usd, label, raw),
        )
        return True


def get_state(key, default=""):
    with _lock, _conn() as c:
        row = c.execute("SELECT value FROM collector_state WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_state(key, value):
    with _lock, _conn() as c:
        c.execute(
            "INSERT INTO collector_state (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def _period_start(period):
    """返回周期起点的 UTC 时间字符串，与库中 ts（ISO ...Z）直接按字符串比较"""
    now = datetime.now().astimezone()
    if period == "day":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        start = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0)
    elif period == "month":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif period == "all":
        start = datetime(1970, 1, 1, tzinfo=timezone.utc)
    else:
        raise ValueError(period)
    return start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def aggregate(period):
    """聚合指定周期（day/week/month/all）全部来源的 token 与费用；cost 全未知时为 None"""
    start = _period_start(period)
    sql = """SELECT COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0),
             COALESCE(SUM(cache_read_tokens),0), COALESCE(SUM(cache_write_tokens),0),
             SUM(cost_usd),
             SUM(CASE WHEN cost_usd IS NULL THEN 1 ELSE 0 END),
             SUM(CASE WHEN cost_usd IS NULL THEN input_tokens + output_tokens ELSE 0 END),
             COALESCE(SUM(credits),0)
             FROM usage_records WHERE ts >= ?"""
    with _lock, _conn() as c:
        row = c.execute(sql, (start,)).fetchone()
    return {
        "input_tokens": row[0] or 0,
        "output_tokens": row[1] or 0,
        "cache_read_tokens": row[2] or 0,
        "cache_write_tokens": row[3] or 0,
        "cost_usd": row[4],
        "total_tokens": (row[0] or 0) + (row[1] or 0) + (row[2] or 0) + (row[3] or 0),
        "io_tokens": (row[0] or 0) + (row[1] or 0),
        "unpriced_count": row[5] or 0,
        "unpriced_io": row[6] or 0,
        "credits": row[7] or 0,
    }


def aggregate_by_source(period):
    """按来源分组聚合，用于展开视图"""
    start = _period_start(period)
    sql = """SELECT source, COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0),
             COALESCE(SUM(cache_read_tokens),0), COALESCE(SUM(cache_write_tokens),0), SUM(cost_usd),
             COALESCE(SUM(credits),0)
             FROM usage_records WHERE ts >= ? GROUP BY source
             ORDER BY (COALESCE(SUM(input_tokens),0) + COALESCE(SUM(output_tokens),0)
                       + COALESCE(SUM(cache_read_tokens),0)
                       + COALESCE(SUM(cache_write_tokens),0)) DESC"""
    with _lock, _conn() as c:
        rows = c.execute(sql, (start,)).fetchall()
    return [{
        "source": r[0], "input_tokens": r[1], "output_tokens": r[2],
        "cache_read_tokens": r[3], "cache_write_tokens": r[4], "cost_usd": r[5],
        "total_tokens": r[1] + r[2] + r[3] + r[4],
        "io_tokens": r[1] + r[2],
        "credits": r[6] or 0,
    } for r in rows]


def daily_series(days=7):
    """近 N 天每日 token 用量（按本地日期分桶，旧→新）"""
    today = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    buckets = [today - timedelta(days=i) for i in range(days - 1, -1, -1)]
    start_utc = buckets[0].astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    with _lock, _conn() as c:
        rows = c.execute(
            "SELECT ts, input_tokens + output_tokens + cache_read_tokens + cache_write_tokens "
            "FROM usage_records WHERE ts >= ?",
            (start_utc,)).fetchall()
    filled = {d.date(): 0 for d in buckets}
    for ts, io in rows:
        try:
            d = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().date()
        except ValueError:
            continue
        if d in filled:
            filled[d] += io or 0
    return [{"date": d.strftime("%m-%d"), "tokens": filled[d.date()]} for d in buckets]


def note_quota_fetch(source, used_usd):
    """确定当天的用量基线：每个来源每天只在首次取到额度时写入一次

    基线优先取紧邻零点前的快照（连续运行时误差仅一个采集周期），
    否则取今日首条快照，避免把多天前的旧值当成今日起点。
    """
    if used_usd is None:
        return
    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    day_start = today + "T00:00:00"
    key = QUOTA_BASE_PREFIX + source
    with _lock, _conn() as c:
        row = c.execute("SELECT value FROM collector_state WHERE key=?", (key,)).fetchone()
        try:
            saved = json.loads(row[0]) if row and row[0] else {}
        except ValueError:
            saved = {}
        if saved.get("date") == today and saved.get("used") is not None:
            return
        prev = c.execute(
            "SELECT ts, used_usd FROM quota_snapshots WHERE source=? AND ts < ? "
            "ORDER BY ts DESC LIMIT 1", (source, day_start)).fetchone()
        first = c.execute(
            "SELECT used_usd FROM quota_snapshots WHERE source=? AND ts >= ? "
            "ORDER BY ts LIMIT 1", (source, day_start)).fetchone()
        base_used = None
        if prev and prev[1] is not None:
            try:
                gap = (datetime.now() - datetime.fromisoformat(prev[0])).total_seconds()
            except ValueError:
                gap = None
            # 只信任紧邻今日的快照，间隔数天的旧值跨了未知用量，不能当基线
            if gap is not None and gap <= 120:
                base_used = prev[1]
        if base_used is None and first and first[0] is not None:
            base_used = first[0]
        if base_used is None:
            base_used = used_usd
        c.execute(
            "INSERT INTO collector_state (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps({"date": today, "used": base_used})))


def quota_used_today(sources):
    """今日额度增量：当天基线 → 最新快照

    基线由 note_quota_fetch 在每天首次采集时确定；缺少基线时返回 None。
    """
    srcs = (sources,) if isinstance(sources, str) else tuple(sources)
    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    total = 0.0
    seen = False
    with _lock, _conn() as c:
        for src in srcs:
            row = c.execute("SELECT value FROM collector_state WHERE key=?",
                            (QUOTA_BASE_PREFIX + src,)).fetchone()
            if not row or not row[0]:
                continue
            try:
                base = json.loads(row[0])
            except ValueError:
                continue
            if base.get("date") != today or base.get("used") is None:
                continue
            latest = c.execute(
                "SELECT used_usd FROM quota_snapshots WHERE source=? "
                "ORDER BY ts DESC LIMIT 1", (src,)).fetchone()
            if not latest or latest[0] is None:
                continue
            total += max(0.0, float(latest[0]) - float(base["used"]))
            seen = True
    return total if seen else None


def latest_quota_snapshots():
    """每个来源最新一条额度快照"""
    sql = """SELECT q.source, q.ts, q.used_usd, q.total_usd, q.label
             FROM quota_snapshots q
             JOIN (SELECT source, MAX(ts) AS mts FROM quota_snapshots GROUP BY source) m
               ON q.source = m.source AND q.ts = m.mts
             ORDER BY q.source"""
    with _lock, _conn() as c:
        rows = c.execute(sql).fetchall()
    return [{"source": r[0], "ts": r[1], "used_usd": r[2], "total_usd": r[3], "label": r[4]} for r in rows]
