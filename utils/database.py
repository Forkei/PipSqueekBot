import aiosqlite
import uuid
import time
import os

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'db', 'pipsqueek.db')


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS playlists (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                owner_id INTEGER NOT NULL,
                owner_name TEXT NOT NULL,
                is_public INTEGER NOT NULL DEFAULT 1,
                created_at INTEGER NOT NULL
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS playlist_tracks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                playlist_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                title TEXT NOT NULL,
                url TEXT NOT NULL,
                duration INTEGER,
                thumbnail TEXT,
                added_by_id INTEGER NOT NULL,
                added_by_name TEXT NOT NULL,
                added_at INTEGER NOT NULL,
                FOREIGN KEY (playlist_id) REFERENCES playlists(id) ON DELETE CASCADE
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS play_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                user_name TEXT NOT NULL,
                video_id TEXT NOT NULL,
                title TEXT NOT NULL,
                url TEXT NOT NULL,
                played_at INTEGER NOT NULL
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS agent_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                updated_at INTEGER NOT NULL,
                UNIQUE(guild_id, key)
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS conversation_context (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                author_name TEXT,
                timestamp INTEGER NOT NULL
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS taste_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                profile_text TEXT NOT NULL,
                updated_at INTEGER NOT NULL,
                UNIQUE(guild_id, user_id)
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS guild_config (
                guild_id INTEGER PRIMARY KEY,
                mode TEXT NOT NULL DEFAULT 'mk1',
                agent_channel_id INTEGER
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                display_name TEXT NOT NULL,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            )
        ''')
        # Migration: add author_id to conversation_context if not present
        try:
            await db.execute('ALTER TABLE conversation_context ADD COLUMN author_id INTEGER')
        except Exception:
            pass
        await db.commit()


async def create_playlist(name: str, owner_id: int, owner_name: str, is_public: bool = True) -> str:
    playlist_id = str(uuid.uuid4())[:8].upper()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            'INSERT INTO playlists (id, name, owner_id, owner_name, is_public, created_at) VALUES (?, ?, ?, ?, ?, ?)',
            (playlist_id, name, owner_id, owner_name, 1 if is_public else 0, int(time.time()))
        )
        await db.commit()
    return playlist_id


async def get_playlist(playlist_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM playlists WHERE id = ?', (playlist_id.upper(),)) as cursor:
            return await cursor.fetchone()


async def get_user_playlists(owner_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            'SELECT p.*, COUNT(t.id) as track_count FROM playlists p '
            'LEFT JOIN playlist_tracks t ON p.id = t.playlist_id '
            'WHERE p.owner_id = ? GROUP BY p.id ORDER BY p.created_at DESC',
            (owner_id,)
        ) as cursor:
            return await cursor.fetchall()


async def get_public_playlists():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            'SELECT p.*, COUNT(t.id) as track_count FROM playlists p '
            'LEFT JOIN playlist_tracks t ON p.id = t.playlist_id '
            'WHERE p.is_public = 1 GROUP BY p.id ORDER BY p.created_at DESC LIMIT 20'
        ) as cursor:
            return await cursor.fetchall()


async def get_playlist_tracks(playlist_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            'SELECT * FROM playlist_tracks WHERE playlist_id = ? ORDER BY position ASC',
            (playlist_id.upper(),)
        ) as cursor:
            return await cursor.fetchall()


async def add_track_to_playlist(playlist_id: str, title: str, url: str, duration: int,
                                 thumbnail: str, added_by_id: int, added_by_name: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            'SELECT COALESCE(MAX(position), 0) + 1 FROM playlist_tracks WHERE playlist_id = ?',
            (playlist_id.upper(),)
        ) as cursor:
            row = await cursor.fetchone()
            next_pos = row[0]
        await db.execute(
            'INSERT INTO playlist_tracks (playlist_id, position, title, url, duration, thumbnail, added_by_id, added_by_name, added_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (playlist_id.upper(), next_pos, title, url, duration, thumbnail, added_by_id, added_by_name, int(time.time()))
        )
        await db.commit()
        return next_pos


async def remove_track_from_playlist(playlist_id: str, position: int) -> bool:
    playlist_id = playlist_id.upper()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            'SELECT id FROM playlist_tracks WHERE playlist_id = ? AND position = ?',
            (playlist_id, position)
        ) as cursor:
            row = await cursor.fetchone()
        if not row:
            return False
        await db.execute('DELETE FROM playlist_tracks WHERE playlist_id = ? AND position = ?', (playlist_id, position))
        await db.execute(
            'UPDATE playlist_tracks SET position = position - 1 WHERE playlist_id = ? AND position > ?',
            (playlist_id, position)
        )
        await db.commit()
        return True


async def delete_playlist(playlist_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('DELETE FROM playlist_tracks WHERE playlist_id = ?', (playlist_id.upper(),))
        await db.execute('DELETE FROM playlists WHERE id = ?', (playlist_id.upper(),))
        await db.commit()


async def set_playlist_privacy(playlist_id: str, is_public: bool):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            'UPDATE playlists SET is_public = ? WHERE id = ?',
            (1 if is_public else 0, playlist_id.upper())
        )
        await db.commit()


async def rename_playlist(playlist_id: str, new_name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('UPDATE playlists SET name = ? WHERE id = ?', (new_name, playlist_id.upper()))
        await db.commit()


# ─── Play History ──────────────────────────────────────────────────────────────

async def log_play(guild_id: int, user_id: int, user_name: str,
                   video_id: str, title: str, url: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            'INSERT INTO play_history (guild_id, user_id, user_name, video_id, title, url, played_at) '
            'VALUES (?, ?, ?, ?, ?, ?, ?)',
            (guild_id, user_id, user_name, video_id, title, url, int(time.time()))
        )
        await db.commit()


async def get_play_history(guild_id: int, limit: int = 20) -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            'SELECT * FROM play_history WHERE guild_id = ? ORDER BY played_at DESC LIMIT ?',
            (guild_id, limit)
        ) as cursor:
            return await cursor.fetchall()


async def get_user_play_history(guild_id: int, user_id: int, limit: int = 20) -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            'SELECT * FROM play_history WHERE guild_id = ? AND user_id = ? ORDER BY played_at DESC LIMIT ?',
            (guild_id, user_id, limit)
        ) as cursor:
            return await cursor.fetchall()


# ─── Agent Memory ──────────────────────────────────────────────────────────────

async def store_memory(guild_id: int, key: str, value: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            'INSERT INTO agent_memory (guild_id, key, value, updated_at) VALUES (?, ?, ?, ?) '
            'ON CONFLICT(guild_id, key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at',
            (guild_id, key, value, int(time.time()))
        )
        await db.commit()


async def get_memory(guild_id: int, key: str) -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            'SELECT value FROM agent_memory WHERE guild_id = ? AND key = ?',
            (guild_id, key)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None


async def list_memories(guild_id: int) -> list[tuple[str, str]]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            'SELECT key, value FROM agent_memory WHERE guild_id = ? ORDER BY updated_at DESC',
            (guild_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [(r[0], r[1]) for r in rows]


async def delete_memory(guild_id: int, key: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('DELETE FROM agent_memory WHERE guild_id = ? AND key = ?', (guild_id, key))
        await db.commit()


# ─── Conversation Context ──────────────────────────────────────────────────────

CONTEXT_LIMIT = 30


async def upsert_user(guild_id: int, user_id: int, display_name: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            'INSERT INTO users (guild_id, user_id, display_name, updated_at) VALUES (?, ?, ?, ?) '
            'ON CONFLICT(guild_id, user_id) DO UPDATE SET display_name=excluded.display_name, updated_at=excluded.updated_at',
            (guild_id, user_id, display_name, int(time.time()))
        )
        await db.commit()


async def get_user_id_by_name(guild_id: int, display_name: str) -> int | None:
    """Look up user_id for a display name (case-insensitive). Returns None if unknown."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            'SELECT user_id FROM users WHERE guild_id = ? AND lower(display_name) = lower(?)',
            (guild_id, display_name)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None


async def save_conversation_turn(guild_id: int, role: str, content: str,
                                  author_name: str = None, author_id: int = None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            'INSERT INTO conversation_context (guild_id, role, content, author_name, author_id, timestamp) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (guild_id, role, content, author_name, author_id, int(time.time()))
        )
        async with db.execute(
            'SELECT id FROM conversation_context WHERE guild_id = ? ORDER BY timestamp DESC LIMIT -1 OFFSET ?',
            (guild_id, CONTEXT_LIMIT)
        ) as cursor:
            old_ids = [r[0] for r in await cursor.fetchall()]
        if old_ids:
            await db.execute(
                f'DELETE FROM conversation_context WHERE id IN ({",".join("?" * len(old_ids))})',
                old_ids
            )
        await db.commit()


async def get_conversation_history(guild_id: int, limit: int = CONTEXT_LIMIT) -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            'SELECT role, content, author_name, timestamp FROM conversation_context '
            'WHERE guild_id = ? ORDER BY timestamp ASC',
            (guild_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return list(rows)[-limit:]


async def clear_conversation_history(guild_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('DELETE FROM conversation_context WHERE guild_id = ?', (guild_id,))
        await db.commit()


# ─── Taste Profiles ────────────────────────────────────────────────────────────

async def store_taste_profile(guild_id: int, user_id: int, profile_text: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            'INSERT INTO taste_profiles (guild_id, user_id, profile_text, updated_at) VALUES (?, ?, ?, ?) '
            'ON CONFLICT(guild_id, user_id) DO UPDATE SET profile_text=excluded.profile_text, updated_at=excluded.updated_at',
            (guild_id, user_id, profile_text, int(time.time()))
        )
        await db.commit()


# ─── Guild Config ──────────────────────────────────────────────────────────────

async def save_guild_config(guild_id: int, mode: str, agent_channel_id: int | None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            'INSERT INTO guild_config (guild_id, mode, agent_channel_id) VALUES (?, ?, ?) '
            'ON CONFLICT(guild_id) DO UPDATE SET mode=excluded.mode, agent_channel_id=excluded.agent_channel_id',
            (guild_id, mode, agent_channel_id)
        )
        await db.commit()


async def load_all_guild_configs() -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT guild_id, mode, agent_channel_id FROM guild_config') as cursor:
            return await cursor.fetchall()


# ─── Taste Profiles ────────────────────────────────────────────────────────────

async def get_taste_profile(guild_id: int, user_id: int) -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            'SELECT profile_text FROM taste_profiles WHERE guild_id = ? AND user_id = ?',
            (guild_id, user_id)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None
