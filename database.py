"""
SQLite database layer for gig-notifier bot.
Provides persistent storage for projects, stats, and user settings.
"""

import os
import sqlite3
import json
import logging
from datetime import datetime
from typing import Optional, Any
from contextlib import contextmanager

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "gig_notifier.db")

# ============================================================
# Database Connection Management
# ============================================================


@contextmanager
def get_db():
    """Get a database connection with row factory."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    """Initialize the database schema."""
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Projects table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                title TEXT,
                budget TEXT,
                budget_raw REAL,
                category TEXT,
                client_name TEXT,
                posted_date TEXT,
                description TEXT,
                url TEXT,
                source TEXT DEFAULT 'projects',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Daily stats table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS daily_stats (
                date TEXT,
                category TEXT,
                project_count INTEGER DEFAULT 0,
                avg_budget REAL,
                total_budget REAL,
                source TEXT DEFAULT 'projects',
                PRIMARY KEY (date, category, source)
            )
        """)
        
        # Client stats table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS client_stats (
                client_name TEXT PRIMARY KEY,
                project_count INTEGER DEFAULT 0,
                avg_budget REAL,
                total_budget REAL,
                last_seen TEXT,
                first_seen TEXT
            )
        """)
        
        # User settings table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_settings (
                chat_id TEXT PRIMARY KEY,
                settings_json TEXT
            )
        """)
        
        # User monitor table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_monitor (
                chat_id TEXT,
                category TEXT,
                source TEXT DEFAULT 'projects',
                PRIMARY KEY (chat_id, category, source)
            )
        """)
        
        # Seen projects table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS seen_projects (
                project_id TEXT,
                chat_id TEXT DEFAULT 'global',
                source TEXT DEFAULT 'projects',
                seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (project_id, chat_id, source)
            )
        """)
        
        # Sribu contests table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sribu_contests (
                id TEXT PRIMARY KEY,
                title TEXT,
                budget TEXT,
                budget_raw REAL,
                category TEXT,
                category_name TEXT,
                client_name TEXT,
                posted_date TEXT,
                description TEXT,
                url TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Fastwork jobs table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS fastwork_jobs (
                id TEXT PRIMARY KEY,
                title TEXT,
                budget TEXT,
                budget_raw REAL,
                tag_id TEXT,
                tag_name TEXT,
                client_name TEXT,
                posted_date TEXT,
                description TEXT,
                url TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        conn.commit()
        logger.info("Database initialized successfully")


# ============================================================
# JSON Migration
# ============================================================


def migrate_from_json():
    """Migrate data from existing JSON files to SQLite."""
    data_dir = os.path.join(os.path.dirname(__file__), "data")
    
    # Migrate seen_projects.json
    seen_file = os.path.join(data_dir, "seen_projects.json")
    if os.path.exists(seen_file):
        try:
            with open(seen_file) as f:
                seen_ids = json.load(f)
            with get_db() as conn:
                cursor = conn.cursor()
                for pid in seen_ids:
                    cursor.execute(
                        "INSERT OR IGNORE INTO seen_projects (project_id) VALUES (?)",
                        (pid,)
                    )
                conn.commit()
            logger.info(f"Migrated {len(seen_ids)} seen projects from JSON")
        except Exception as e:
            logger.error(f"Error migrating seen_projects: {e}")
    
    # Migrate client_stats.json
    client_file = os.path.join(data_dir, "client_stats.json")
    if os.path.exists(client_file):
        try:
            with open(client_file) as f:
                stats = json.load(f)
            with get_db() as conn:
                cursor = conn.cursor()
                for client_name, data in stats.items():
                    cursor.execute("""
                        INSERT OR REPLACE INTO client_stats 
                        (client_name, project_count, avg_budget, total_budget, last_seen, first_seen)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (
                        client_name,
                        data.get("project_count", 0),
                        data.get("avg_budget", 0),
                        data.get("total_budget", 0),
                        data.get("last_seen", ""),
                        data.get("first_seen", "")
                    ))
                conn.commit()
            logger.info(f"Migrated {len(stats)} client stats from JSON")
        except Exception as e:
            logger.error(f"Error migrating client_stats: {e}")
    
    # Migrate daily_digest.json
    digest_file = os.path.join(data_dir, "daily_digest.json")
    if os.path.exists(digest_file):
        try:
            with open(digest_file) as f:
                digest = json.load(f)
            with get_db() as conn:
                cursor = conn.cursor()
                for date, categories in digest.items():
                    if isinstance(categories, dict):
                        for category, data in categories.items():
                            if isinstance(data, dict):
                                cursor.execute("""
                                    INSERT OR REPLACE INTO daily_stats 
                                    (date, category, project_count, avg_budget, total_budget)
                                    VALUES (?, ?, ?, ?, ?)
                                """, (
                                    date,
                                    category,
                                    data.get("count", 0),
                                    data.get("avg_budget", 0),
                                    data.get("total_budget", 0)
                                ))
                conn.commit()
            logger.info(f"Migrated daily digest from JSON")
        except Exception as e:
            logger.error(f"Error migrating daily_digest: {e}")
    
    # Migrate monitor_config.json
    monitor_file = os.path.join(data_dir, "monitor_config.json")
    if os.path.exists(monitor_file):
        try:
            with open(monitor_file) as f:
                config = json.load(f)
            with get_db() as conn:
                cursor = conn.cursor()
                for cat_id in config.get("categories", []):
                    cursor.execute(
                        "INSERT OR IGNORE INTO user_monitor (chat_id, category) VALUES (?, ?)",
                        ("global", cat_id)
                    )
                conn.commit()
            logger.info(f"Migrated monitor config from JSON")
        except Exception as e:
            logger.error(f"Error migrating monitor_config: {e}")
    
    # Migrate fastwork_seen.json
    fw_seen_file = os.path.join(data_dir, "fastwork_seen.json")
    if os.path.exists(fw_seen_file):
        try:
            with open(fw_seen_file) as f:
                seen_ids = json.load(f)
            with get_db() as conn:
                cursor = conn.cursor()
                for job_id in seen_ids:
                    cursor.execute(
                        "INSERT OR IGNORE INTO seen_projects (project_id, source) VALUES (?, ?)",
                        (job_id, "fastwork")
                    )
                conn.commit()
            logger.info(f"Migrated {len(seen_ids)} fastwork seen jobs from JSON")
        except Exception as e:
            logger.error(f"Error migrating fastwork_seen: {e}")
    
    # Migrate sribu_seen.json
    sribu_seen_file = os.path.join(data_dir, "sribu_seen.json")
    if os.path.exists(sribu_seen_file):
        try:
            with open(sribu_seen_file) as f:
                seen_ids = json.load(f)
            with get_db() as conn:
                cursor = conn.cursor()
                for contest_id in seen_ids:
                    cursor.execute(
                        "INSERT OR IGNORE INTO seen_projects (project_id, source) VALUES (?, ?)",
                        (contest_id, "sribu")
                    )
                conn.commit()
            logger.info(f"Migrated {len(seen_ids)} sribu seen contests from JSON")
        except Exception as e:
            logger.error(f"Error migrating sribu_seen: {e}")
    
    logger.info("JSON migration complete")


# ============================================================
# Project Operations
# ============================================================


def add_project(project_id: str, title: str, budget: str, budget_raw: float,
                category: str, client_name: str, posted_date: str, 
                description: str, url: str, source: str = "projects") -> bool:
    """Add a new project to the database."""
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO projects 
                (id, title, budget, budget_raw, category, client_name, posted_date, description, url, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (project_id, title, budget, budget_raw, category, client_name, 
                  posted_date, description, url, source))
            conn.commit()
            return True
    except Exception as e:
        logger.error(f"Error adding project: {e}")
        return False


def is_project_seen(project_id: str, source: str = "projects") -> bool:
    """Check if a project has been seen."""
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM seen_projects WHERE project_id = ? AND source = ?",
                (project_id, source)
            )
            return cursor.fetchone() is not None
    except Exception as e:
        logger.error(f"Error checking seen project: {e}")
        return False


def mark_project_seen(project_id: str, source: str = "projects") -> bool:
    """Mark a project as seen."""
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR IGNORE INTO seen_projects (project_id, source) VALUES (?, ?)
            """, (project_id, source))
            conn.commit()
            return True
    except Exception as e:
        logger.error(f"Error marking project seen: {e}")
        return False


# ============================================================
# Stats Operations
# ============================================================


def update_daily_stats(date: str, category: str, project_count: int,
                       avg_budget: float, total_budget: float,
                       source: str = "projects") -> bool:
    """Update or insert daily stats for a category."""
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO daily_stats (date, category, project_count, avg_budget, total_budget, source)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(date, category, source) DO UPDATE SET
                    project_count = project_count + excluded.project_count,
                    total_budget = total_budget + excluded.total_budget,
                    avg_budget = CASE 
                        WHEN project_count + excluded.project_count > 0 
                        THEN (total_budget + excluded.total_budget) / (project_count + excluded.project_count)
                        ELSE 0 END
            """, (date, category, project_count, avg_budget, total_budget, source))
            conn.commit()
            return True
    except Exception as e:
        logger.error(f"Error updating daily stats: {e}")
        return False


def get_daily_stats(date: str = None, source: str = "projects") -> list:
    """Get daily stats, optionally for a specific date."""
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            if date:
                cursor.execute(
                    "SELECT * FROM daily_stats WHERE date = ? AND source = ?",
                    (date, source)
                )
            else:
                cursor.execute(
                    "SELECT * FROM daily_stats WHERE source = ? ORDER BY date DESC",
                    (source,)
                )
            return [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        logger.error(f"Error getting daily stats: {e}")
        return []


def update_client_stats(client_name: str, project_count: int = 1,
                        budget: float = 0, last_seen: str = None) -> bool:
    """Update client statistics."""
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO client_stats (client_name, project_count, total_budget, last_seen)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(client_name) DO UPDATE SET
                    project_count = project_count + excluded.project_count,
                    total_budget = total_budget + excluded.total_budget,
                    avg_budget = CASE 
                        WHEN project_count + excluded.project_count > 0 
                        THEN (total_budget + excluded.total_budget) / (project_count + excluded.project_count)
                        ELSE 0 END,
                    last_seen = excluded.last_seen
            """, (client_name, project_count, budget, last_seen))
            conn.commit()
            return True
    except Exception as e:
        logger.error(f"Error updating client stats: {e}")
        return False


def get_top_clients(limit: int = 10) -> list:
    """Get top clients by project count."""
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM client_stats 
                ORDER BY project_count DESC 
                LIMIT ?
            """, (limit,))
            return [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        logger.error(f"Error getting top clients: {e}")
        return []


# ============================================================
# User Settings Operations
# ============================================================


def save_user_settings(chat_id: str, settings: dict) -> bool:
    """Save user settings as JSON."""
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO user_settings (chat_id, settings_json)
                VALUES (?, ?)
            """, (chat_id, json.dumps(settings)))
            conn.commit()
            return True
    except Exception as e:
        logger.error(f"Error saving user settings: {e}")
        return False


def get_user_settings(chat_id: str) -> dict:
    """Get user settings."""
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT settings_json FROM user_settings WHERE chat_id = ?",
                (chat_id,)
            )
            row = cursor.fetchone()
            if row:
                return json.loads(row["settings_json"])
            return {}
    except Exception as e:
        logger.error(f"Error getting user settings: {e}")
        return {}


# ============================================================
# Monitor Operations
# ============================================================


def add_monitored_category(chat_id: str, category: str, source: str = "projects") -> bool:
    """Add a category to user's monitoring."""
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR IGNORE INTO user_monitor (chat_id, category, source)
                VALUES (?, ?, ?)
            """, (chat_id, category, source))
            conn.commit()
            return True
    except Exception as e:
        logger.error(f"Error adding monitored category: {e}")
        return False


def remove_monitored_category(chat_id: str, category: str, source: str = "projects") -> bool:
    """Remove a category from user's monitoring."""
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                DELETE FROM user_monitor WHERE chat_id = ? AND category = ? AND source = ?
            """, (chat_id, category, source))
            conn.commit()
            return True
    except Exception as e:
        logger.error(f"Error removing monitored category: {e}")
        return False


def get_monitored_categories(chat_id: str = "global", source: str = "projects") -> list:
    """Get all monitored categories for a user."""
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT category FROM user_monitor WHERE chat_id = ? AND source = ?
            """, (chat_id, source))
            return [row["category"] for row in cursor.fetchall()]
    except Exception as e:
        logger.error(f"Error getting monitored categories: {e}")
        return []


def toggle_monitored_category(chat_id: str, category: str, 
                               source: str = "projects") -> bool:
    """Toggle a category in user's monitoring. Returns new state."""
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            # Check if exists
            cursor.execute("""
                SELECT 1 FROM user_monitor WHERE chat_id = ? AND category = ? AND source = ?
            """, (chat_id, category, source))
            exists = cursor.fetchone() is not None
            
            if exists:
                cursor.execute("""
                    DELETE FROM user_monitor WHERE chat_id = ? AND category = ? AND source = ?
                """, (chat_id, category, source))
            else:
                cursor.execute("""
                    INSERT INTO user_monitor (chat_id, category, source) VALUES (?, ?, ?)
                """, (chat_id, category, source))
            conn.commit()
            return not exists  # Return new state (True = was added = now monitored)
    except Exception as e:
        logger.error(f"Error toggling monitored category: {e}")
        return False


# ============================================================
# Sribu Contest Operations
# ============================================================


def add_sribu_contest(contest_id: str, title: str, budget: str, budget_raw: float,
                      category: str, category_name: str, client_name: str,
                      posted_date: str, description: str, url: str) -> bool:
    """Add a new Sribu contest."""
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO sribu_contests
                (id, title, budget, budget_raw, category, category_name, client_name, 
                 posted_date, description, url)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (contest_id, title, budget, budget_raw, category, category_name,
                  client_name, posted_date, description, url))
            conn.commit()
            return True
    except Exception as e:
        logger.error(f"Error adding sribu contest: {e}")
        return False


def is_sribu_seen(contest_id: str) -> bool:
    """Check if a Sribu contest has been seen."""
    return is_project_seen(contest_id, source="sribu")


def mark_sribu_seen(contest_id: str) -> bool:
    """Mark a Sribu contest as seen."""
    return mark_project_seen(contest_id, source="sribu")


# ============================================================
# Fastwork Job Operations
# ============================================================


def add_fastwork_job(job_id: str, title: str, budget: str, budget_raw: float,
                     tag_id: str, tag_name: str, client_name: str,
                     posted_date: str, description: str, url: str) -> bool:
    """Add a new Fastwork job."""
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO fastwork_jobs
                (id, title, budget, budget_raw, tag_id, tag_name, client_name,
                 posted_date, description, url)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (job_id, title, budget, budget_raw, tag_id, tag_name,
                  client_name, posted_date, description, url))
            conn.commit()
            return True
    except Exception as e:
        logger.error(f"Error adding fastwork job: {e}")
        return False


def is_fastwork_seen(job_id: str) -> bool:
    """Check if a Fastwork job has been seen."""
    return is_project_seen(job_id, source="fastwork")


def mark_fastwork_seen(job_id: str) -> bool:
    """Mark a Fastwork job as seen."""
    return mark_project_seen(job_id, source="fastwork")


# ============================================================
# Initialization
# ============================================================


def init():
    """Initialize the database and run migrations."""
    init_db()
    migrate_from_json()


if __name__ == "__main__":
    init()
    print("Database initialized and migrations complete.")