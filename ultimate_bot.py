import asyncio
import re
import websockets
import json
import logging
import time
import uuid
import requests
import textwrap
from datetime import datetime, timedelta, timezone

from stream_uptime import get_channel_id, get_live_status_and_vod


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('twitch_events.log', mode='a'),
        # logging.StreamHandler()
    ]
)

# ========== CONFIG ==========

TOKEN_FILE = "tokens.json"

with open('client_auth.json') as f:
    CLIENT_ID, CLIENT_SECRET = json.load(f)

BOT_NICK = "joms_bot"
CHANNEL = "andersonjph"
# CHANNEL = "xire91"

# channel to follow events
USER_ID = get_channel_id(CHANNEL)

# ========== TOKEN HANDLING ==========

def split_message(message, max_length=500):
    return textwrap.wrap(
        message,
        width=max_length,
        break_long_words=True,
        break_on_hyphens=True,
        placeholder='-',
    )

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def format_timedelta(td: timedelta, link:bool = False) -> str:
    total_seconds = int(td.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if link:
        return f"{hours:02}h{minutes:02}m{seconds:02}s"
    else:
        return f"{hours}:{minutes:02}:{seconds:02}"

def refresh_twitch_token():
    url = "https://id.twitch.tv/oauth2/token"
    params = {
        "grant_type": "refresh_token",
        "refresh_token": get_refresh_token(),
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }
    response = requests.post(url, params=params)
    if response.status_code == 200:
        data = response.json()
        save_tokens(data["access_token"], data.get("refresh_token", get_refresh_token()), data["expires_in"])
        logging.info("🔄 Access token refreshed.")
    else:
        logging.error("❌ Failed to refresh token: %s", response.status_code)
        logging.error(response.text)

def get_access_token():
    try:
        with open(TOKEN_FILE, "r") as f:
            return json.load(f)["access_token"]
    except FileNotFoundError:
        logging.error("No tokens found. Please refresh manually.")
        return None

def get_refresh_token():
    with open(TOKEN_FILE, "r") as f:
        return json.load(f)["refresh_token"]

def save_tokens(access_token, refresh_token, expires_in):
    with open(TOKEN_FILE, "w") as f:
        json.dump({
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": time.time() + expires_in - 60
        }, f)

def is_token_expired():
    try:
        with open(TOKEN_FILE, "r") as f:
            return time.time() > json.load(f)["expires_at"]
    except FileNotFoundError:
        return True

# ========== CHAT BOT ==========
IRC_URL = "wss://irc-ws.chat.twitch.tv:443"


TMI_REGEX = re.compile(
    r'^(?:@(?P<tags>[^ ]+)\s+)?'
    r':(?:(?P<user>[^!]+)!)?.*?tmi\.twitch\.tv\s+'
    r'(?P<command>[A-Z]+)'
    r'(?:\s+(?P<channel>#[^ ]+))?'
    r'(?:\s+:(?P<message>.*))?$'
)

def parse_tmi_message(raw: str) -> dict | None:
    match = TMI_REGEX.match(raw)
    if not match:
        return None

    # parse tags into dict if present
    tags = {}
    tags_raw = match.group("tags")
    if tags_raw:
        for tag in tags_raw.split(";"):
            if "=" in tag:
                k, v = tag.split("=", 1)
                tags[k] = v
            else:
                tags[tag] = ""

    return {
        "tags": tags,
        "user": match.group("user"),
        "command": match.group("command"),
        "channel": match.group("channel"),
        "message": match.group("message"),
    }

class TwitchBot:
    def __init__(self):
        self.ws = None

        self.backoffs = [0, 1, 5, 10, 30, 60]
        self.backoff_idx = 0

    async def connect(self):
        while True:
            try:
                if is_token_expired():
                    logging.info("Token expired, refreshing...")
                    refresh_twitch_token()
                token = get_access_token()

                async with websockets.connect(IRC_URL) as ws:
                    self.ws = ws
                    await self.authenticate(token)
                    await self.listen()
            except Exception as e:
                logging.exception("Chat bot error: %s", e)


            delay = self.backoffs[self.backoff_idx]
            self.backoff_idx = min(self.backoff_idx + 1, len(self.backoffs) - 1)
            await asyncio.sleep(delay)

    async def authenticate(self, token):
        await self.ws.send(f"PASS oauth:{token}")
        await self.ws.send(f"NICK {BOT_NICK}")
        await self.ws.send(f"JOIN #{CHANNEL}")
        await self.ws.send("CAP REQ :twitch.tv/tags twitch.tv/commands twitch.tv/membership")
        logging.info("✅ Connected to chat")

    async def send_message(self, text):
        if self.ws:
            await self.ws.send(f"PRIVMSG #{CHANNEL} :{text}")
            logging.info("💬 Sent: %s", text)

    async def send_message(self, text):
        for msg in split_message(text):
            await self.ws.send(f"PRIVMSG #{CHANNEL} :{msg}")
            logging.info("💬 Fragment: %s", msg)
            await asyncio.sleep(1.2)
        logging.info("💬 Sent: %s", text)

    async def listen(self):
        async for messages in self.ws:
            if messages.startswith("PING"):
                await self.ws.send("PONG :tmi.twitch.tv")
                continue

            await self.parse_chat_msg(messages)
            self.backoff_idx = 0

    async def parse_chat_msg(self, messages):
        for message in messages.splitlines():
            parsed_msg = parse_tmi_message(message)
            match parsed_msg:
                case None:
                    if re.match(r'^:(?:joms_bot\.)?tmi\.twitch\.tv \d{3} joms_bot .*$', message):
                        logging.debug(f'known weird command: {message}')
                    elif message.startswith(':tmi.twitch.tv CAP * ACK'):
                        logging.debug(f'known weird command: {message}')
                    else:
                        logging.warning(f'could not parse TMI msg: {message}')

                case {'user': username, 'command': 'PRIVMSG', 'message': msg}:
                    # logging.info(f'{username}: {msg}')
                    if (m := re.search(r'joms?[ _]?bot', msg)):
                        logging.warning(f'CHAT MSG: {username} {msg}')

                case {'command': cmd} if cmd in ['JOIN', 'PART', 'USERSTATE', 'USERNOTICE', 'CLEARMSG']:
                    logging.debug(f'known command: {message}')
                case _:
                    logging.warning(f'uh oh parsing: {message}')

# https://www.twitch.tv/andersonjph/clip/AuspiciousSteamyDragonfruitBudStar-DFurcIQmTmh3ncQR

# ========== EVENT LISTENER ==========

TOPICS = [
    f'pinned-chat-updates-v1.{USER_ID}',      # msg pins
    f'polls.{USER_ID}',                       # polls
    f'raid.{USER_ID}',                        # raids
    f'video-playback-by-id.{USER_ID}',        # stream up/down
    f'predictions-channel-v1.{USER_ID}',      # bets
]

PUBSUBS_TO_IGNORE = (
    'community-goal-contribution',
    'community-goal-updated',
    'custom-reward-updated',
    'update-message',
    'unpin-message',
    'POLL_UPDATE',
    'POLL_ARCHIVE',
    'viewcount',
    'commercial',
)

HERMES_URL = "wss://hermes.twitch.tv/v1?clientId=kimne78kx3ncx6brgo4mv6wki5h1ko"

class HermesClient:
    def __init__(self, on_event):
        self.url = HERMES_URL
        self.on_event = on_event

        self.backoffs = [0, 1, 5, 10, 30, 60]
        self.backoff_idx = 0

        self.letest_raid_id = None
        self.allow_self_pins = True

    async def connect(self):
        headers = {
            'Origin': 'https://www.twitch.tv',
            'User-Agent': 'Mozilla/5.0'
        }
        while True:
            try:
                async with websockets.connect(self.url, additional_headers=headers) as ws:
                    await self.subscribe(ws)
                    self.url = HERMES_URL
                    self.backoff_idx = 0
                    await self.listen(ws)
            except Exception as e:
                logging.exception("Hermes error: %s", e)

            delay = self.backoffs[self.backoff_idx]
            self.backoff_idx = min(self.backoff_idx + 1, len(self.backoffs) - 1)
            logging.info("🔁 Reconnecting to Hermes in %d seconds...", delay)
            await asyncio.sleep(delay)

    async def subscribe(self, ws):
        for topic in TOPICS:
            msg = {
                'type': 'subscribe',
                'id': str(uuid.uuid4()),
                'subscribe': {
                    'id': str(uuid.uuid4()),
                    'type': 'pubsub',
                    'pubsub': {'topic': topic}
                },
                'timestamp': now_iso()
            }
            await ws.send(json.dumps(msg))
            logging.info("✅ Subscribed to: %s", topic)

    async def listen(self, ws):
        async for message in ws:
            try:
                data = json.loads(message)
                result = await self.handle_event(data)
                if result == 'reconnect':
                    return
            except Exception:
                logging.exception("Failed to parse event: %s", message)

    async def handle_event(self, data):
        match data:
            case {'type': 'welcome', 'welcome': {'recoveryUrl': recoveryUrl}}:
                logging.info(f'welcome: {data}')
            case {'type': 'reconnect', 'reconnect': {'url': reconnect_url}}:
                self.url = reconnect_url
                logging.info(f'reconnect order: {data}')
                return 'reconnect'
            case {'type': 'subscribeResponse'}:
                logging.info(f'subcribed: {data}')
            case {'type': 'keepalive'}:
                logging.debug(f'keepalive: {data}')
            case {'type': 'notification', 'notification': {'pubsub': pubsub_str}}:
                pubsub = json.loads(pubsub_str)
                await self.handle_pubsub(pubsub)
            case _:
                logging.warning(f'unknown msg: {data}')

    async def handle_pubsub(self, pubsub):
        match pubsub:
            case {'type': 'pin-message', 'data': {'message': {'sender':{'display_name':name}, 'content': {'text': text, 'fragments': fragments}}}}:
                logging.info(f'pin: {pubsub}')
                print(f'PINNED {name}: {text}')
                if self.allow_self_pins or name != 'joms_bot':
                    if any('link' in f for f in fragments):
                        # text = ''.join(('<link redacted>' if 'link' in f else f['text']) for f in fragments)
                        text = ''.join(('< jphPout bot can\'t post links>' if 'link' in f else f['text']) for f in fragments)
                        logging.warning(f'removing link from pin: {text}')
                    await self.on_event(f'MrDestructoid PIN {name}: {text}')
                else:
                    logging.info('skipping self pin')

            case {'type': 'POLL_CREATE', 'data': {'poll': {'title':title, 'choices': choices}}}:
                logging.info(f'poll start: {pubsub}')
                print(f'POLL: {title}')
                for c in choices:
                    print(f'  - {c['title']}')
                choices_str = ''.join(f'【{c["title"]}】' for c in choices)
                await self.on_event(f'MrDestructoid POLL {title}: {choices_str}')

            case {'type': poll_type, 'data': {'poll': {'title':title, 'choices': choices, 'total_voters': total_voters}}} if poll_type in ('POLL_COMPLETE', 'POLL_TERMINATE'):
                logging.info(f'{poll_type}: {pubsub}')
                print(f'{poll_type}: {title}')
                results = [(c['total_voters'] / total_voters * 100, c['title']) for c in choices]
                results = sorted(results, key=lambda x: x[0], reverse=True)
                for perc, choice in results:
                    print(f'  {perc:2.0f}% {choice}')

                result_str = ''.join(f'【{perc:2.0f}% {choice}】' for perc, choice in results)
                if poll_type == 'POLL_COMPLETE':
                    await self.on_event(f'MrDestructoid POLL RESULTS {title}: {result_str}')
                else:
                    await self.on_event(f'MrDestructoid POLL TERMINATED {title}: {result_str}')

            case {'type': 'event-created', 'data': {'event': {'outcomes': outcomes, 'title': title, 'status':'ACTIVE'}}}:
                logging.info(f'bet start: {pubsub}')
                print(f'BET: {title}')
                for o in outcomes:
                    print(f'  - {o['title']}')
                choices = ''.join(f'【{o["title"]}】' for o in outcomes)
                await self.on_event(f'MrDestructoid BET {title}: {choices}')

            case {'type': 'event-updated', 'data': {'event': {'outcomes': outcomes, 'title': title, 'status':'RESOLVED', 'winning_outcome_id': win_id}}}:
                logging.info(f'bet end: {pubsub}')
                win_outcome = [o for o in outcomes if o['id'] == win_id][0]
                total_points = sum(o['total_points'] for o in outcomes)
                odds = total_points / win_outcome['total_points']

                print(f'BET OUTCOME {title}: 【{win_outcome["title"]}】 {win_outcome['total_users']} weebs won {total_points:,d} monocoins, {odds:.2f}/1 odds')
                await self.on_event(f'MrDestructoid BET OUTCOME {title}: 【{win_outcome["title"]}】 {win_outcome['total_users']} weebs won {total_points:,d} monocoins, {odds:.2f}/1 odds')

            case {'type': 'event-updated', 'data': {'event': {'status':status}}} if status in ('ACTIVE', 'LOCKED', 'RESOLVE_PENDING',):
                # states while bet is in progress gets ignored
                logging.debug(f'bet ignored state {pubsub}')

            case {'type': 'raid_update_v2', 'raid': {'id': raid_id, 'target_login': target_login}}:
                logging.info(f'raiding: {pubsub}')
                await self.handle_raid_event(raid_id, target_login)

            case {'type': 'raid_go_v2'}:
                logging.info(f'raid go: {pubsub}')

            case {'type': 'stream-up'}:
                logging.info(f'stream up')
                print(f'stream up' + '='*80)

            case {'type': 'stream-down'}:
                logging.info(f'stream down')
                print(f'stream down' + '='*80)

            case {'type': pubsub_type} if pubsub_type in PUBSUBS_TO_IGNORE:
                logging.debug(f'ignoring pubsub {pubsub_type}')
            case _:
                logging.warning(f'unknown pubsub: {pubsub}')

    async def handle_raid_event(self, raid_id, target_login):
        if raid_id == self.letest_raid_id:
            logging.info(f'duplicate raid notif: {raid_id}')
            return

        # update the last seen raid id to avoid duplicates
        self.letest_raid_id = raid_id

        target_info = await asyncio.to_thread(get_live_status_and_vod, target_login)
        match target_info:
            case { 'type': 'error', 'error': err }:
                logging.error(f'error getting raid target info: {err}')

            case { 'type': 'offline' }:
                print(f'Raiding {target_login} while they are offline.')
                await self.on_event(f'MrDestructoid Raiding {target_login} while they are offline.')

            case { 'type': 'live', 'uptime': uptime, 'vod': None }:
                print(f'Raiding {target_login} (uptime: {format_timedelta(uptime)}), but no VOD info available.')
                await self.on_event(f'MrDestructoid Raiding {target_login} (uptime: {format_timedelta(uptime)}), but no VOD info available.')

            case { 'type': 'live', 'uptime': uptime, 'vod': { 'id': vod_id, 'url': vod_url } }:
                uptime_str = format_timedelta(uptime)
                uptime_link = format_timedelta(uptime, link=True)
                print(f'Raiding {target_login} (uptime: {uptime_str}). VOD: {vod_url}?t={uptime_link}')
                await self.on_event(f'MrDestructoid Raiding {target_login} (uptime: {uptime_str}) VOD: /videos/{vod_id}?t={uptime_link}')

# ========== MAIN ==========
async def start_bots():
    bot = TwitchBot()

    async def handle_event(event_message):
        await bot.send_message(event_message)

    hermes = HermesClient(on_event=handle_event)

    await asyncio.gather(
        bot.connect(),
        hermes.connect()
    )

def main():
    try:
        asyncio.run(start_bots())
    except KeyboardInterrupt as e:
        print(f'Interrupted by keyboard')

if __name__ == "__main__":
    main()
