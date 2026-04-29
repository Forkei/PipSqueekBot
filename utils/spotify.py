import re
import os
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from dotenv import load_dotenv

load_dotenv()

_sp: spotipy.Spotify | None = None


def _get_client() -> spotipy.Spotify:
    global _sp
    if _sp is None:
        client_id = os.getenv('SPOTIFY_CLIENT_ID', '').strip()
        client_secret = os.getenv('SPOTIFY_CLIENT_SECRET', '').strip()
        if not client_id or not client_secret:
            raise RuntimeError(
                'Spotify credentials missing. Add SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET to your .env.\n'
                'Get them free at: https://developer.spotify.com/dashboard\n'
                '(Create an app — any redirect URI like http://localhost:8888/callback works)'
            )
        auth = SpotifyClientCredentials(client_id=client_id, client_secret=client_secret)
        _sp = spotipy.Spotify(auth_manager=auth)
    return _sp


def _extract_id(url: str, kind: str) -> str | None:
    m = re.search(rf'open\.spotify\.com/{kind}/([A-Za-z0-9]+)', url)
    return m.group(1) if m else None


async def get_playlist_tracks(url: str) -> tuple[str, list[dict]]:
    """Returns (playlist_name, list of {title, artist, search_query})"""
    import asyncio, functools
    sp = _get_client()
    playlist_id = _extract_id(url, 'playlist')
    if not playlist_id:
        raise ValueError('Invalid Spotify playlist URL')

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, functools.partial(sp.playlist, playlist_id))
    name = result['name']
    items = result['tracks']['items']

    next_url = result['tracks'].get('next')
    while next_url:
        page = await loop.run_in_executor(None, functools.partial(sp.next, result['tracks']))
        items.extend(page['items'])
        result['tracks'] = page
        next_url = page.get('next')

    tracks = []
    for item in items:
        track = item.get('track')
        if not track:
            continue
        title = track['name']
        artists = ', '.join(a['name'] for a in track['artists'])
        tracks.append({'title': title, 'artist': artists, 'search_query': f'{title} {artists}'})

    return name, tracks


async def get_album_tracks(url: str) -> tuple[str, list[dict]]:
    """Returns (album_name, list of {title, artist, search_query})"""
    import asyncio, functools
    sp = _get_client()
    album_id = _extract_id(url, 'album')
    if not album_id:
        raise ValueError('Invalid Spotify album URL')

    loop = asyncio.get_event_loop()
    album = await loop.run_in_executor(None, functools.partial(sp.album, album_id))
    name = f"{album['name']} — {', '.join(a['name'] for a in album['artists'])}"
    tracks = []
    for track in album['tracks']['items']:
        title = track['name']
        artists = ', '.join(a['name'] for a in track['artists'])
        tracks.append({'title': title, 'artist': artists, 'search_query': f'{title} {artists}'})

    return name, tracks


def is_spotify_url(url: str) -> bool:
    return 'open.spotify.com' in url


def get_spotify_type(url: str) -> str | None:
    for kind in ('playlist', 'album', 'track'):
        if f'/{kind}/' in url:
            return kind
    return None
