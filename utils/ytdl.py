import asyncio
import os
import hashlib
import yt_dlp
from dotenv import load_dotenv

load_dotenv()

CACHE_DIR = os.path.join(os.path.dirname(__file__), '..', 'data', 'cache')
COOKIES_FILE = os.getenv('YOUTUBE_COOKIES_FILE', '')
CACHE_MAX_MB = int(os.getenv('CACHE_MAX_MB', '2048'))
CACHE_MAX_SONGS = int(os.getenv('CACHE_MAX_SONGS', '500'))

os.makedirs(CACHE_DIR, exist_ok=True)

YTDL_FORMAT_OPTIONS = {
    'format': 'bestaudio/best',
    'outtmpl': os.path.join(CACHE_DIR, '%(id)s.%(ext)s'),
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'ytsearch',
    'source_address': '0.0.0.0',
    'postprocessors': [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'opus',
        'preferredquality': '192',
    }],
    'postprocessor_args': ['-vn'],
    'prefer_ffmpeg': True,
    'keepvideo': False,
}

YTDL_PLAYLIST_OPTIONS = {
    'format': 'bestaudio/best',
    'nocheckcertificate': True,
    'ignoreerrors': True,
    'quiet': True,
    'no_warnings': True,
    'extract_flat': True,
    'skip_download': True,
}

if COOKIES_FILE and os.path.exists(COOKIES_FILE):
    YTDL_FORMAT_OPTIONS['cookiefile'] = COOKIES_FILE
    YTDL_PLAYLIST_OPTIONS['cookiefile'] = COOKIES_FILE


def _cache_path_for_id(video_id: str) -> str | None:
    for fname in os.listdir(CACHE_DIR):
        if fname.startswith(video_id) and not fname.endswith('.part'):
            return os.path.join(CACHE_DIR, fname)
    return None


def _enforce_cache_limits():
    files = []
    for fname in os.listdir(CACHE_DIR):
        fpath = os.path.join(CACHE_DIR, fname)
        stat = os.stat(fpath)
        files.append((stat.st_atime, fpath, stat.st_size))
    files.sort()

    total_mb = sum(f[2] for f in files) / (1024 * 1024)
    while files and (len(files) > CACHE_MAX_SONGS or total_mb > CACHE_MAX_MB):
        _, oldest_path, size_bytes = files.pop(0)
        os.remove(oldest_path)
        total_mb -= size_bytes / (1024 * 1024)


async def search_ytmusic(query: str, limit: int = 5) -> list[dict]:
    opts = {**YTDL_PLAYLIST_OPTIONS, 'noplaylist': True, 'default_search': 'ytsearch5'}
    loop = asyncio.get_running_loop()
    def _search():
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(f'ytsearch{limit}:{query}', download=False)
    info = await loop.run_in_executor(None, _search)
    if not info or 'entries' not in info:
        return []
    return [
        {
            'id': e.get('id'),
            'title': e.get('title'),
            'url': e.get('webpage_url') or f"https://www.youtube.com/watch?v={e.get('id')}",
            'duration': e.get('duration', 0),
            'thumbnail': e.get('thumbnail'),
            'uploader': e.get('uploader'),
        }
        for e in info['entries'] if e
    ]


async def extract_info(url: str) -> dict | None:
    loop = asyncio.get_running_loop()
    opts = {**YTDL_FORMAT_OPTIONS, 'noplaylist': True, 'skip_download': True}
    def _extract():
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)
    try:
        info = await loop.run_in_executor(None, _extract)
    except yt_dlp.utils.DownloadError:
        return None
    if not info:
        return None
    return {
        'id': info.get('id'),
        'title': info.get('title'),
        'url': info.get('webpage_url') or url,
        'duration': info.get('duration', 0),
        'thumbnail': info.get('thumbnail'),
        'uploader': info.get('uploader'),
    }


async def extract_playlist(url: str) -> list[dict]:
    loop = asyncio.get_running_loop()
    def _extract():
        with yt_dlp.YoutubeDL(YTDL_PLAYLIST_OPTIONS) as ydl:
            return ydl.extract_info(url, download=False)
    try:
        info = await loop.run_in_executor(None, _extract)
    except yt_dlp.utils.DownloadError:
        return []
    if not info:
        return []
    entries = info.get('entries', [])
    tracks = []
    for e in entries:
        if not e:
            continue
        vid_id = e.get('id')
        tracks.append({
            'id': vid_id,
            'title': e.get('title', 'Unknown'),
            'url': e.get('url') or e.get('webpage_url') or f"https://www.youtube.com/watch?v={vid_id}",
            'duration': e.get('duration', 0),
            'thumbnail': e.get('thumbnail'),
            'uploader': e.get('uploader'),
        })
    return tracks


async def download_and_get_path(url: str, video_id: str = None) -> str | None:
    loop = asyncio.get_running_loop()

    if video_id:
        cached = _cache_path_for_id(video_id)
        if cached:
            return cached

    _enforce_cache_limits()

    def _download():
        with yt_dlp.YoutubeDL(YTDL_FORMAT_OPTIONS) as ydl:
            return ydl.extract_info(url, download=True)
    try:
        info = await loop.run_in_executor(None, _download)
    except yt_dlp.utils.DownloadError:
        return None

    if not info:
        return None

    vid_id = info.get('id') or video_id
    if vid_id:
        path = _cache_path_for_id(vid_id)
        if path:
            return path

    return None


def format_duration(seconds: int) -> str:
    if not seconds:
        return '?:??'
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f'{h}:{m:02d}:{s:02d}'
    return f'{m}:{s:02d}'
