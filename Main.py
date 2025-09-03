import datetime, time, json, sqlite3
import os
import platform
import shutil

import requests, webbrowser, urllib.parse
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains

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

# ---------Plex-------------

from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.firefox.service import Service as FirefoxService
from selenium.webdriver.edge.service import Service as EdgeService
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.safari.webdriver import WebDriver as SafariDriver

def get_default_profile(browser):
    system = platform.system().lower()
    home = os.path.expanduser("~")

    if browser == "chrome":
        if system == "windows":
            return os.path.join(os.environ["LOCALAPPDATA"], "Google/Chrome/User Data")
        elif system == "darwin":  # macOS
            return os.path.join(home, "Library/Application Support/Google/Chrome")
        else:  # Linux
            return os.path.join(home, ".config/google-chrome")

    if browser == "brave":
        if system == "windows":
            return os.path.join(os.environ["LOCALAPPDATA"], "BraveSoftware/Brave-Browser/User Data")
        elif system == "darwin":
            return os.path.join(home, "Library/Application Support/BraveSoftware/Brave-Browser")
        else:
            return os.path.join(home, ".config/BraveSoftware/Brave-Browser")

    if browser == "edge":
        if system == "windows":
            return os.path.join(os.environ["LOCALAPPDATA"], "Microsoft/Edge/User Data")
        elif system == "darwin":
            return os.path.join(home, "Library/Application Support/Microsoft Edge")
        else:
            return os.path.join(home, ".config/microsoft-edge")

    if browser == "firefox":
        if system == "windows":
            return os.path.join(os.environ["APPDATA"], "Mozilla/Firefox/Profiles")
        elif system == "darwin":
            return os.path.join(home, "Library/Application Support/Firefox/Profiles")
        else:
            return os.path.join(home, ".mozilla/firefox")

    return None

def get_driver_auto():
    system = platform.system().lower()

    # Try detecting default browser name
    try:
        default_browser = webbrowser.get().name.lower()
    except:
        default_browser = ""

    candidates = [
        ("chrome", "chromedriver", ChromeService, ChromeOptions),
        ("brave", "chromedriver", ChromeService, ChromeOptions),
        ("firefox", "geckodriver", FirefoxService, FirefoxOptions),
        ("edge", "msedgedriver", EdgeService, EdgeOptions),
    ]

    # if system == "darwin":
    #     candidates.append(("safari", None, None, None))

    for browser_name, driver_cmd, service_cls, options_cls in candidates:
        if browser_name in default_browser:
            if browser_name == "safari":
                print("Launching Safari (default browser)")
                return SafariDriver()
            elif driver_cmd and shutil.which(driver_cmd):
                print(f"Launching {browser_name} (default browser)")
                options = options_cls()

                # Try to attach to existing user profile
                profile_path = get_default_profile(browser_name)
                if profile_path and os.path.exists(profile_path):
                    options.add_argument(f"user-data-dir={profile_path}")
                    print(f"Using profile: {profile_path}")

                if browser_name in ("chrome", "brave"):
                    return webdriver.Chrome(service=service_cls(), options=options)
                elif browser_name == "firefox":
                    return webdriver.Firefox(service=service_cls(), options=options)
                elif browser_name == "edge":
                    return webdriver.Edge(service=service_cls(), options=options)

    # Fallback loop
    for browser_name, driver_cmd, service_cls, options_cls in candidates:
        if browser_name == "safari" and system == "darwin":
            print("Launching Safari (fallback)")
            return SafariDriver()
        elif driver_cmd and shutil.which(driver_cmd):
            print(f"Launching {browser_name} (fallback)")
            options = options_cls()

            profile_path = get_default_profile(browser_name)
            if profile_path and os.path.exists(profile_path):
                options.add_argument(f"user-data-dir={profile_path}")
                print(f"Using profile: {profile_path}")

            if browser_name in ("chrome", "brave"):
                return webdriver.Chrome(service=service_cls(), options=options)
            elif browser_name == "firefox":
                return webdriver.Firefox(service=service_cls(), options=options)
            elif browser_name == "edge":
                return webdriver.Edge(service=service_cls(), options=options)

    raise RuntimeError("No supported browser/driver found.")



# def get_driver():
#     profile_path = os.path.join(os.getcwd(), "chrome_profile")
#
#     chrome_options = Options()
#     chrome_options.add_argument(f"--user-data-dir={profile_path}")
#     chrome_options.add_argument("--profile-directory=Default")
#
#     return webdriver.Chrome(options=chrome_options)

def ensureSignIn():
    driver = get_driver_auto()
    url = "https://app.plex.tv/desktop/#!"
    driver.get(url)

    wait = WebDriverWait(driver, 15)

    try:
        # Look for the profile/avatar icon (appears only if logged in)
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, '[data-testid="user-menu-button"]')))
        print("Already signed in to Plex")
        return driver

    except:
        print("Not signed in. Please log in manually in the opened browser...")

        # Wait until the login button disappears and profile is visible
        WebDriverWait(driver, 300).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, '[data-testid="user-menu-button"]'))
        )
        print("Sign in detected, session saved for future runs")
        return driver

def setPlexWatchHistory(driver):
    conn = db_conn()
    c = conn.cursor()

    # get all unrated films that are not in Plex history
    c.execute("SELECT title, year FROM history WHERE in_plex_history=0")
    films = c.fetchall()

    wait = WebDriverWait(driver, 15)

    for title, year in films:
        query = f"{title} {year}"
        encoded_query = urllib.parse.quote(query)
        url = f"https://app.plex.tv/desktop/#!/search?query={encoded_query}"
        print(f"Searching Plex for: {title} ({year})")

        driver.get(url)

        try:
            # wait for the search results to load
            first_result = wait.until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "div.SearchResultListRow-container-eOnSD1 a")
                )
            )

            # click the first result link
            first_result.click()
            print(f"Clicked first result for {title} ({year})")

            # locate "Mark Watched" and click if needed
            try:
                watch_button = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, 'button[data-testid="preplay-togglePlayedState"]'))
                )
                watch_button.click()
                print("Marked as Watched")

                # update DB entry when successful
                c.execute(
                    "UPDATE history SET in_plex_history=1 WHERE title=? AND year=?",
                    (title, year),
                )
                conn.commit()

            except Exception as e:
                print("Could not mark as watched:", e)
        except Exception as e:
            print(f"No results found for {title} ({year}): {e}")
        break

    conn.commit()
    conn.close()

def set_rating(driver, rating):
    """
    Set Plex rating on the 0-10 scale.
    Example: 7 = 3.5 stars, 10 = 5 stars.
    """
    # Find the slider element
    slider = driver.find_element(By.CSS_SELECTOR, '[role="slider"]')

    # Get its size and location
    slider_container = slider.find_element(By.XPATH, "..")  # parent span
    width = slider_container.size['width']

    # Clamp rating between 0–10
    rating = max(0, min(10, rating))

    # Calculate target x offset
    # Each step is width/10
    step = width / 10
    target_offset = int(rating * step)

    # Move and click
    actions = ActionChains(driver)
    actions.click_and_hold(slider).move_by_offset(target_offset - (width // 2), 0).release().perform()
    
    
def setPlexWatchRating(driver):
    conn = db_conn()
    c = conn.cursor()

    c.execute("SELECT title, year, rating FROM history WHERE in_plex_rating=0 AND rated=1")
    films = c.fetchall()

    wait = WebDriverWait(driver, 15)

    for title, year, rating in films:
        query = f"{title} {year}"
        encoded_query = urllib.parse.quote(query)
        url = f"https://app.plex.tv/desktop/#!/search?query={encoded_query}"
        print(f"Searching Plex for: {title} ({year})")

        driver.get(url)

        try:
            first_result = wait.until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "div.SearchResultListRow-container-eOnSD1 a")
                )
            )
            first_result.click()
            print(f"Clicked first result for {title} ({year})")

            try:
                set_rating(driver, rating)
                print(" Rating set")

                # update DB entry
                c.execute(
                    "UPDATE history SET in_plex_rating=1 WHERE title=? AND year=?",
                    (title, year),
                )
                conn.commit()

            except Exception as e:
                print(" Could not set rating:", e)

        except Exception as e:
            print(f"No results found for {title} ({year}): {e}")
        break

    conn.commit()
    conn.close()


def setPlexWatchHistoryAndRating(driver):
    conn = db_conn()
    c = conn.cursor()

    c.execute("SELECT title, year, rating FROM history WHERE in_plex_history=0 OR in_plex_rating=0")
    films = c.fetchall()

    wait = WebDriverWait(driver, 15)

    for title, year, rating in films:
        query = f"{title} {year}"
        encoded_query = urllib.parse.quote(query)
        url = f"https://app.plex.tv/desktop/#!/search?query={encoded_query}"
        print(f"Searching Plex for: {title} ({year})")

        driver.get(url)

        try:
            first_result = wait.until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "div.SearchResultListRow-container-eOnSD1 a")
                )
            )
            first_result.click()
            print(f"Clicked first result for {title} ({year})")

            # Handle history
            try:
                watch_button = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, 'button[data-testid="preplay-togglePlayedState"]'))
                )
                watch_button.click()
                print(" Marked as Watched")
                c.execute(
                    "UPDATE history SET in_plex_history=1 WHERE title=? AND year=?",
                    (title, year),
                )
                conn.commit()
            except Exception as e:
                print(" Could not mark as watched:", e)

            # Handle rating
            try:
                set_rating(driver, rating)
                print(" Rating set")
                c.execute(
                    "UPDATE history SET in_plex_rating=1 WHERE title=? AND year=?",
                    (title, year),
                )
                conn.commit()
            except Exception as e:
                print(" Could not set rating:", e)

        except Exception as e:
            print(f"No results found for {title} ({year}): {e}")

    conn.commit()
    conn.close()

# ---------- ROUTES ----------
@app.route("/")
def dashboard():
    return render_template("dashboard.html")


@app.route("/films")
def filmsInDB():
    conn = db_conn()
    c = conn.cursor()
    c.execute("SELECT slug, title, rating, watched_at, year FROM history ORDER BY watched_at DESC")
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

@app.route("/push/<mode>")
def push(mode):
    driver = ensureSignIn()
    if mode == "history":
        setPlexWatchHistory(driver)
    elif mode == "ratings":
        setPlexWatchRating(driver)
    elif mode == "all":
        setPlexWatchHistoryAndRating(driver)
    return redirect(url_for("dashboard"))

if __name__ == "__main__":
    app.run(debug=True)