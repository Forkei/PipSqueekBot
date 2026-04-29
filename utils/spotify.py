import re
import aiohttp

# Uses Spotify's anonymous web player token — no credentials required.
# Works for any public playlist or album.

_ANON_TOKEN_URL = 'https://open.spotify.com/get_access_token?reason=transport&productType=web_player'
_API_BASE = 'https://api.spotify.com/v1'
_HEADERS = {'app-platform': 'WebPlayer'}


def _extract_id(url: str, kind: str) -> str | None:
    m = re.search(rf'open\.spotify\.com/{kind}/([A-Za-z0-9]+)', url)
    return m.group(1) if m else None


async def _get_token(session: aiohttp.ClientSession) -> str:
    async with session.get(_ANON_TOKEN_URL, headers=_HEADERS) as r:
        r.raise_for_status()
        data = await r.json()
        return data['accessToken']


async def _api_get(session: aiohttp.ClientSession, token: str, path: str, params: dict = None) -> dict:
    async with session.get(
        f'{_API_BASE}/{path}',
        headers={**_HEADERS, 'Authorization': f'Bearer {token}'},
        params=params or {}
    ) as r:
        r.raise_for_status()
        return await r.json()


async def get_playlist_tracks(url: str) -> tuple[str, list[dict]]:
    """Returns (playlist_name, list of {title, artist, search_query})"""
    playlist_id = _extract_id(url, 'playlist')
    if not playlist_id:
        raise ValueError('Invalid Spotify playlist URL')

    async with aiohttp.ClientSession() as session:
        token = await _get_token(session)
        data = await _api_get(session, token, f'playlists/{playlist_id}', {'fields': 'name,tracks(items(track(name,artists(name))),next)'})
        name = data['name']
        tracks = []
        items = data['tracks']['items']
        next_url = data['tracks'].get('next')

        while next_url:
            async with session.get(next_url, headers={**_HEADERS, 'Authorization': f'Bearer {token}'}) as r:
                page = await r.json()
            items.extend(page['items'])
            next_url = page.get('next')

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
    album_id = _extract_id(url, 'album')
    if not album_id:
        raise ValueError('Invalid Spotify album URL')

    async with aiohttp.ClientSession() as session:
        token = await _get_token(session)
        data = await _api_get(session, token, f'albums/{album_id}', {'fields': 'name,artists(name),tracks(items(name,artists(name)))'})
        name = f"{data['name']} — {', '.join(a['name'] for a in data['artists'])}"
        tracks = []
        for track in data['tracks']['items']:
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
