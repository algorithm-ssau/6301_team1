import os
import io
import datetime as dt
from typing import Optional
import json

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload, MediaIoBaseDownload

SCOPES = [
    'https://www.googleapis.com/auth/calendar',
    'https://www.googleapis.com/auth/drive.file',  # доступ только к файлам, созданным приложением
]
CREDENTIALS_FILE = 'credentials.json'
TOKEN_FILE = 'token.json'
DRIVE_FOLDER_NAME = 'Obsidian_Bot'
DRIVE_FILE_PROP = 'driveFileId'  # ключ в extendedProperties события


def creds_from_json(raw: str) -> Credentials | None:
    if not raw:
        return None
    info = json.loads(raw)
    creds = Credentials.from_authorized_user_info(info, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return creds

def get_credentials() -> Credentials:
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=8080, prompt='consent', open_browser=True)
        with open(TOKEN_FILE, 'w') as f:
            f.write(creds.to_json())
    return creds


# ---------------- Drive ----------------
class DriveAPI:
    def __init__(self, creds: Credentials):
        self.service = build('drive', 'v3', credentials=creds)
        self.folder_id = self._ensure_folder(DRIVE_FOLDER_NAME)

    def _ensure_folder(self, name: str) -> str:
        q = (f"name='{name}' and mimeType='application/vnd.google-apps.folder' "
             f"and trashed=false")
        res = self.service.files().list(q=q, fields='files(id,name)').execute()
        files = res.get('files', [])
        if files:
            return files[0]['id']
        meta = {'name': name, 'mimeType': 'application/vnd.google-apps.folder'}
        f = self.service.files().create(body=meta, fields='id').execute()
        return f['id']

    def upload_note(self, filename: str, content: str) -> tuple[str, str]:
        """Создаёт/обновляет файл в папке. Возвращает (file_id, webViewLink)."""
        safe = filename.replace("'", "\\'")
        q = f"name='{safe}' and '{self.folder_id}' in parents and trashed=false"
        res = self.service.files().list(q=q, fields='files(id)').execute()
        media = MediaInMemoryUpload(content.encode('utf-8'), mimetype='text/markdown')
        if res.get('files'):
            fid = res['files'][0]['id']
            self.service.files().update(fileId=fid, media_body=media).execute()
        else:
            meta = {'name': filename, 'parents': [self.folder_id],
                    'mimeType': 'text/markdown'}
            f = self.service.files().create(body=meta, media_body=media,
                                            fields='id').execute()
            fid = f['id']
        info = self.service.files().get(fileId=fid,
                                        fields='id,webViewLink').execute()
        return info['id'], info['webViewLink']

    def list_notes(self, subject: Optional[str] = None) -> list[dict]:
        q = f"'{self.folder_id}' in parents and trashed=false"
        if subject:
            q += f" and name contains '[{subject}]'"
        res = self.service.files().list(
            q=q,
            fields='files(id,name,webViewLink,createdTime)',
            orderBy='createdTime desc',
        ).execute()
        return res.get('files', [])

    def download_text(self, file_id: str) -> str:
        req = self.service.files().get_media(fileId=file_id)
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, req)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return buf.getvalue().decode('utf-8', errors='replace')


# ---------------- Calendar ----------------
class CalendarAPI:
    def __init__(self, creds: Credentials, tz: str = 'Europe/Samara'):
        self.service = build('calendar', 'v3', credentials=creds)
        self.tz = tz

    def add_event(self, summary: str, start: dt.datetime, end: dt.datetime,
                  description: str = '', drive_file_id: Optional[str] = None) -> dict:
        body = {
            'summary': summary,
            'description': description,
            'start': {'dateTime': start.isoformat(), 'timeZone': self.tz},
            'end':   {'dateTime': end.isoformat(),   'timeZone': self.tz},
        }
        if drive_file_id:
            body['extendedProperties'] = {'private': {DRIVE_FILE_PROP: drive_file_id}}
        return self.service.events().insert(calendarId='primary', body=body).execute()

    def list_upcoming(self, days: int = 7) -> list[dict]:
        now = dt.datetime.utcnow().isoformat() + 'Z'
        until = (dt.datetime.utcnow() + dt.timedelta(days=days)).isoformat() + 'Z'
        res = self.service.events().list(
            calendarId='primary', timeMin=now, timeMax=until,
            singleEvents=True, orderBy='startTime', maxResults=50,
        ).execute()
        return res.get('items', [])

    @staticmethod
    def get_drive_id(event: dict) -> Optional[str]:
        return (event.get('extendedProperties', {})
                     .get('private', {})
                     .get(DRIVE_FILE_PROP))
