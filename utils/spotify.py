import os
import re
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from dotenv import load_dotenv

load_dotenv()

_sp = None


def _get_client() -> spotipy.Spotify:
    global _sp
    if _sp is None:
        client_id = os.getenv('SPOTIFY_CLIENT_ID')
        client_secret = os.getenv('SPOTIFY_CLIENT_SECRET')
        if not client_id or not client_secret:
            raise ValueError('Spotify credentials not set in .env (SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET)')
        auth = SpotifyClientCredentials(client_id=client_id, client_secret=client_secret)
        _sp = spotipy.Spotify(auth_manager=auth)
    return _sp


def _extract_id(url: str, kind: str) -> str | None:
    pattern = rf'open\.spotify\.com/{kind}/([A-Za-z0-9]+)'
    m = re.search(pattern, url)
    return m.group(1) if m else None


def get_playlist_tracks(url: str) -> tuple[str, list[dict]]:
    """Returns (playlist_name, list of {title, artist, search_query})"""
    sp = _get_client()
    playlist_id = _extract_id(url, 'playlist')
    if not playlist_id:
        raise ValueError('Invalid Spotify playlist URL')

    result = sp.playlist(playlist_id)
    name = result['name']
    tracks = []
    items = result['tracks']['items']

    while result['tracks'].get('next'):
        result['tracks'] = sp.next(result['tracks'])
        items.extend(result['tracks']['items'])

    for item in items:
        track = item.get('track')
        if not track:
            continue
        title = track['name']
        artists = ', '.join(a['name'] for a in track['artists'])
        tracks.append({
            'title': title,
            'artist': artists,
            'search_query': f'{title} {artists}',
        })

    return name, tracks


def get_album_tracks(url: str) -> tuple[str, list[dict]]:
    """Returns (album_name, list of {title, artist, search_query})"""
    sp = _get_client()
    album_id = _extract_id(url, 'album')
    if not album_id:
        raise ValueError('Invalid Spotify album URL')

    album = sp.album(album_id)
    name = f"{album['name']} — {', '.join(a['name'] for a in album['artists'])}"
    tracks = []
    for track in album['tracks']['items']:
        title = track['name']
        artists = ', '.join(a['name'] for a in track['artists'])
        tracks.append({
            'title': title,
            'artist': artists,
            'search_query': f'{title} {artists}',
        })
    return name, tracks


def is_spotify_url(url: str) -> bool:
    return 'open.spotify.com' in url


def get_spotify_type(url: str) -> str | None:
    for kind in ('playlist', 'album', 'track'):
        if f'/{kind}/' in url:
            return kind
    return None
