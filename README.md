# Trakt ↔ Plex Sync Dashboard

This project provides a simple **Flask web dashboard** for syncing and managing your **Trakt.tv watch history and ratings**.  
It uses a local SQLite database to track watched movies, ratings, and unrated items, and provides a browser interface for reviewing and rating films.

---

## Features

- **OAuth Device Flow**: Authenticate with your Trakt account.  
- **Sync Modes**:  
  - Recent watch history  
  - Full watch history  
  - Ratings sync  
- **Unrated Queue**: See unrated films and quickly rate them.  
- **Film Dashboard**: View all films in your database, including ratings and watch dates.  
- **Future Stubs**: Placeholder functions for syncing ratings/history to Plex.

---

## Requirements

- Python 3.9+  
- A [Trakt.tv](https://trakt.tv) account  
- Trakt API credentials ([Get them here](https://trakt.tv/oauth/applications))  

---

## Installation

1. Clone or download this repository.  

2. Install dependencies:
   ```bash
   pip install flask requests
3. Copy the example API keys file and fill in your Trakt credentials:
  
    ```cp example-API_KEYS.json API_KEYS.json```
    Edit `API_KEYS.json` and replace with your own:
    ```
    {
      "Trakt_Client_ID": "YOUR_CLIENT_ID",
      "Trakt_Client_Secret": "YOUR_CLIENT_SECRET"
    }
    ```
4. Run the app:
   ```python Main.py```

## Usage

1. Open the dashboard in your browser:
   `http://127.0.0.1:5000`

2. Pages:

- **Dashboard** (`/`)  
  Quick links to all actions:
  - Sync Recent History
  - Sync Full History
  - Sync Ratings
  - View Films in DB
  - Rate Films

- **Films in DB** (`/films`)  
  Shows all movies stored in the database, with columns:
  - Title (clickable link to Trakt)
  - Rating
  - Watched At
  - Sorted by most recent watch

- **Rate Films** (`/rate`)  
  Paginated list of unrated movies with a rating form:
  - Shows up to `limit` (default 10) per page
  - Users can input ratings (1–10) and submit
  - Ratings are sent to Trakt via API
  - Navigation to move between pages
  - Reference table for rating meanings stays visible

- **Sync endpoints**:
  - `/sync/recent` → Sync most recent Trakt history  
  - `/sync/full` → Sync full Trakt history  
  - `/sync/ratings` → Sync Trakt ratings  

---

## Database Schema

SQLite database: `trakt_plex.db`

**history**
| Column          | Type    | Notes                             |
|-----------------|---------|-----------------------------------|
| history_id      | INTEGER | Trakt history event ID (PK)       |
| slug            | TEXT    | Trakt movie slug                  |
| imdb_id         | TEXT    | IMDb ID                           |
| tmdb_id         | INTEGER | TMDB ID                           |
| title           | TEXT    | Movie title                       |
| year            | INTEGER | Release year                      |
| watched_at      | TEXT    | ISO datetime string               |
| rated           | INTEGER | 0 = not rated, 1 = rated          |
| rating          | INTEGER | Rating value (1–10)               |
| in_plex_history | INTEGER | 0 = not synced to Plex            |
| in_plex_rating  | INTEGER | 0 = not synced to Plex rating     |

**unrated**
| Column | Type | Notes |
|--------|------|-------|
| slug   | TEXT | Trakt movie slug (PK) |
| title  | TEXT | Movie title |

---

## Screenshots

> Placeholders for screenshots – add real images after running the app.

### Dashboard
![Dashboard Placeholder](screenshots/dashboard.png)

### Films in DB
![Films Placeholder](screenshots/films.png)

### Rate Films
![Rate Placeholder](screenshots/rate.png)

---

## Notes

- Plex sync functions (`setPlexWatchHistory`, `setPlexWatchRating`) are stubs.  
- Trakt token (`trakt_token.json`) is created automatically after first authentication.  
- Templates should be in `templates/` directory:  
- `dashboard.html`  
- `films.html`  
- `rate.html`  
- Pagination in `/rate` and `/films` allows dynamic page limits using `?limit=10` (default 10).  
- Ratings reference table in `/rate` stays sticky as you scroll.  
