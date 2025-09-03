import json
import sqlite3
import requests
import webbrowser
import time
from pathlib import Path

DB_FILE = "trakt_plex.db"
TOKEN_FILE = "trakt_token.json"


# ---------- DB SETUP ----------
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS history (
        history_id INTEGER PRIMARY KEY,     -- Trakt history event id
        slug TEXT NOT NULL,                 -- trakt movie slug
        imdb_id TEXT,
        tmdb_id INTEGER,
        title TEXT,
        year INTEGER,
        watched_at TEXT,
        rated INTEGER DEFAULT 0,
        rating INTEGER,
        in_plex_history INTEGER DEFAULT 0,
        in_plex_rating INTEGER DEFAULT 0
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS unrated (
        slug TEXT PRIMARY KEY,
        title TEXT
    )""")
    conn.commit()
    return conn


# ---------- OAUTH HELPERS ----------
def get_trakt_token(client_id, client_secret):
    """Run Trakt device flow and save token to file."""
    r = requests.post("https://api.trakt.tv/oauth/device/code",
                      json={"client_id": client_id})
    r.raise_for_status()
    device = r.json()
    print(f"Go to {device['verification_url']} and enter code: {device['user_code']}")

    while True:
        time.sleep(device["interval"])
        r = requests.post("https://api.trakt.tv/oauth/device/token", json={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": device["device_code"]
        })
        if r.status_code == 200:
            token = r.json()
            with open(TOKEN_FILE, "w") as f:
                json.dump(token, f, indent=2)
            print("Trakt authorization successful")
            return token
        elif r.status_code == 400:
            # still pending
            continue
        elif r.status_code in (403, 404):
            raise RuntimeError("Device code expired or invalid, restart flow")
        else:
            r.raise_for_status()


def load_trakt_token(client_id, client_secret):
    """Load access token from file or start new auth flow."""
    if Path(TOKEN_FILE).exists():
        with open(TOKEN_FILE, "r") as f:
            return json.load(f)
    return get_trakt_token(client_id, client_secret)


def trakt_headers(client_id, access_token):
    return {
        "Content-Type": "application/json",
        "trakt-api-version": "2",
        "trakt-api-key": client_id,
        "Authorization": f"Bearer {access_token}"
    }


# ---------- SYNC FUNCTIONS ----------
def getHistory(headers):
    url = "https://api.trakt.tv/sync/history/movies"
    r = requests.get(url, headers=headers)
    r.raise_for_status()
    data = r.json()

    conn = init_db()
    c = conn.cursor()
    for item in data:
        h_id = item["id"]  # history event id
        m = item["movie"]
        ids = m["ids"]

        c.execute("""
            INSERT OR IGNORE INTO history (
                history_id, slug, imdb_id, tmdb_id,
                title, year, watched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            h_id,
            ids["slug"],
            ids.get("imdb"),
            ids.get("tmdb"),
            m["title"],
            m["year"],
            item["watched_at"]
        ))
    conn.commit()
    conn.close()


def getHistoryRating(headers):
    url = "https://api.trakt.tv/sync/ratings/movies"
    r = requests.get(url, headers=headers)
    r.raise_for_status()
    ratings = r.json()

    conn = init_db()
    c = conn.cursor()
    for r_item in ratings:
        slug = r_item["movie"]["ids"]["slug"]
        rating = r_item["rating"]
        c.execute("UPDATE history SET rated=1, rating=? WHERE slug=?", (rating, slug))

    c.execute("SELECT slug,title FROM history WHERE rated=0")
    for slug, title in c.fetchall():
        c.execute("INSERT OR IGNORE INTO unrated (slug, title) VALUES (?, ?)", (slug, title))
    conn.commit()
    conn.close()


def rateUnratedFilms():
    conn = init_db()
    c = conn.cursor()
    c.execute("SELECT slug,title FROM unrated")
    films = c.fetchall()
    for slug, title in films:
        url = f"https://trakt.tv/movies/{slug}"
        print(f"Open for rating: {title} ({url})")
        webbrowser.open(url)
        # TODO: Poll Trakt for rating update and update DB
    conn.close()



def setPlexWatchHistory():
    # check if the film is not entered into the plex history
    ## if not inPlexHistory
    # open a headless browser to the film on plex then press watched
    # ensure to update the table that it was entered into plex history
    pass

def setPlexWatchRating():
    """
    Similar to watch history
    """
    pass

def setPlexWatchHistoryAndRating():
    """
    This one does both setPlexWatchHistory() and setPlexWatchRating()
    but at the same time this way there is less browser requests to the same page
    ONLY WHEN NEITHER ARE FILLED IN AND BOTH ARE PRESENT IN THE DB
    """
    pass

if __name__ == "__main__":
    keys = json.load(open("API_KEYS.json", "r"))
    client_id = keys["Trakt_Client_ID"]
    client_secret = keys["Trakt_Client_Secret"]

    token = load_trakt_token(client_id, client_secret)
    headers = trakt_headers(client_id, token["access_token"])

    getHistory(headers)
    getHistoryRating(headers)
    rateUnratedFilms()