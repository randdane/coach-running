CREATE TABLE activities (
    id            INTEGER PRIMARY KEY,
    athlete_id    TEXT    NOT NULL,
    start_date    TEXT    NOT NULL,
    name          TEXT    NOT NULL,
    type          TEXT    NOT NULL,
    distance_km   REAL,
    duration_min  INTEGER,
    avg_hr        INTEGER,
    raw_json      TEXT    NOT NULL,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_activities_athlete_date ON activities(athlete_id, start_date DESC);

CREATE TABLE messages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT    NOT NULL CHECK (kind IN ('morning','post_run')),
    trigger       TEXT    NOT NULL CHECK (trigger IN ('scheduled','webhook','poll','manual')),
    activity_id   INTEGER,
    model         TEXT    NOT NULL,
    prompt        TEXT    NOT NULL,
    response      TEXT    NOT NULL,
    tool_calls    TEXT,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_messages_created ON messages(created_at DESC);
CREATE INDEX idx_messages_activity ON messages(activity_id);

CREATE TABLE strava_tokens (
    athlete_id     TEXT PRIMARY KEY,
    access_token   TEXT NOT NULL,
    refresh_token  TEXT NOT NULL,
    expires_at     INTEGER NOT NULL
);
