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
