import http.server
import socketserver
import webbrowser
import threading
import requests
import json
import time
import urllib.parse

# ==== CONFIG ====
with open('client_auth.json') as f:
    CLIENT_ID, CLIENT_SECRET = json.load(f)
REDIRECT_URI = "http://localhost:3000"
SCOPES = ["chat:read", "chat:edit"]

# ==== ENDPOINTS ====
AUTH_URL = "https://id.twitch.tv/oauth2/authorize"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"
TOKEN_FILE = "tokens.json"

auth_code_result = {}

class OAuthHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        if "code" in params:
            auth_code_result["code"] = params["code"][0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<h1>You may now close this window.</h1>")
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"<h1>Error: Missing code</h1>")

def start_server():
    with socketserver.TCPServer(("localhost", 3000), OAuthHandler) as httpd:
        httpd.timeout = 120  # max wait
        httpd.handle_request()  # one request only

def build_auth_url():
    scope_str = "+".join(SCOPES)
    return f"{AUTH_URL}?response_type=code&client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}&scope={scope_str}&state=xyz"

def exchange_code_for_tokens(code):
    params = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI
    }
    response = requests.post(TOKEN_URL, params=params)
    return response.json()

def save_tokens(data):
    expires_at = time.time() + data["expires_in"] - 60  # refresh 1 min early
    with open(TOKEN_FILE, "w") as f:
        json.dump({
            "access_token": data["access_token"],
            "refresh_token": data["refresh_token"],
            "expires_at": expires_at
        }, f)
    print("✅ Tokens saved to", TOKEN_FILE)

def main():
    print("🔐 Starting OAuth setup...")
    print("🌐 Opening browser to authorize...")

    threading.Thread(target=start_server, daemon=True).start()
    webbrowser.open(build_auth_url())

    # Wait for code to be captured
    for _ in range(120):
        if "code" in auth_code_result:
            break
        time.sleep(1)

    code = auth_code_result.get("code")
    if not code:
        print("❌ Authorization code not received. Aborting.")
        return

    print("🔁 Exchanging code for tokens...")
    tokens = exchange_code_for_tokens(code)

    if "access_token" in tokens:
        save_tokens(tokens)
        print("✅ Setup complete. You can now run your bot.")
    else:
        print("❌ Failed to get tokens:", tokens)

if __name__ == "__main__":
    main()
