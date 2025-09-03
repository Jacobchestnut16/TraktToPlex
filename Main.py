import datetime
import json
import sqlite3
import requests
import webbrowser
import time
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for

app = Flask(__name__)

DB_FILE = "trakt_plex.db"
TOKEN_FILE = "trakt_token.json"
API_KEYS = json.load(open("API_KEYS.json", "r"))
CLIENT_ID = API_KEYS["Trakt_Client_ID"]
CLIENT_SECRET = API_KEYS["Trakt_Client_Secret"]
TOKEN = json.load(open(TOKEN_FILE))


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

def db_conn():
    return sqlite3.connect(DB_FILE)


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

def getAllHistory(headers):
    """Fetch all Trakt history pages, not just recent watches."""
    page = 1
    per_page = 100
    conn = init_db()
    c = conn.cursor()

    while True:
        url = f"https://api.trakt.tv/sync/history/movies?page={page}&limit={per_page}"
        r = requests.get(url, headers=headers)
        r.raise_for_status()
        data = r.json()
        if not data:
            break

        for item in data:
            h_id = item["id"]
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

        page += 1

    conn.commit()
    conn.close()


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
        choice = input(f"Do you want to rate '{title}'? [y/N]: ").strip().lower()
        if choice == "y":
            print(f"Opening {url}")
            webbrowser.open(url)
            # TODO: Poll Trakt for rating update and update DB
        else:
            print(f"Skipped {title}")
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


# ---------- ROUTES ----------
@app.route("/")
def dashboard():
    return render_template("dashboard.html")


@app.route("/films")
def filmsInDB():
    conn = db_conn()
    c = conn.cursor()
    c.execute("SELECT slug, title, rating, watched_at FROM history ORDER BY watched_at DESC")
    films = c.fetchall()
    conn.close()
    return render_template("films.html", films=films)


@app.route("/rate", methods=["GET", "POST"])
def rate():
    conn = db_conn()
    c = conn.cursor()

    if request.method == "POST":
        ratings_to_submit = []
        for slug, rating in request.form.items():
            if rating.strip():  # only if user filled
                ratings_to_submit.append((slug, int(rating)))

        if ratings_to_submit:
            rated_at = datetime.datetime.utcnow().isoformat() + "Z"
            payload = {"movies": []}
            for slug, rating in ratings_to_submit:
                payload["movies"].append({
                    "ids": {"slug": slug},
                    "rating": rating,
                    "rated_at": rated_at
                })

            # push to Trakt
            r = requests.post("https://api.trakt.tv/sync/ratings",
                              headers=trakt_headers(CLIENT_ID, TOKEN["access_token"]), json=payload)
            r.raise_for_status()

            # update DB
            for slug, rating in ratings_to_submit:
                c.execute("UPDATE history SET rated=1, rating=? WHERE slug=?", (rating, slug))
                c.execute("DELETE FROM unrated WHERE slug=?", (slug,))
            conn.commit()

        conn.close()
        return redirect(url_for("rate"))

    # GET mode: fetch unrated films
    page = int(request.args.get("page", 1))
    limit = int(request.args.get("limit", 10))  # allow ?limit=20 etc
    offset = (page - 1) * limit

    c.execute("SELECT slug, title FROM unrated ORDER BY rowid DESC LIMIT ? OFFSET ?", (limit, offset))
    films = c.fetchall()

    c.execute("SELECT COUNT(*) FROM unrated")
    total = c.fetchone()[0]
    conn.close()

    total_pages = (total + limit - 1) // limit

    return render_template(
        "rate.html",
        films=films,
        page=page,
        total=total,
        limit=limit,
        total_pages=total_pages
    )



# ---------- STUBS FOR SYNC TASKS ----------
@app.route("/sync/<mode>")
def sync(mode):
    headers = trakt_headers(CLIENT_ID, TOKEN["access_token"])
    if mode == "recent":
        getHistory(headers)
    elif mode == "full":
        getAllHistory(headers)
    elif mode == "ratings":
        getHistoryRating(headers)
    return redirect(url_for("dashboard"))

if __name__ == "__main__":
    app.run(debug=True)