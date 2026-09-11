# My Cloud Storage

A private, personal cloud file-storage web app built with **Streamlit** and **Backblaze B2**.
Access your files from any browser — desktop, tablet, or mobile — while the actual file
contents live in your own private B2 bucket, never on the Streamlit server itself.

---

## 1. What this application does

- Password-protected web interface for browsing, uploading, downloading, and organizing files.
- Files are stored in **Backblaze B2** (S3-compatible API), not on the Streamlit host.
- A local **SQLite** database stores only metadata (name, size, folder, MIME type, dates) —
  never file contents — so the file browser and search stay fast without listing the whole
  bucket on every click.
- Supports **any file type** (documents, images, video, audio, archives, executables,
  installers, source code, etc.) up to a configurable size limit. There is no extension
  whitelist.
- Folder creation and nested navigation, breadcrumb trail, global search, storage-usage
  dashboard, file rename, and a B2-to-SQLite recovery sync.

---

## 2. Architecture

```
Browser
  │
  ▼
Streamlit Application  (app.py, auth.py)
  │
  ▼
Python backend
  ├── database.py   → SQLite metadata (storage.db)
  └── b2_storage.py → boto3 S3-compatible client
  │
  ▼
Backblaze B2 (S3-compatible API)
  │
  ▼
Private B2 Bucket  (actual file contents)
```

- **Backblaze B2** stores every uploaded file — PDFs, images, video, archives, everything.
- **SQLite** stores only metadata: file name, B2 object key, folder, size, MIME type,
  extension, and timestamps. It never stores file bytes.
- Folders are **not** real B2 "directories" — B2 (like S3) has no native folder concept.
  Folders are represented as `/`-separated prefixes in the object key
  (e.g. `Documents/Work/report.pdf`), and SQLite keeps a `folders` table purely to make
  browsing and creating empty folders convenient.

---

## 3. Requirements

- Python 3.9+
- A Backblaze B2 account with a **private** bucket
- A B2 **Application Key** (not your master key) scoped to that bucket

---

## 4. Install Python dependencies

```bash
pip install -r requirements.txt
```

---

## 5. Configure Backblaze B2

### 5.1 Create a private bucket

1. Log in to the [Backblaze B2 console](https://secure.backblaze.com/).
2. Go to **Buckets → Create a Bucket**.
3. Choose a unique bucket name.
4. Set **Files in Bucket** to **Private**.
5. Note the bucket's **Endpoint** shown on the bucket details page — it looks like
   `https://s3.<region>.backblazeb2.com` (e.g. `https://s3.us-west-004.backblazeb2.com`).

### 5.2 Create a B2 Application Key

1. Go to **App Keys → Add a New Application Key**.
2. Restrict it to the bucket you just created (not "All buckets" / master key) if possible.
3. Grant **Read and Write** capabilities.
4. Copy the **keyID** and **applicationKey** immediately — the application key is only
   shown once.

You now have everything needed for the five required secrets:
`B2_ENDPOINT`, `B2_BUCKET_NAME`, `B2_KEY_ID`, `B2_APPLICATION_KEY`, plus your own
`APP_PASSWORD`.

---

## 6. Configure Streamlit secrets

Copy the example file and fill in real values:

```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
```

Edit `.streamlit/secrets.toml`:

```toml
APP_PASSWORD = "choose-a-strong-password"

B2_ENDPOINT = "https://s3.YOUR_REGION.backblazeb2.com"
B2_BUCKET_NAME = "YOUR_BUCKET"
B2_KEY_ID = "YOUR_KEY_ID"
B2_APPLICATION_KEY = "YOUR_APPLICATION_KEY"

MAX_FILE_SIZE_MB = 200
FREE_STORAGE_LIMIT_GB = 10
```

**Never commit `.streamlit/secrets.toml` to Git.** It is already listed in `.gitignore`.
Only `.streamlit/secrets.toml.example` (placeholders only) should ever be committed.

---

## 7. Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

Open the URL Streamlit prints (typically `http://localhost:8501`), enter your
`APP_PASSWORD`, and you're in.

---

## 8. Deploy to Streamlit Community Cloud

### Step 1 — Create a GitHub repository

Push the project to a new GitHub repository.

### Step 2 — Upload these files

```
app.py
config.py
database.py
b2_storage.py
auth.py
utils.py
requirements.txt
.gitignore
README.md
.streamlit/secrets.toml.example
```

Do **not** push a real `.streamlit/secrets.toml` — it's git-ignored by default, and
Streamlit Community Cloud secrets are configured separately (see Step 4).

### Step 3 — Deploy

1. Go to [share.streamlit.io](https://share.streamlit.io) and sign in.
2. Click **New app**, pick your repository, branch, and set the main file to `app.py`.
3. Deploy.

### Step 4 — Configure secrets on Streamlit Community Cloud

In your app's dashboard, go to **Settings → Secrets** and paste the same contents you
put in your local `secrets.toml`:

```toml
APP_PASSWORD = "YOUR_PASSWORD"

B2_ENDPOINT = "https://s3.YOUR_REGION.backblazeb2.com"
B2_BUCKET_NAME = "YOUR_BUCKET_NAME"
B2_KEY_ID = "YOUR_KEY_ID"
B2_APPLICATION_KEY = "YOUR_APPLICATION_KEY"

MAX_FILE_SIZE_MB = 200
FREE_STORAGE_LIMIT_GB = 10
```

Save — the app restarts automatically with the new configuration.

---

## 9. Using the app

- **Login** with `APP_PASSWORD`.
- **My Files** — browse folders, create new folders, open/close nested folders via
  breadcrumbs, download/rename/delete files.
- **Upload** — pick one or more files, see their sizes, upload into the current folder.
  Oversized files or uploads that would exceed your storage limit are rejected before
  ever reaching B2.
- **Search** — searches file name, folder, and extension across your whole bucket using
  the local SQLite index (fast — no full bucket listing per keystroke). The search box in
  the header works the same way from any page.
- **Storage** — usage dashboard: used/available/limit, file & folder counts, largest
  files, and a breakdown by file type. Includes a button to recompute actual usage
  directly from B2 (bypassing the SQLite cache) if you ever want to double-check it.
- **Settings** — test the B2 connection, view (non-secret) configuration, and run
  **Sync Database From B2** to rebuild the local metadata index from scratch.

---

## 10. How storage limits work

Two independent limits are enforced, both configurable via secrets:

- **`MAX_FILE_SIZE_MB`** (default 200) — the largest single file the app will accept.
- **`FREE_STORAGE_LIMIT_GB`** (default 10) — an application-level cap on total usage.
  This is **not** the same as your actual Backblaze B2 plan/free-tier allowance — it's a
  guard rail this app enforces on top of whatever B2 itself allows, so check your own B2
  plan/billing separately.

Before every upload the app checks `current_usage + incoming_file_size <= limit` using
the SQLite metadata cache, and rejects the upload with a clear message if it would exceed
either limit.

---

## 11. Security considerations

- `B2_KEY_ID` and `B2_APPLICATION_KEY` never reach the browser. They are read once
  server-side from `st.secrets` and used only inside `b2_storage.py`.
- The bucket should be **private**; the app never makes it public. Downloads are streamed
  from B2, through the authenticated Streamlit server, to the logged-in browser session.
- The app password is compared using a constant-time comparison
  (`hmac.compare_digest`) to reduce timing side-channels. It currently reads a plaintext
  value from `st.secrets["APP_PASSWORD"]` — the comparison logic lives in one function
  (`auth._password_matches`) so it can be swapped for a hashed-password check later
  without touching any other code.
- Uploaded and typed file/folder names are sanitized (`utils.sanitize_name` /
  `normalize_folder_path`) to strip `..`, `/`, `\`, and control characters, preventing
  path traversal outside the intended B2 key namespace.
- All SQLite queries are parameterized — user input is never concatenated into SQL.
- Unknown file types are still accepted and stored with MIME type
  `application/octet-stream` when a type can't be guessed; MIME type is metadata only,
  never used as a security boundary.

---

## 12. SQLite limitation on hosted environments

**Important:** on Streamlit Community Cloud (and most free/ephemeral hosting), the local
filesystem — including `storage.db` — can be wiped whenever the app restarts, sleeps, or
redeploys. The actual files are always safe in B2 regardless, but the local metadata
cache is not guaranteed to persist.

For this reason:

- Treat `storage.db` as a **rebuildable cache**, not a source of truth.
- Use **Settings → Sync Database From B2** after any redeploy or restart to
  rebuild file/folder metadata directly from what's actually in your bucket.
- If you need metadata to survive restarts without manual syncing, the codebase is
  structured so `database.py` can later be swapped for a persistent database (e.g.
  Postgres) — this is documented here as an optional future upgrade, and is **not**
  implemented in this initial version per the project's requirements.

---

## 13. B2 recovery / synchronization

`database.rebuild_from_b2_objects()` (wired up to the **Sync Database From B2** button in
Settings) will:

1. List every object in the configured bucket.
2. Parse folder prefixes from each object key.
3. Insert or update matching SQLite file/folder records.
4. Never touches or deletes anything in B2 — it's a read-only scan of B2 used only to
   repair the local cache.

This is your safety net: even if `storage.db` is completely lost, your files are never
at risk, and one click rebuilds the browsing/search index.

---

## 14. Known limitations (documented, not hidden)

- **Folder rename** is intentionally not implemented (file rename is). Renaming a folder
  means rewriting every object key beneath it; a half-finished version of that is worse
  than not having it, so it's left out of this version rather than shipped broken.
- **Downloads are memory-buffered.** Streamlit's `st.download_button` needs the full file
  in memory before offering it to the browser, so a file near your `MAX_FILE_SIZE_MB`
  limit will use a comparable amount of RAM during download. Keep this in mind if you
  raise the size limit significantly.
- The application-level `FREE_STORAGE_LIMIT_GB` is enforced by this app, independent of
  whatever your actual Backblaze B2 plan or billing allows — the two are not the same
  thing.

---

## 15. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| "Backblaze configuration is incomplete" on startup | One or more required secrets are missing — check `.streamlit/secrets.toml` against `secrets.toml.example`. |
| "Backblaze B2 credentials were rejected" | Double-check `B2_KEY_ID` / `B2_APPLICATION_KEY`, and that the key has access to the configured bucket. |
| "Bucket ... was not found" | Check `B2_BUCKET_NAME` and that `B2_ENDPOINT` matches the bucket's actual region. |
| Files/folders "disappeared" after a redeploy | The hosted SQLite file was likely reset. Go to **Settings → Sync Database From B2**. |
| Upload rejected as "too large" | Raise `MAX_FILE_SIZE_MB` in secrets if appropriate for your host's memory limits. |
| Upload rejected for "Storage limit reached" | Raise `FREE_STORAGE_LIMIT_GB`, or delete some files first. |
| Duplicate filenames | The app automatically renames incoming duplicates to `name (1).ext`, `name (2).ext`, etc. — it never silently overwrites. |

---

## 16. Project structure

```
personal_cloud_storage/
├── app.py                          # Streamlit UI and page routing
├── config.py                       # Secrets loading & validation
├── database.py                     # SQLite metadata layer
├── b2_storage.py                   # Backblaze B2 (boto3) client wrapper
├── auth.py                         # Login / logout / session auth
├── utils.py                        # Sanitization, formatting, icons, MIME
├── requirements.txt
├── README.md
├── .gitignore
└── .streamlit/
    └── secrets.toml.example
```
