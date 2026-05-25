"""
Lacak Bid 📊
Track project yang sudah di-bid, catat status, dan analisis statistik.
Ringan — pakai SQLite, no API needed.

Commands terintegrasi:
- /bid [url] — tandain project sebagai sudah di-bid
- /open — lihat daftar project yg belum di-award
- /stats — statistik bid: win rate, platform mana paling efektif
"""

import logging
import json
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# ================================================================
# Database setup
# ================================================================


def _ensure_table(db_conn):
    """Create bid tracking table if not exists."""
    cursor = db_conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bid_tracker (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            project_url TEXT NOT NULL,
            project_title TEXT DEFAULT '',
            platform TEXT DEFAULT '',
            budget TEXT DEFAULT '',
            bid_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'pending',  -- pending, awarded, rejected, ghosted
            notes TEXT DEFAULT '',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, project_url)
        )
    """)
    db_conn.commit()


def add_bid(db_conn, user_id: str, project_url: str, project_title: str = "",
             platform: str = "", budget: str = "") -> bool:
    """Record a bid. Returns True if new, False if already exists."""
    _ensure_table(db_conn)
    cursor = db_conn.cursor()

    # Check duplicate
    cursor.execute(
        "SELECT id FROM bid_tracker WHERE user_id = ? AND project_url = ?",
        (user_id, project_url)
    )
    if cursor.fetchone():
        return False

    cursor.execute("""
        INSERT INTO bid_tracker (user_id, project_url, project_title, platform, budget, bid_date)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (user_id, project_url, project_title[:200], platform, budget[:50],
          datetime.now().isoformat()))
    db_conn.commit()
    return True


def update_bid_status(db_conn, user_id: str, project_url: str,
                       status: str, notes: str = "") -> bool:
    """Update bid status (awarded/rejected/ghosted)."""
    _ensure_table(db_conn)
    cursor = db_conn.cursor()
    cursor.execute("""
        UPDATE bid_tracker SET status = ?, notes = ?, updated_at = ?
        WHERE user_id = ? AND project_url = ?
    """, (status, notes, datetime.now().isoformat(), user_id, project_url))
    db_conn.commit()
    return cursor.rowcount > 0


def get_open_bids(db_conn, user_id: str, limit: int = 20) -> list[dict]:
    """Get pending bids (not yet awarded/rejected)."""
    _ensure_table(db_conn)
    cursor = db_conn.cursor()
    cursor.execute("""
        SELECT project_url, project_title, platform, budget, bid_date, status
        FROM bid_tracker
        WHERE user_id = ? AND status = 'pending'
        ORDER BY bid_date DESC
        LIMIT ?
    """, (user_id, limit))
    return [dict(row) for row in cursor.fetchall()]


def get_bid_stats(db_conn, user_id: str) -> dict:
    """Get bid statistics."""
    _ensure_table(db_conn)
    cursor = db_conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM bid_tracker WHERE user_id = ?", (user_id,))
    total = cursor.fetchone()[0]

    cursor.execute(
        "SELECT status, COUNT(*) FROM bid_tracker WHERE user_id = ? GROUP BY status",
        (user_id,)
    )
    by_status = {row["status"]: row[1] for row in cursor.fetchall()}

    # Platform breakdown
    cursor.execute("""
        SELECT platform, COUNT(*) as cnt FROM bid_tracker
        WHERE user_id = ? GROUP BY platform ORDER BY cnt DESC
    """, (user_id,))
    by_platform = [(row["platform"] or "unknown", row["cnt"]) for row in cursor.fetchall()]

    # Recent activity (last 30 days)
    since = (datetime.now() - timedelta(days=30)).isoformat()
    cursor.execute(
        "SELECT COUNT(*) FROM bid_tracker WHERE user_id = ? AND bid_date >= ?",
        (user_id, since)
    )
    recent = cursor.fetchone()[0]

    awarded = by_status.get("awarded", 0)
    win_rate = round(awarded / total * 100, 1) if total > 0 else 0

    return {
        "total": total,
        "pending": by_status.get("pending", 0),
        "awarded": awarded,
        "rejected": by_status.get("rejected", 0),
        "ghosted": by_status.get("ghosted", 0),
        "win_rate": win_rate,
        "recent_30d": recent,
        "by_platform": by_platform,
    }


def format_open_bids(bids: list[dict]) -> str:
    """Format open/pending bids for display."""
    if not bids:
        return "📊 Tidak ada bid yang pending.\n\nGunakan <code>/bid [url]</code> untuk mulai tracking."

    lines = [f"📊 <b>{len(bids)} Bid Pending</b>", ""]
    for i, b in enumerate(bids[:15], 1):
        days_ago = _days_ago(b["bid_date"])
        lines.append(
            f"<b>#{i}</b> {b['project_title'][:50]}\n"
            f"   💰 {b['budget'] or '-'}  •  🌐 {b['platform'] or '-'}  •  📅 {days_ago}"
        )
    
    if len(bids) > 15:
        lines.append(f"\n... dan {len(bids) - 15} lainnya.")
    
    lines.append("\n💡 <i>Update status: /award, /reject, /ghosted</i>")
    return "\n".join(lines)


def format_bid_stats(stats: dict) -> str:
    """Format bid statistics for display."""
    platform_lines = "\n".join(
        f"  • {p}: {c} bid" for p, c in stats["by_platform"][:5]
    ) if stats["by_platform"] else "  (belum ada data)"

    return (
        f"📊 <b>Statistik Bid</b>\n"
        f"\n"
        f"🎯 <b>Win Rate:</b> {stats['win_rate']}% ({stats['awarded']}/{stats['total']})\n"
        f"📦 <b>Total Bid:</b> {stats['total']}\n"
        f"⏳ <b>Pending:</b> {stats['pending']}\n"
        f"🏆 <b>Awarded:</b> {stats['awarded']}\n"
        f"❌ <b>Rejected:</b> {stats['rejected']}\n"
        f"👻 <b>Ghosted:</b> {stats['ghosted']}\n"
        f"🔥 <b>30 Hari Terakhir:</b> {stats['recent_30d']} bid\n"
        f"\n"
        f"🌐 <b>Per Platform:</b>\n"
        f"{platform_lines}\n"
        f"\n"
        f"💡 <i>Pro tip: Bid dalam 2-4 jam setelah posting untuk win rate lebih tinggi!</i>"
    )


def _days_ago(date_str: str) -> str:
    """Human-friendly time ago."""
    if not date_str:
        return "?"
    try:
        dt = datetime.fromisoformat(date_str[:19])
        delta = datetime.now() - dt
        if delta.days == 0:
            return "Hari ini"
        elif delta.days == 1:
            return "Kemarin"
        elif delta.days < 7:
            return f"{delta.days} hari lalu"
        elif delta.days < 30:
            return f"{delta.days // 7} minggu lalu"
        else:
            return f"{delta.days} hari lalu"
    except Exception:
        return "?"


# ================================================================
# Self-test
# ================================================================

if __name__ == "__main__":
    from database import get_db

    with get_db() as conn:
        test_user = "test_user_123"

        # Test add
        print("=== Add Bids ===")
        add_bid(conn, test_user, "https://projects.co.id/test1", "Project Test 1", "projects", "5jt")
        add_bid(conn, test_user, "https://fastwork.id/test2", "Project Test 2", "fastwork", "2jt")
        add_bid(conn, test_user, "https://projects.co.id/test3", "Project Test 3", "projects", "10jt")

        # Test duplicate
        result = add_bid(conn, test_user, "https://projects.co.id/test1", "Project Test 1")
        print(f"Duplicate check: {'OK (not added)' if not result else 'FAIL (should not add)'}")

        # Test open bids
        print("\n=== Open Bids ===")
        open_bids = get_open_bids(conn, test_user)
        print(format_open_bids(open_bids))

        # Test update
        update_bid_status(conn, test_user, "https://projects.co.id/test1", "awarded", "Dapet project!")
        
        # Test stats
        print("\n=== Stats ===")
        stats = get_bid_stats(conn, test_user)
        print(format_bid_stats(stats))

        # Cleanup test data
        cursor = conn.cursor()
        cursor.execute("DELETE FROM bid_tracker WHERE user_id = ?", (test_user,))
        conn.commit()
        print("\n✅ Test cleanup done")
