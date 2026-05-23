import os
import json
from flask import Flask, request, session
from google_auth_oauthlib.flow import Flow

from google_services import SCOPES
from db import Database

REDIRECT_URI = os.environ['OAUTH_REDIRECT_URI']
PUBLIC_BASE = REDIRECT_URI.rsplit('/', 1)[0]

_db = Database()
app = Flask(__name__)

app.secret_key = '1234'

os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'


def _flow() -> Flow:
    return Flow.from_client_secrets_file('credentials.json', scopes=SCOPES, redirect_uri=REDIRECT_URI)


def auth_url_for(vk_id: int) -> str:
    return f"{PUBLIC_BASE}/start_auth?vk_id={vk_id}"


@app.route('/start_auth')
def start_auth():
    vk_id = request.args.get('vk_id', '')
    if not vk_id.isdigit():
        return 'bad vk_id', 400

    flow = _flow()  # Сохраняем в переменную, а не вызываем функцию дважды
    url, _ = flow.authorization_url(
        access_type='offline', prompt='consent', state=vk_id,
        include_granted_scopes='true'
    )

    # print(f"(внутри неё зашит redirect_uri): {url}")
    # 3. СОХРАНЯЕМ ВЕРИФИКАТОР В СЕССИЮ БРАУЗЕРА
    session['code_verifier'] = flow.code_verifier

    return f'<a href="{url}">Войти через Google</a><script>location="{url}"</script>'


@app.route('/oauth2callback')
def callback():
    # print(f" ФАКТИЧЕСКИЙ URL КОЛБЭКА ОТ БРАУЗЕРА: {request.url}")
    state = request.args.get('state', '')
    if not state.isdigit():
        return 'bad state', 400
    if request.args.get('iss') != "https://accounts.google.com": return 'bad state', 400
    if request.args.get('scope') != "https://www.googleapis.com/auth/drive.file https://www.googleapis.com/auth/calendar": return 'bad state', 400
    flow = _flow()

    # 4. ВОССТАНАВЛИВАЕМ ВЕРИФИКАТОР ПЕРЕД СТАРТОМ fetch_token
    if 'code_verifier' in session:
        flow.code_verifier = session['code_verifier']
    else:
        return 'Ошибка: Сессия устарела или верификатор кода не найден.', 400

    flow.fetch_token(authorization_response=request.url)
    _db.set_creds(int(state), flow.credentials.to_json())

    # 5. ОЧИЩАЕМ СЕССИЮ
    session.pop('code_verifier', None)

    return '<script>window.close();</script>', 200

@app.route('/')
def home():
    return "Server is running!"
@app.route('/mike/')
def mike_endpoint():
    return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Hello World</title>
</head>
<body>

    <!-- This script displays an alert box -->
    <script>
        alert("Hello, World!");
    </script>

</body>
</html>"""

def run():
    app.run(host='0.0.0.0', port=7042, debug=False, use_reloader=False)
    # app.run(host='0.0.0.0', port=7042, debug=False, use_reloader=False, ssl_context='adhoc')
