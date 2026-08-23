"""Optional Google OAuth, Drive profile backup, and YouTube playlist access."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import threading
import time
import webbrowser
from dataclasses import dataclass
from difflib import SequenceMatcher
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen


OAUTH_SCOPES = (
    "openid",
    "email",
    "https://www.googleapis.com/auth/drive.appdata",
    "https://www.googleapis.com/auth/youtube.readonly",
)
PROFILE_FILE_NAME = "youtube-media-studio-profile.json"
KEYRING_SERVICE = "YouTube Media Studio Google Cloud"
KEYRING_USER = "active-refresh-token"
AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
PORTABLE_SETTING_KEYS = (
    "defaults/audio_quality",
    "defaults/sample_rate",
    "defaults/album_silence_threshold_db",
    "defaults/album_min_silence_duration",
    "defaults/album_min_track_duration",
    "defaults/album_trim_silence_padding",
    "defaults/video_seek_seconds",
    "defaults/remember_video_display_modes",
    "defaults/wikipedia_track_order",
    "defaults/ai_enabled",
    "defaults/search_suggestions",
    "defaults/crystalness",
    "updates/include_betas",
    "cloud/auto_sync",
    "workspace/persist_enabled",
    "privacy/crash_reports_enabled",
    "library/volume",
    "library/repeat_mode",
    "library/shuffle",
    "library/video_aspect_mode",
    "library/video_crop_mode",
    "library/video_view_mode",
)


class TokenStore(Protocol):
    def load(self) -> str: ...
    def save(self, token: str) -> None: ...
    def clear(self) -> None: ...


class SystemTokenStore:
    """Keep the Google refresh token in the operating-system credential vault."""

    def load(self) -> str:
        import keyring

        return str(keyring.get_password(KEYRING_SERVICE, KEYRING_USER) or "")

    def save(self, token: str) -> None:
        import keyring

        keyring.set_password(KEYRING_SERVICE, KEYRING_USER, token)

    def clear(self) -> None:
        import keyring

        try:
            keyring.delete_password(KEYRING_SERVICE, KEYRING_USER)
        except keyring.errors.PasswordDeleteError:
            pass


@dataclass(frozen=True, slots=True)
class GoogleOAuthConfig:
    client_id: str
    client_secret: str
    auth_uri: str = AUTH_URI
    token_uri: str = TOKEN_URI

    @classmethod
    def from_file(cls, path: str | Path) -> GoogleOAuthConfig:
        payload = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
        installed = payload.get("installed") if isinstance(payload, dict) else None
        if not isinstance(installed, dict) or not installed.get("client_id"):
            raise ValueError("Select a Google OAuth desktop-client JSON file")
        return cls(
            client_id=str(installed["client_id"]),
            client_secret=str(installed.get("client_secret") or ""),
            auth_uri=str(installed.get("auth_uri") or AUTH_URI),
            token_uri=str(installed.get("token_uri") or TOKEN_URI),
        )


def build_cloud_profile(
    settings: dict[str, Any],
    playlists: dict[str, list[dict[str, Any]]],
    *,
    device_id: str,
    updated_at: str,
) -> dict[str, Any]:
    """Create the versioned, portable profile; credentials and paths are excluded."""

    portable = {
        key: value
        for key in PORTABLE_SETTING_KEYS
        if (value := settings.get(key)) is not None
        and isinstance(value, (str, int, float, bool))
    }
    return {
        "schema_version": 1,
        "updated_at": updated_at,
        "device_id": device_id,
        "settings": portable,
        "playlists": playlists,
    }


def validate_cloud_profile(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a sanitized profile or reject an incompatible cloud payload."""

    if payload.get("schema_version") != 1:
        raise ValueError("This cloud profile uses an unsupported format")
    raw_settings = payload.get("settings")
    raw_playlists = payload.get("playlists")
    if not isinstance(raw_settings, dict) or not isinstance(raw_playlists, dict):
        raise ValueError("The cloud profile is incomplete")
    settings = {
        key: value
        for key, value in raw_settings.items()
        if key in PORTABLE_SETTING_KEYS
        and isinstance(value, (str, int, float, bool))
    }
    playlists: dict[str, list[dict[str, Any]]] = {}
    for name, entries in raw_playlists.items():
        if not isinstance(name, str) or not isinstance(entries, list):
            continue
        playlists[name] = [entry for entry in entries if isinstance(entry, dict)]
    return {**payload, "settings": settings, "playlists": playlists}


def portable_playlist_snapshot(
    playlists: dict[str, list[str]], items: list[dict[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    """Replace machine-specific playlist paths with portable track identities."""

    by_path = {str(item.get("path") or "").casefold(): item for item in items}
    result: dict[str, list[dict[str, Any]]] = {}
    for name, paths in playlists.items():
        entries = []
        for path in paths:
            item = by_path.get(str(path).casefold())
            if item:
                entries.append(
                    {
                        "title": str(item.get("title") or ""),
                        "artists": str(item.get("artists") or ""),
                        "album": str(item.get("album") or ""),
                        "year": item.get("year"),
                    }
                )
        result[name] = entries
    return result


def match_portable_tracks(
    entries: list[dict[str, Any]], items: list[dict[str, Any]]
) -> tuple[list[str], list[str]]:
    """Match cloud/YouTube identities to this machine's indexed local media."""

    matched: list[str] = []
    missing: list[str] = []
    used: set[str] = set()
    for entry in entries:
        title = str(entry.get("title") or "").strip()
        artists = str(entry.get("artists") or entry.get("channel") or "").strip()
        wanted = _identity_key(title)
        best: tuple[float, dict[str, Any] | None] = (0.0, None)
        for item in items:
            path = str(item.get("path") or "")
            if not path or path.casefold() in used:
                continue
            local_title = _identity_key(item.get("title"))
            score = SequenceMatcher(None, wanted, local_title).ratio()
            if wanted and local_title and (wanted in local_title or local_title in wanted):
                score = max(score, 0.92)
            artist_key = _identity_key(artists)
            local_artist = _identity_key(item.get("artists"))
            if artist_key and local_artist and (
                artist_key in local_artist or local_artist in artist_key
            ):
                score += 0.08
            if score > best[0]:
                best = (score, item)
        if best[1] is not None and best[0] >= 0.72:
            path = str(best[1]["path"])
            matched.append(path)
            used.add(path.casefold())
        else:
            missing.append(title or "Untitled YouTube track")
    return matched, missing


def _identity_key(value: object) -> str:
    text = str(value or "").casefold()
    text = re.sub(r"\([^)]*(official|audio|video|lyrics?)[^)]*\)", " ", text)
    return re.sub(r"[^a-z0-9]+", "", text)


@dataclass(frozen=True, slots=True)
class GoogleAccount:
    email: str


@dataclass(frozen=True, slots=True)
class YouTubePlaylist:
    playlist_id: str
    title: str
    item_count: int


@dataclass(frozen=True, slots=True)
class YouTubePlaylistEntry:
    video_id: str
    title: str
    channel: str


class _OAuthCallback(BaseHTTPRequestHandler):
    query: dict[str, list[str]] = {}
    received = threading.Event()

    def do_GET(self) -> None:  # noqa: N802
        type(self).query = parse_qs(urlparse(self.path).query)
        type(self).received.set()
        body = b"Google sign-in completed. You can return to YouTube Media Studio."
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *args: object) -> None:
        del args


class GoogleCloudProfileClient:
    """Small REST client that keeps Google integration optional and account-scoped."""

    def __init__(
        self,
        config: GoogleOAuthConfig,
        *,
        token_store: TokenStore | None = None,
        timeout: float = 20.0,
    ) -> None:
        self.config = config
        self.token_store = token_store or SystemTokenStore()
        self.timeout = timeout
        self._access_token = ""
        self._expires_at = 0.0

    def connect(self, *, browser_opener: Any = webbrowser.open) -> GoogleAccount:
        """Authorize in the system browser using PKCE and a loopback callback."""

        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
        state = secrets.token_urlsafe(32)
        _OAuthCallback.query = {}
        _OAuthCallback.received.clear()
        server = ThreadingHTTPServer(("127.0.0.1", 0), _OAuthCallback)
        redirect_uri = f"http://127.0.0.1:{server.server_port}/oauth/callback"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        parameters = {
            "client_id": self.config.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(OAUTH_SCOPES),
            "access_type": "offline",
            "prompt": "consent select_account",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        browser_opener(f"{self.config.auth_uri}?{urlencode(parameters)}")
        try:
            if not _OAuthCallback.received.wait(180):
                raise TimeoutError("Google sign-in timed out")
        finally:
            server.shutdown()
            server.server_close()
        query = _OAuthCallback.query
        if query.get("state", [""])[0] != state:
            raise RuntimeError("Google sign-in returned an invalid state")
        if query.get("error"):
            raise RuntimeError(f"Google sign-in was not completed: {query['error'][0]}")
        code = query.get("code", [""])[0]
        if not code:
            raise RuntimeError("Google sign-in did not return an authorization code")
        token = self._post_form(
            self.config.token_uri,
            {
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
                "code": code,
                "code_verifier": verifier,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
        )
        self._accept_token(token)
        refresh_token = str(token.get("refresh_token") or "")
        if not refresh_token:
            raise RuntimeError("Google did not return an offline refresh token")
        self.token_store.save(refresh_token)
        return self.account()

    def is_connected(self) -> bool:
        return bool(self.token_store.load())

    def account(self) -> GoogleAccount:
        payload = self._json_request(
            "https://openidconnect.googleapis.com/v1/userinfo"
        )
        return GoogleAccount(email=str(payload.get("email") or "Google account"))

    def disconnect(self) -> None:
        refresh_token = self.token_store.load()
        if refresh_token:
            try:
                self._post_form(
                    "https://oauth2.googleapis.com/revoke",
                    {"token": refresh_token},
                    authenticated=False,
                )
            except (OSError, RuntimeError):
                pass
        self.token_store.clear()
        self._access_token = ""
        self._expires_at = 0.0

    def download_profile(self) -> dict[str, Any] | None:
        files = self._json_request(
            "https://www.googleapis.com/drive/v3/files?"
            + urlencode(
                {
                    "spaces": "appDataFolder",
                    "q": f"name='{PROFILE_FILE_NAME}' and trashed=false",
                    "fields": "files(id,name,modifiedTime)",
                }
            )
        ).get("files", [])
        if not files:
            return None
        raw = self._raw_request(
            f"https://www.googleapis.com/drive/v3/files/{files[0]['id']}?alt=media"
        )
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError("Google Drive returned an invalid cloud profile")
        return payload

    def upload_profile(self, profile: dict[str, Any]) -> None:
        content = json.dumps(profile, ensure_ascii=False, sort_keys=True).encode("utf-8")
        files = self._json_request(
            "https://www.googleapis.com/drive/v3/files?"
            + urlencode(
                {
                    "spaces": "appDataFolder",
                    "q": f"name='{PROFILE_FILE_NAME}' and trashed=false",
                    "fields": "files(id)",
                }
            )
        ).get("files", [])
        if files:
            self._raw_request(
                f"https://www.googleapis.com/upload/drive/v3/files/{files[0]['id']}?uploadType=media",
                method="PATCH",
                body=content,
                content_type="application/json; charset=utf-8",
            )
            return
        boundary = f"yms-{secrets.token_hex(12)}"
        metadata = json.dumps(
            {"name": PROFILE_FILE_NAME, "parents": ["appDataFolder"]}
        ).encode("utf-8")
        body = (
            f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode()
            + metadata
            + f"\r\n--{boundary}\r\nContent-Type: application/json\r\n\r\n".encode()
            + content
            + f"\r\n--{boundary}--\r\n".encode()
        )
        self._raw_request(
            "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart",
            method="POST",
            body=body,
            content_type=f"multipart/related; boundary={boundary}",
        )

    def youtube_playlists(self) -> list[YouTubePlaylist]:
        result: list[YouTubePlaylist] = []
        page_token = ""
        while True:
            params = {
                "part": "snippet,contentDetails",
                "mine": "true",
                "maxResults": "50",
            }
            if page_token:
                params["pageToken"] = page_token
            payload = self._json_request(
                "https://www.googleapis.com/youtube/v3/playlists?" + urlencode(params)
            )
            for item in payload.get("items", []):
                result.append(
                    YouTubePlaylist(
                        playlist_id=str(item.get("id") or ""),
                        title=str(item.get("snippet", {}).get("title") or "Untitled"),
                        item_count=int(
                            item.get("contentDetails", {}).get("itemCount") or 0
                        ),
                    )
                )
            page_token = str(payload.get("nextPageToken") or "")
            if not page_token:
                return result

    def youtube_playlist_entries(self, playlist_id: str) -> list[YouTubePlaylistEntry]:
        result: list[YouTubePlaylistEntry] = []
        page_token = ""
        while True:
            params = {
                "part": "snippet,contentDetails",
                "playlistId": playlist_id,
                "maxResults": "50",
            }
            if page_token:
                params["pageToken"] = page_token
            payload = self._json_request(
                "https://www.googleapis.com/youtube/v3/playlistItems?"
                + urlencode(params)
            )
            for item in payload.get("items", []):
                snippet = item.get("snippet", {})
                video_id = str(item.get("contentDetails", {}).get("videoId") or "")
                if video_id:
                    result.append(
                        YouTubePlaylistEntry(
                            video_id=video_id,
                            title=str(snippet.get("title") or "Untitled"),
                            channel=str(snippet.get("videoOwnerChannelTitle") or ""),
                        )
                    )
            page_token = str(payload.get("nextPageToken") or "")
            if not page_token:
                return result

    def _token(self) -> str:
        if self._access_token and time.time() < self._expires_at - 60:
            return self._access_token
        refresh_token = self.token_store.load()
        if not refresh_token:
            raise RuntimeError("Connect a Google account first")
        token = self._post_form(
            self.config.token_uri,
            {
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            authenticated=False,
        )
        self._accept_token(token)
        return self._access_token

    def _accept_token(self, token: dict[str, Any]) -> None:
        self._access_token = str(token.get("access_token") or "")
        if not self._access_token:
            raise RuntimeError("Google did not return an access token")
        self._expires_at = time.time() + int(token.get("expires_in") or 3600)

    def _post_form(
        self,
        url: str,
        values: dict[str, str],
        *,
        authenticated: bool = False,
    ) -> dict[str, Any]:
        raw = self._raw_request(
            url,
            method="POST",
            body=urlencode({key: value for key, value in values.items() if value}).encode(),
            content_type="application/x-www-form-urlencoded",
            authenticated=authenticated,
        )
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError("Google returned an unexpected response")
        return payload

    def _json_request(self, url: str) -> dict[str, Any]:
        payload = json.loads(self._raw_request(url).decode("utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError("Google returned an unexpected response")
        return payload

    def _raw_request(
        self,
        url: str,
        *,
        method: str = "GET",
        body: bytes | None = None,
        content_type: str = "",
        authenticated: bool = True,
    ) -> bytes:
        headers = {"Accept": "application/json", "User-Agent": "YouTube-Media-Studio"}
        if authenticated:
            headers["Authorization"] = f"Bearer {self._token()}"
        if content_type:
            headers["Content-Type"] = content_type
        try:
            with urlopen(
                Request(url, data=body, headers=headers, method=method),
                timeout=self.timeout,
            ) as response:
                return response.read()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Google request failed ({exc.code}): {detail}") from exc
