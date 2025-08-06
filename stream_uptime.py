import requests
import logging
from datetime import datetime, timezone

# Setup logging
logger = logging.getLogger(__name__)

def get_live_status_and_vod(channel_name: str):
    url = "https://gql.twitch.tv/gql"
    headers = {
        "Client-ID": "kimne78kx3ncx6brgo4mv6wki5h1ko",
        "Content-Type": "application/json"
    }

    query = """
    query ChannelLiveAndVideos($login: String!) {
      user(login: $login) {
        stream {
          createdAt
        }
        videos(first: 1, sort: TIME, type: ARCHIVE) {
          edges {
            node {
              id
              createdAt
              status
              title
            }
          }
        }
      }
    }
    """

    payload = {
        "operationName": "ChannelLiveAndVideos",
        "variables": {"login": channel_name},
        "query": query
    }

    logger.info(f"Querying Twitch for channel '{channel_name}'...")

    try:
        response = requests.post(url, headers=headers, json=payload)
        if response.status_code != 200:
            logger.error(f"HTTP error: {response.status_code}")
            return {
                "type": "error",
                "error": f"HTTP error {response.status_code}"
            }

        data = response.json()
        user = data.get("data", {}).get("user")
        if user is None:
            logger.warning(f"Channel '{channel_name}' not found.")
            return {
                "type": "error",
                "error": f"Channel '{channel_name}' not found"
            }

        stream = user.get("stream")
        if stream is None:
            logger.info(f"Channel '{channel_name}' is offline.")
            return {
                "type": "offline"
            }

        # Channel is live
        created_at = stream["createdAt"]
        start_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        uptime = now - start_dt
        logger.info(f"Channel '{channel_name}' is live. Uptime: {uptime}.")

        # Try to get current VOD info
        vod_info = None
        videos = user.get("videos", {}).get("edges", [])
        if videos:
            node = videos[0].get("node", {})
            vod_id = node.get("id")
            if vod_id:
                vod_info = {
                    "id": vod_id,
                    "url": f"https://twitch.tv/videos/{vod_id}"
                }
                logger.info(f"VOD ID found: {vod_id}")
            else:
                logger.info("No VOD ID found despite stream being live.")

        return {
            "type": "live",
            "uptime": uptime,
            "vod": vod_info  # may be None
        }

    except Exception as e:
        logger.exception("Exception during Twitch API call:")
        return {
            "type": "error",
            "error": str(e)
        }

def get_channel_id(username):
    url = "https://gql.twitch.tv/gql"
    headers = {
        "Client-ID": "kimne78kx3ncx6brgo4mv6wki5h1ko",
        "Content-Type": "application/json"
    }
    payload = [{
        "operationName": "UserByLogin",
        "variables": {
            "login": username
        },
        "query": """
        query UserByLogin($login: String!) {
            user(login: $login) {
                id
                login
                displayName
            }
        }
        """
    }]

    response = requests.post(url, headers=headers, json=payload)
    result = response.json()

    match result:
        case [{"data": {"user": {"id": user_id}}}]:
            return user_id
        case [{"data": {"user": None}}]:
            logger.error("User not found.")
        case _:
            logger.error("Unexpected response format:", result)
    return None

def test():
    print(repr(get_channel_id("joms_bot")))

    match get_live_status_and_vod("andersonjph"):
        case { "type": "error", "error": err }:
            print(f"Error: {err}")
        case { "type": "offline" }:
            print("Channel is offline.")
        case { "type": "live", "uptime": uptime, "vod": None }:
            print(f"Channel is live (uptime: {uptime}), but no VOD available.")
        case { "type": "live", "uptime": uptime, "vod": { "id": vod_id, "url": vod_url } }:
            print(f"Channel is live (uptime: {uptime}). VOD: {vod_url}")

if __name__ == "__main__":
    test()
