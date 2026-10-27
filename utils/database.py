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
            CREATE TABLE IF NOT EXISTS agent_conversation (
                guild_id INTEGER PRIMARY KEY,
                messages_json TEXT NOT NULL,
                updated_at INTEGER NOT NULL
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
        await db.execute('''
            CREATE TABLE IF NOT EXISTS guild_kv (
                guild_id INTEGER NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                PRIMARY KEY (guild_id, key)
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS lore (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                added_by TEXT,
                created_at INTEGER NOT NULL
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS skip_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                requester_id INTEGER,
                requester_name TEXT,
                video_id TEXT,
                title TEXT NOT NULL,
                skipped_at INTEGER NOT NULL
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS game_scores (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                user_name TEXT NOT NULL,
                points INTEGER NOT NULL DEFAULT 0,
                wins INTEGER NOT NULL DEFAULT 0,
                games INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (guild_id, user_id)
            )
        ''')
        # Indexes
        await db.execute('CREATE INDEX IF NOT EXISTS idx_play_history_guild ON play_history(guild_id)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_play_history_guild_user ON play_history(guild_id, user_id)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_lore_guild ON lore(guild_id)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_skip_log_guild ON skip_log(guild_id, skipped_at)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_liked_songs_user ON liked_songs(user_id)')
        await db.execute('CREATE INDEX IF NOT EXISTS idx_agent_memory_guild ON agent_memory(guild_id)')
        # Migrations
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


# ─── Users ─────────────────────────────────────────────────────────────────────

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


async def get_guild_user_ids(guild_id: int) -> list[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT user_id FROM users WHERE guild_id = ?', (guild_id,)) as cursor:
            return [r[0] for r in await cursor.fetchall()]


# ─── Agent Conversation (persisted across restarts) ────────────────────────────

async def save_agent_conversation(guild_id: int, messages_json: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            'INSERT INTO agent_conversation (guild_id, messages_json, updated_at) VALUES (?, ?, ?) '
            'ON CONFLICT(guild_id) DO UPDATE SET messages_json=excluded.messages_json, updated_at=excluded.updated_at',
            (guild_id, messages_json, int(time.time()))
        )
        await db.commit()


async def load_agent_conversations() -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT guild_id, messages_json FROM agent_conversation') as cursor:
            return await cursor.fetchall()


async def clear_agent_conversation(guild_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('DELETE FROM agent_conversation WHERE guild_id = ?', (guild_id,))
        await db.commit()


# ─── Guild key/value (mood, wrapped schedule, ...) ─────────────────────────────

async def kv_get(guild_id: int, key: str) -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            'SELECT value FROM guild_kv WHERE guild_id = ? AND key = ?', (guild_id, key)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None


async def kv_set(guild_id: int, key: str, value: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            'INSERT INTO guild_kv (guild_id, key, value) VALUES (?, ?, ?) '
            'ON CONFLICT(guild_id, key) DO UPDATE SET value=excluded.value',
            (guild_id, key, value)
        )
        await db.commit()


# ─── Lore ──────────────────────────────────────────────────────────────────────

async def add_lore(guild_id: int, content: str, added_by: str = None) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            'INSERT INTO lore (guild_id, content, added_by, created_at) VALUES (?, ?, ?, ?)',
            (guild_id, content, added_by, int(time.time()))
        )
        await db.commit()
        return cur.lastrowid


async def list_lore(guild_id: int) -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            'SELECT * FROM lore WHERE guild_id = ? ORDER BY created_at DESC', (guild_id,)
        ) as cursor:
            return await cursor.fetchall()


async def delete_lore(guild_id: int, lore_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute('DELETE FROM lore WHERE guild_id = ? AND id = ?', (guild_id, lore_id))
        await db.commit()
        return cur.rowcount > 0


# ─── Skips ─────────────────────────────────────────────────────────────────────

async def log_skip(guild_id: int, requester_id: int | None, requester_name: str | None,
                   video_id: str | None, title: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            'INSERT INTO skip_log (guild_id, requester_id, requester_name, video_id, title, skipped_at) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (guild_id, requester_id, requester_name, video_id, title, int(time.time()))
        )
        await db.commit()


# ─── Stats (Wrapped / roast) ───────────────────────────────────────────────────

async def _rows(query: str, params: tuple) -> list:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query, params) as cursor:
            return await cursor.fetchall()


async def top_songs(guild_id: int, since: int, limit: int = 5) -> list:
    return await _rows(
        'SELECT title, COUNT(*) AS n FROM play_history WHERE guild_id = ? AND played_at >= ? '
        'GROUP BY lower(title) ORDER BY n DESC LIMIT ?', (guild_id, since, limit))


async def top_requesters(guild_id: int, since: int, bot_id: int, limit: int = 5) -> list:
    return await _rows(
        'SELECT user_name, user_id, COUNT(*) AS n FROM play_history '
        'WHERE guild_id = ? AND played_at >= ? AND user_id != ? '
        'GROUP BY user_id ORDER BY n DESC LIMIT ?', (guild_id, since, bot_id, limit))


async def play_counts(guild_id: int, since: int, bot_id: int) -> dict:
    rows = await _rows(
        'SELECT COUNT(*) AS total, COUNT(DISTINCT lower(title)) AS uniq, '
        'SUM(CASE WHEN user_id = ? THEN 1 ELSE 0 END) AS pip '
        'FROM play_history WHERE guild_id = ? AND played_at >= ?', (bot_id, guild_id, since))
    r = rows[0]
    return {'total': r['total'] or 0, 'unique': r['uniq'] or 0, 'pip': r['pip'] or 0}


async def most_skipped(guild_id: int, since: int, limit: int = 3) -> list:
    return await _rows(
        'SELECT title, requester_name, COUNT(*) AS n FROM skip_log WHERE guild_id = ? AND skipped_at >= ? '
        'GROUP BY lower(title) ORDER BY n DESC LIMIT ?', (guild_id, since, limit))


async def skip_count_for_user(guild_id: int, user_id: int) -> int:
    rows = await _rows(
        'SELECT COUNT(*) AS n FROM skip_log WHERE guild_id = ? AND requester_id = ?', (guild_id, user_id))
    return rows[0]['n'] or 0


async def requests_in_window(guild_id: int, since: int, bot_id: int, limit: int = 60) -> list:
    return await _rows(
        'SELECT user_name, title FROM play_history WHERE guild_id = ? AND played_at >= ? AND user_id != ? '
        'ORDER BY played_at DESC LIMIT ?', (guild_id, since, bot_id, limit))


async def most_liked(user_ids: list[int], since: int, limit: int = 3) -> list:
    if not user_ids:
        return []
    marks = ','.join('?' * len(user_ids))
    return await _rows(
        f'SELECT title, COUNT(*) AS n FROM liked_songs WHERE user_id IN ({marks}) AND liked_at >= ? '
        f'GROUP BY video_id ORDER BY n DESC LIMIT ?', (*user_ids, since, limit))


# ─── Game Scores ───────────────────────────────────────────────────────────────

async def add_game_result(guild_id: int, user_id: int, user_name: str, points: int, won: bool):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            'INSERT INTO game_scores (guild_id, user_id, user_name, points, wins, games) VALUES (?, ?, ?, ?, ?, 1) '
            'ON CONFLICT(guild_id, user_id) DO UPDATE SET user_name=excluded.user_name, '
            'points=points+excluded.points, wins=wins+excluded.wins, games=games+1',
            (guild_id, user_id, user_name, points, 1 if won else 0)
        )
        await db.commit()


async def game_leaderboard(guild_id: int, limit: int = 10) -> list:
    return await _rows(
        'SELECT * FROM game_scores WHERE guild_id = ? ORDER BY points DESC LIMIT ?', (guild_id, limit))


async def game_score_for(guild_id: int, user_id: int):
    rows = await _rows('SELECT * FROM game_scores WHERE guild_id = ? AND user_id = ?', (guild_id, user_id))
    return rows[0] if rows else None


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
