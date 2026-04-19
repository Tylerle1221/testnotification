"""
Setup script - auto-detects your Telegram chat_id and saves it to config.json.
Run this ONCE after sending any message to your Telegram bot.
"""
import json, urllib.request, sys, time
from pathlib import Path

CONFIG = Path(__file__).parent / "config.json"

def load_config():
    with open(CONFIG) as f:
        return json.load(f)

def save_config(cfg):
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)

def get_updates(token):
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    resp = urllib.request.urlopen(url, timeout=10)
    return json.loads(resp.read())

def main():
    cfg = load_config()
    token = cfg["telegram"]["bot_token"]
    username = cfg["telegram"].get("username", "")

    print(f"\n=== Telegram Chat ID Setup ===")
    print(f"Bot token: {token[:20]}...")
    print(f"Target user: @{username}")
    print()

    if cfg["telegram"].get("chat_id"):
        print(f"Chat ID already configured: {cfg['telegram']['chat_id']}")
        ans = input("Re-detect? (y/N): ").strip().lower()
        if ans != "y":
            print("Skipping. Exiting.")
            return

    print("Please send any message to your bot in Telegram now.")
    print("Waiting up to 60 seconds...")
    print()

    for i in range(12):
        try:
            data = get_updates(token)
            if data.get("ok") and data["result"]:
                for update in data["result"]:
                    msg = update.get("message") or update.get("channel_post")
                    if not msg:
                        continue
                    chat = msg["chat"]
                    from_user = msg.get("from", {})
                    uname = from_user.get("username", "")
                    cid = str(chat["id"])

                    print(f"Found message from: @{uname} (chat_id={cid})")

                    if not username or uname.lower() == username.lower():
                        cfg["telegram"]["chat_id"] = cid
                        save_config(cfg)
                        print(f"\n✓ chat_id saved: {cid}")
                        print("You can now run: python main.py")
                        return
        except Exception as e:
            print(f"API error: {e}")

        sys.stdout.write(f"\r  Attempt {i+1}/12...")
        sys.stdout.flush()
        time.sleep(5)

    print("\n\nTimed out. Make sure you sent a message to the bot, then run this script again.")

if __name__ == "__main__":
    main()