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
                user_id INTEGER PRIMARY KEY,
                profile_text TEXT NOT NULL,
                updated_at INTEGER NOT NULL
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
                username TEXT,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS liked_songs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                user_name TEXT NOT NULL,
                video_id TEXT NOT NULL,
                title TEXT NOT NULL,
                url TEXT NOT NULL,
                liked_at INTEGER NOT NULL,
                UNIQUE(user_id, video_id)
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS dev_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
        ''')
        # Indexes
        await db.execute('CREATE INDEX IF NOT EXISTS idx_play_history_guild ON play_history(guild_id)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_play_history_guild_user ON play_history(guild_id, user_id)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_conv_guild_ts ON conversation_context(guild_id, timestamp)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_liked_songs_user ON liked_songs(user_id)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_agent_memory_guild ON agent_memory(guild_id)')
        # Migrations
        try:
            await db.execute('ALTER TABLE conversation_context ADD COLUMN author_id INTEGER')
        except Exception:
            pass
        try:
            await db.execute('ALTER TABLE users ADD COLUMN username TEXT')
        except Exception:
            pass
        # Migrate taste_profiles: drop guild_id, make global per user
        try:
            async with db.execute('PRAGMA table_info(taste_profiles)') as cur:
                cols = [row[1] for row in await cur.fetchall()]
            if 'guild_id' in cols:
                await db.execute(
                    'CREATE TABLE taste_profiles_new '
                    '(user_id INTEGER PRIMARY KEY, profile_text TEXT NOT NULL, updated_at INTEGER NOT NULL)'
                )
                await db.execute(
                    'INSERT INTO taste_profiles_new (user_id, profile_text, updated_at) '
                    'SELECT user_id, profile_text, MAX(updated_at) FROM taste_profiles GROUP BY user_id'
                )
                await db.execute('DROP TABLE taste_profiles')
                await db.execute('ALTER TABLE taste_profiles_new RENAME TO taste_profiles')
        except Exception:
            pass
        # Migrate liked_songs: drop guild_id, make global per user
        try:
            async with db.execute('PRAGMA table_info(liked_songs)') as cur:
                cols = [row[1] for row in await cur.fetchall()]
            if 'guild_id' in cols:
                await db.execute(
                    'CREATE TABLE liked_songs_new ('
                    'id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, '
                    'user_name TEXT NOT NULL, video_id TEXT NOT NULL, title TEXT NOT NULL, '
                    'url TEXT NOT NULL, liked_at INTEGER NOT NULL, UNIQUE(user_id, video_id))'
                )
                await db.execute(
                    'INSERT OR IGNORE INTO liked_songs_new (user_id, user_name, video_id, title, url, liked_at) '
                    'SELECT user_id, user_name, video_id, title, url, MIN(liked_at) '
                    'FROM liked_songs GROUP BY user_id, video_id'
                )
                await db.execute('DROP TABLE liked_songs')
                await db.execute('ALTER TABLE liked_songs_new RENAME TO liked_songs')
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


async def upsert_user(guild_id: int, user_id: int, display_name: str, username: str = None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            'INSERT INTO users (guild_id, user_id, display_name, username, updated_at) VALUES (?, ?, ?, ?, ?) '
            'ON CONFLICT(guild_id, user_id) DO UPDATE SET '
            'display_name=excluded.display_name, username=excluded.username, updated_at=excluded.updated_at',
            (guild_id, user_id, display_name, username, int(time.time()))
        )
        await db.commit()


async def get_user_id_by_name(guild_id: int, name: str) -> int | None:
    """Look up user_id by display name or stable username (case-insensitive)."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            'SELECT user_id FROM users WHERE guild_id = ? '
            'AND (lower(display_name) = lower(?) OR lower(username) = lower(?))',
            (guild_id, name, name)
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
        await db.execute(
            'DELETE FROM conversation_context WHERE guild_id = ? AND id NOT IN '
            '(SELECT id FROM conversation_context WHERE guild_id = ? ORDER BY timestamp DESC LIMIT ?)',
            (guild_id, guild_id, CONTEXT_LIMIT)
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

async def store_taste_profile(user_id: int, profile_text: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            'INSERT INTO taste_profiles (user_id, profile_text, updated_at) VALUES (?, ?, ?) '
            'ON CONFLICT(user_id) DO UPDATE SET profile_text=excluded.profile_text, updated_at=excluded.updated_at',
            (user_id, profile_text, int(time.time()))
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


# ─── Liked Songs ───────────────────────────────────────────────────────────────

async def like_song(user_id: int, user_name: str, video_id: str, title: str, url: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            'INSERT OR IGNORE INTO liked_songs (user_id, user_name, video_id, title, url, liked_at) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (user_id, user_name, video_id, title, url, int(time.time()))
        )
        await db.commit()


async def unlike_song(user_id: int, video_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            'DELETE FROM liked_songs WHERE user_id = ? AND video_id = ?',
            (user_id, video_id)
        )
        await db.commit()


async def get_liked_songs(user_id: int | None = None, limit: int = 50) -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if user_id is not None:
            async with db.execute(
                'SELECT * FROM liked_songs WHERE user_id = ? ORDER BY liked_at DESC LIMIT ?',
                (user_id, limit)
            ) as cursor:
                return await cursor.fetchall()
        else:
            async with db.execute(
                'SELECT * FROM liked_songs ORDER BY liked_at DESC LIMIT ?',
                (limit,)
            ) as cursor:
                return await cursor.fetchall()


async def is_song_liked(user_id: int, video_id: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            'SELECT 1 FROM liked_songs WHERE user_id = ? AND video_id = ?',
            (user_id, video_id)
        ) as cursor:
            return await cursor.fetchone() is not None


# ─── Taste Profiles ────────────────────────────────────────────────────────────

async def get_taste_profile(user_id: int) -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            'SELECT profile_text FROM taste_profiles WHERE user_id = ?',
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None


# ─── Developer Notes ───────────────────────────────────────────────────────────

async def add_dev_note(guild_id: int, content: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            'INSERT INTO dev_notes (guild_id, content, created_at) VALUES (?, ?, ?)',
            (guild_id, content, int(time.time()))
        )
        await db.commit()


async def get_dev_notes(guild_id: int, limit: int = 20) -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            'SELECT id, content, created_at FROM dev_notes WHERE guild_id = ? ORDER BY created_at DESC LIMIT ?',
            (guild_id, limit)
        ) as cursor:
            return await cursor.fetchall()


async def clear_dev_notes(guild_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('DELETE FROM dev_notes WHERE guild_id = ?', (guild_id,))
        await db.commit()
