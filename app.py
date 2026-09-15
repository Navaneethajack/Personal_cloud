"""
app.py

Main Streamlit application for the Personal Cloud Storage project.

Ties together:
    - config.py   (configuration / secrets)
    - auth.py     (login / session authentication)
    - database.py (SQLite metadata)
    - b2_storage.py (Backblaze B2 file storage)
    - utils.py    (shared helpers)

Run with:
    streamlit run app.py
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import streamlit as st

import auth
import b2_storage
import database
import utils
from config import AppConfig, is_configured, load_config, render_setup_instructions

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("personal_cloud_storage.app")

st.set_page_config(
    page_title="My Cloud Storage",
    page_icon="☁️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Styling
# ---------------------------------------------------------------------------

def inject_css() -> None:
    st.markdown(
        """
        <style>
        #MainMenu {visibility: hidden;}
        footer {visibility: hidden;}
        header[data-testid="stHeader"] {background: transparent;}

        .block-container {
            padding-top: 1.5rem;
            padding-bottom: 3rem;
            max-width: 1100px;
        }

        .cloud-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 1rem;
            padding: 1rem 1.25rem;
            border-radius: 16px;
            background: linear-gradient(135deg, #4f46e5 0%, #6366f1 60%, #818cf8 100%);
            color: white;
            margin-bottom: 1.5rem;
        }
        .cloud-header h1 {
            color: white;
            font-size: 1.4rem;
            margin: 0;
        }
        .cloud-header .storage-pill {
            background: rgba(255,255,255,0.18);
            border-radius: 999px;
            padding: 0.35rem 0.9rem;
            font-size: 0.85rem;
        }

        .item-card {
            border: 1px solid rgba(120,120,120,0.18);
            border-radius: 12px;
            padding: 0.6rem 0.9rem;
            margin-bottom: 0.5rem;
            background: rgba(127,127,127,0.04);
        }
        .item-card:hover {
            background: rgba(99,102,241,0.08);
            border-color: rgba(99,102,241,0.35);
        }

        .muted { color: #888; font-size: 0.82rem; }
        .breadcrumb-bar {
            padding: 0.5rem 0;
            font-size: 0.95rem;
            margin-bottom: 0.5rem;
        }
        .section-title {
            font-size: 1.1rem;
            font-weight: 700;
            margin: 1rem 0 0.5rem 0;
        }
        div[data-testid="stVerticalBlockBorderWrapper"] {
            border-radius: 12px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Session state helpers
# ---------------------------------------------------------------------------

def _init_session_state() -> None:
    defaults = {
        "current_folder": "",
        "nav_page": "Home",
        "search_query": "",
        "confirm_delete_key": None,
        "confirm_delete_folder": None,
        "details_file_id": None,
        "rename_file_id": None,
        "flash_messages": [],
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _flash(message: str, kind: str = "success") -> None:
    st.session_state["flash_messages"].append((kind, message))


def _render_flash_messages() -> None:
    messages = st.session_state.get("flash_messages", [])
    for kind, message in messages:
        if kind == "success":
            st.success(message)
        elif kind == "error":
            st.error(message)
        else:
            st.info(message)
    st.session_state["flash_messages"] = []


# ---------------------------------------------------------------------------
# Header / Sidebar
# ---------------------------------------------------------------------------

def render_header(config: AppConfig, used_bytes: int) -> None:
    limit_bytes = config.free_storage_limit_bytes
    used_str = utils.format_size(used_bytes)
    limit_str = utils.format_size(limit_bytes)

    st.markdown(
        f"""
        <div class="cloud-header">
            <div>
                <h1>☁️ My Cloud Storage</h1>
            </div>
            <div class="storage-pill">📊 {used_str} / {limit_str}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    top_cols = st.columns([3, 1])
    with top_cols[0]:
        query = st.text_input(
            "Search files...",
            value=st.session_state.get("search_query", ""),
            placeholder="🔍 Search files across all folders...",
            label_visibility="collapsed",
            key="header_search_input",
        )
        st.session_state["search_query"] = query
    with top_cols[1]:
        if st.button("🚪 Logout", use_container_width=True):
            auth.logout()
            st.rerun()


def render_sidebar() -> str:
    st.sidebar.markdown("### ☁️ Navigation")
    pages = [
        ("🏠 Home", "Home"),
        ("📁 My Files", "My Files"),
        ("⬆ Upload", "Upload"),
        ("🔍 Search", "Search"),
        ("📊 Storage", "Storage"),
        ("⚙ Settings", "Settings"),
    ]
    current = st.session_state.get("nav_page", "Home")
    for label, page_key in pages:
        button_type = "primary" if current == page_key else "secondary"
        if st.sidebar.button(label, use_container_width=True, key=f"nav_{page_key}", type=button_type):
            st.session_state["nav_page"] = page_key
            st.rerun()

    st.sidebar.markdown("---")
    if st.sidebar.button("🚪 Logout", use_container_width=True, key="nav_logout"):
        auth.logout()
        st.rerun()

    return st.session_state.get("nav_page", "Home")


# ---------------------------------------------------------------------------
# Breadcrumb navigation
# ---------------------------------------------------------------------------

def render_breadcrumb(current_folder: str) -> None:
    st.markdown('<div class="breadcrumb-bar">', unsafe_allow_html=True)
    segments = [s for s in current_folder.split("/") if s] if current_folder else []

    cols = st.columns(len(segments) + 1)
    with cols[0]:
        if st.button("🏠 Home", key="breadcrumb_home"):
            st.session_state["current_folder"] = ""
            st.rerun()

    accumulated = ""
    for i, seg in enumerate(segments):
        accumulated = seg if not accumulated else f"{accumulated}/{seg}"
        with cols[i + 1]:
            if st.button(f"/ {seg}", key=f"breadcrumb_{i}_{seg}"):
                st.session_state["current_folder"] = accumulated
                st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Actions: download / delete / rename
# ---------------------------------------------------------------------------

def _handle_download(config: AppConfig, file: "database.FileRecord") -> None:
    cache_key = f"dl_cache_{file.object_key}"
    if st.button("⬇ Download", key=f"dlbtn_{file.id}"):
        with st.spinner(f"Preparing {file.name} for download..."):
            try:
                body = b2_storage.download_object_stream(config, file.object_key)
                st.session_state[cache_key] = body.read()
            except b2_storage.B2Error as exc:
                st.error(str(exc))

    if cache_key in st.session_state:
        st.download_button(
            "💾 Save file",
            data=st.session_state[cache_key],
            file_name=file.name,
            mime=file.mime_type,
            key=f"savebtn_{file.id}",
            on_click=lambda: st.session_state.pop(cache_key, None),
        )


def _handle_delete_file(config: AppConfig, file: "database.FileRecord") -> None:
    confirm_key = f"file:{file.object_key}"
    if st.session_state.get("confirm_delete_key") == confirm_key:
        st.warning(f"Delete **{file.name}** ({utils.format_size(file.size)})? This cannot be undone.")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("Cancel", key=f"cancel_del_{file.id}", use_container_width=True):
                st.session_state["confirm_delete_key"] = None
                st.rerun()
        with c2:
            if st.button("🗑 Delete", key=f"confirm_del_{file.id}", type="primary", use_container_width=True):
                try:
                    b2_storage.delete_object(config, file.object_key)
                except b2_storage.B2Error as exc:
                    st.error(f"Deletion failed, metadata was NOT removed: {exc}")
                else:
                    try:
                        database.delete_file(config.db_path, file.id)
                        _flash(f"Deleted '{file.name}'.")
                    except database.DatabaseError as exc:
                        st.error(
                            f"File was deleted from B2, but removing its metadata failed: {exc}. "
                            "Run 'Sync Database From B2' in Settings to repair the local index."
                        )
                st.session_state["confirm_delete_key"] = None
                st.rerun()
    else:
        if st.button("🗑 Delete", key=f"delbtn_{file.id}"):
            st.session_state["confirm_delete_key"] = confirm_key
            st.rerun()


def _handle_rename_file(config: AppConfig, file: "database.FileRecord") -> None:
    rename_active = st.session_state.get("rename_file_id") == file.id
    if rename_active:
        new_name = st.text_input("New name", value=file.name, key=f"rename_input_{file.id}")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("Cancel", key=f"rename_cancel_{file.id}", use_container_width=True):
                st.session_state["rename_file_id"] = None
                st.rerun()
        with c2:
            if st.button("✅ Save", key=f"rename_save_{file.id}", type="primary", use_container_width=True):
                clean_name = utils.sanitize_name(new_name)
                existing = database.list_existing_names_in_folder(config.db_path, file.folder)
                existing = [n for n in existing if n != file.name]
                if clean_name in existing:
                    clean_name = utils.unique_display_name(clean_name, existing)
                new_key = utils.build_object_key(file.folder, clean_name)
                try:
                    b2_storage.copy_object(config, file.object_key, new_key)
                    b2_storage.delete_object(config, file.object_key)
                    database.update_file_metadata(config.db_path, file.id, clean_name, new_key, file.folder)
                    _flash(f"Renamed to '{clean_name}'.")
                except (b2_storage.B2Error, database.DatabaseError) as exc:
                    st.error(f"Rename failed: {exc}")
                st.session_state["rename_file_id"] = None
                st.rerun()
    else:
        if st.button("✏️ Rename", key=f"renamebtn_{file.id}"):
            st.session_state["rename_file_id"] = file.id
            st.rerun()


def _render_file_details(file: "database.FileRecord") -> None:
    with st.expander(f"ℹ️ Details — {file.name}", expanded=True):
        st.markdown(f"**Name:** {file.name}")
        st.markdown(f"**Type / MIME:** `{file.mime_type}`")
        st.markdown(f"**Extension:** `{file.extension or '—'}`")
        st.markdown(f"**Size:** {utils.format_size(file.size)}")
        st.markdown(f"**Folder:** {file.folder or 'Home'}")
        st.markdown(f"**Uploaded:** {file.created_at}")
        st.markdown(f"**Last modified:** {file.updated_at}")
        st.markdown(f"**Object key:** `{file.object_key}`")
        if st.button("Close", key=f"close_details_{file.id}"):
            st.session_state["details_file_id"] = None
            st.rerun()


def _render_file_row(config: AppConfig, file: "database.FileRecord") -> None:
    with st.container(border=True):
        cols = st.columns([0.5, 3, 1.2, 1.6, 3.2])
        cols[0].markdown(f"### {utils.get_file_icon(file.name)}")
        cols[1].markdown(f"**{file.name}**")
        cols[2].markdown(f"<span class='muted'>{utils.format_size(file.size)}</span>", unsafe_allow_html=True)
        cols[3].markdown(f"<span class='muted'>{file.updated_at[:10]}</span>", unsafe_allow_html=True)
        with cols[4]:
            action_cols = st.columns(4)
            with action_cols[0]:
                _handle_download(config, file)
            with action_cols[1]:
                if st.button("ℹ️ Info", key=f"infobtn_{file.id}"):
                    st.session_state["details_file_id"] = (
                        None if st.session_state.get("details_file_id") == file.id else file.id
                    )
                    st.rerun()
            with action_cols[2]:
                _handle_rename_file(config, file)
            with action_cols[3]:
                _handle_delete_file(config, file)

        if st.session_state.get("details_file_id") == file.id:
            _render_file_details(file)


def _handle_delete_folder(config: AppConfig, folder_path: str, folder_name: str) -> None:
    confirm_key = f"folder:{folder_path}"
    if st.session_state.get("confirm_delete_folder") == confirm_key:
        st.warning(f"Delete folder **{folder_name}** and everything inside it? This cannot be undone.")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("Cancel", key=f"cancel_delf_{folder_path}", use_container_width=True):
                st.session_state["confirm_delete_folder"] = None
                st.rerun()
        with c2:
            if st.button("🗑 Delete folder", key=f"confirm_delf_{folder_path}", type="primary", use_container_width=True):
                try:
                    keys = b2_storage.list_object_keys(config, prefix=f"{folder_path}/")
                    for key in keys:
                        b2_storage.delete_object(config, key)
                    database.delete_folder(config.db_path, folder_path)
                    _flash(f"Deleted folder '{folder_name}' and {len(keys)} file(s).")
                except (b2_storage.B2Error, database.DatabaseError) as exc:
                    st.error(f"Folder deletion failed: {exc}")
                st.session_state["confirm_delete_folder"] = None
                st.rerun()
    else:
        if st.button("🗑", key=f"delfbtn_{folder_path}", help="Delete folder"):
            st.session_state["confirm_delete_folder"] = confirm_key
            st.rerun()


def _render_folder_row(config: AppConfig, folder: "database.FolderRecord") -> None:
    with st.container(border=True):
        cols = st.columns([0.5, 4, 1.5])
        with cols[0]:
            st.markdown("### 📁")
        with cols[1]:
            if st.button(f"**{folder.name}**", key=f"openfolder_{folder.path}", use_container_width=True):
                st.session_state["current_folder"] = folder.path
                st.rerun()
        with cols[2]:
            _handle_delete_folder(config, folder.path, folder.name)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

def render_file_browser_page(config: AppConfig) -> None:
    current_folder = st.session_state.get("current_folder", "")
    st.markdown('<div class="section-title">📁 My Files</div>', unsafe_allow_html=True)
    render_breadcrumb(current_folder)

    with st.expander("➕ New folder"):
        new_folder_name = st.text_input("Folder name", key="new_folder_name_input")
        if st.button("Create folder", key="create_folder_btn"):
            clean = utils.sanitize_name(new_folder_name)
            if not new_folder_name.strip():
                st.error("Please enter a folder name.")
            else:
                path = utils.join_folder(current_folder, clean)
                try:
                    created_id = database.create_folder(config.db_path, clean, path)
                    if created_id is None:
                        st.error(f"A folder named '{clean}' already exists here.")
                    else:
                        _flash(f"Folder '{clean}' created.")
                        st.rerun()
                except database.DatabaseError as exc:
                    st.error(str(exc))

    try:
        subfolders = database.list_subfolders(config.db_path, current_folder)
        files = database.list_files_in_folder(config.db_path, current_folder)
    except database.DatabaseError as exc:
        st.error(str(exc))
        return

    if not subfolders and not files:
        st.info("This folder is empty. Use the sidebar to upload files or create a subfolder above.")
        return

    if subfolders:
        st.markdown("##### Folders")
        for folder in subfolders:
            _render_folder_row(config, folder)

    if files:
        st.markdown("##### Files")
        for file in files:
            _render_file_row(config, file)


def render_upload_page(config: AppConfig) -> None:
    current_folder = st.session_state.get("current_folder", "")
    st.markdown('<div class="section-title">⬆ Upload Files</div>', unsafe_allow_html=True)
    st.markdown(f"**Current folder:** {current_folder or 'Home'}")

    uploaded_files = st.file_uploader(
        "Choose files",
        accept_multiple_files=True,
        label_visibility="collapsed",
        key="uploader_widget",
    )

    if not uploaded_files:
        return

    st.markdown("##### Selected files")
    total_incoming = 0
    for f in uploaded_files:
        st.markdown(f"- {utils.get_file_icon(f.name)} **{f.name}** — {utils.format_size(f.size)}")
        total_incoming += f.size

    if st.button("⬆ Upload", type="primary"):
        try:
            current_usage = database.calculate_total_usage(config.db_path)
        except database.DatabaseError as exc:
            st.error(str(exc))
            return

        existing_names = database.list_existing_names_in_folder(config.db_path, current_folder)
        success_count = 0
        failure_count = 0
        progress = st.progress(0, text="Starting upload...")

        for i, uploaded_file in enumerate(uploaded_files):
            progress.progress((i) / len(uploaded_files), text=f"Uploading {uploaded_file.name}...")

            if uploaded_file.size > config.max_file_size_bytes:
                _flash(
                    f"'{uploaded_file.name}' is too large. "
                    f"Maximum allowed size: {config.max_file_size_mb} MB",
                    kind="error",
                )
                failure_count += 1
                continue

            if current_usage + uploaded_file.size > config.free_storage_limit_bytes:
                available = config.free_storage_limit_bytes - current_usage
                _flash(
                    f"Storage limit reached for '{uploaded_file.name}'. "
                    f"Available space: {utils.format_size(max(available, 0))}. "
                    f"Required space: {utils.format_size(uploaded_file.size)}",
                    kind="error",
                )
                failure_count += 1
                continue

            safe_name = utils.sanitize_name(uploaded_file.name)
            unique_name = utils.unique_display_name(safe_name, existing_names)
            object_key = utils.build_object_key(current_folder, unique_name)
            mime_type = uploaded_file.type or utils.guess_mime_type(unique_name)
            extension = utils.get_extension(unique_name)

            try:
                b2_storage.upload_fileobj(config, uploaded_file, object_key, mime_type)
            except b2_storage.B2Error as exc:
                # IMPORTANT: this must go through _flash(), not a raw
                # st.error(). A raw st.error() here only lives for the
                # current script run -- the st.rerun() a few lines below
                # (which must always fire on a mixed-result batch, since
                # successful files DO need the page refreshed) immediately
                # throws this run away, so the message vanishes before
                # it can be read. _flash() persists it in session_state so
                # _render_flash_messages() can show it after the rerun.
                _flash(f"Upload failed for '{unique_name}': {exc}", kind="error")
                failure_count += 1
                continue

            try:
                database.insert_file(
                    config.db_path,
                    name=unique_name,
                    object_key=object_key,
                    folder=current_folder,
                    size=uploaded_file.size,
                    mime_type=mime_type,
                    extension=extension,
                )
            except database.DatabaseError as exc:
                _flash(
                    f"'{unique_name}' was uploaded to Backblaze B2, but saving its metadata failed: {exc}. "
                    "Run 'Sync Database From B2' in Settings to repair the local index.",
                    kind="error",
                )
                failure_count += 1
                continue

            existing_names.append(unique_name)
            current_usage += uploaded_file.size
            success_count += 1

        if success_count and not failure_count:
            done_text = "Done"
        elif success_count and failure_count:
            done_text = f"Done — {success_count} succeeded, {failure_count} failed"
        else:
            done_text = "No files were uploaded"

        progress.progress(1.0, text=done_text)
        time.sleep(0.2)
        progress.empty()

        if success_count:
            _flash(f"✓ {success_count} file(s) uploaded successfully")

        if success_count == 0:
            # Nothing succeeded: skip the rerun so the flashed error
            # messages render immediately below, instead of surviving only
            # in session_state until some future rerun. This also leaves
            # the same files selected in the uploader for an easy retry
            # once the underlying issue (see the error below) is fixed.
            _render_flash_messages()
            return

        st.rerun()


def render_search_page(config: AppConfig, query: Optional[str] = None) -> None:
    st.markdown('<div class="section-title">🔍 Search</div>', unsafe_allow_html=True)
    active_query = query if query is not None else st.text_input("Search all files", key="search_page_input")

    if not active_query:
        st.info("Type a filename, folder name, or extension above to search.")
        return

    try:
        results = database.search_files(config.db_path, active_query)
    except database.DatabaseError as exc:
        st.error(str(exc))
        return

    if not results:
        st.warning(f"No files found matching '{active_query}'.")
        return

    st.markdown(f"**{len(results)} result(s) for '{active_query}'**")
    for file in results:
        st.caption(f"📂 {file.folder or 'Home'}")
        _render_file_row(config, file)


def render_storage_page(config: AppConfig) -> None:
    st.markdown('<div class="section-title">📊 Storage</div>', unsafe_allow_html=True)

    try:
        stats = database.get_stats(config.db_path)
    except database.DatabaseError as exc:
        st.error(str(exc))
        return

    used = stats["total_size"]
    limit = config.free_storage_limit_bytes
    available = max(limit - used, 0)
    fraction = min(used / limit, 1.0) if limit > 0 else 0.0

    c1, c2, c3 = st.columns(3)
    c1.metric("Used", utils.format_size(used))
    c2.metric("Available", utils.format_size(available))
    c3.metric("Limit", utils.format_size(limit))
    st.progress(fraction)

    c4, c5 = st.columns(2)
    c4.metric("Files", stats["file_count"])
    c5.metric("Folders", stats["folder_count"])

    if stats["largest_files"]:
        st.markdown("##### Largest files")
        for f in stats["largest_files"]:
            st.markdown(f"- {f['name']} ({f['folder'] or 'Home'}) — {utils.format_size(f['size'])}")

    st.markdown("##### File type breakdown")
    try:
        all_files = database.list_all_files(config.db_path)
    except database.DatabaseError as exc:
        st.error(str(exc))
        return

    category_totals: dict = {}
    for f in all_files:
        cat = utils.get_file_category(f.name)
        category_totals[cat] = category_totals.get(cat, 0) + f.size

    if category_totals:
        for cat, size in sorted(category_totals.items(), key=lambda kv: kv[1], reverse=True):
            st.markdown(f"- **{cat}**: {utils.format_size(size)}")
    else:
        st.caption("No files yet.")

    st.markdown("---")
    if st.button("🔄 Recalculate usage from Backblaze B2"):
        with st.spinner("Recalculating usage directly from Backblaze B2..."):
            try:
                actual_usage = b2_storage.calculate_bucket_usage(config)
                st.success(f"Actual B2 usage: {utils.format_size(actual_usage)} (SQLite cache: {utils.format_size(used)})")
            except b2_storage.B2Error as exc:
                st.error(str(exc))


def render_settings_page(config: AppConfig) -> None:
    st.markdown('<div class="section-title">⚙ Settings</div>', unsafe_allow_html=True)

    st.markdown("##### Connection")
    if st.button("Test Backblaze B2 connection"):
        with st.spinner("Checking connection..."):
            try:
                b2_storage.check_connection(config)
                st.success(f"Connected to bucket '{config.b2_bucket_name}'.")
            except b2_storage.B2Error as exc:
                st.error(str(exc))

    st.markdown("##### Configuration (read-only)")
    st.markdown(f"- **Bucket:** `{config.b2_bucket_name}`")
    st.markdown(f"- **Endpoint:** `{config.b2_endpoint}`")
    st.markdown(f"- **Max file size:** {config.max_file_size_mb} MB")
    st.markdown(f"- **Storage limit:** {config.free_storage_limit_gb} GB")
    st.caption("B2 credentials are never displayed here or anywhere else in the app.")

    st.markdown("---")
    st.markdown("##### Database recovery")
    st.caption(
        "If the local SQLite database is lost or out of sync (e.g. after redeploying on "
        "an ephemeral host), rebuild it by scanning the actual contents of your B2 bucket. "
        "This never deletes or modifies files in B2."
    )
    if st.button("🔄 Sync Database From B2", type="primary"):
        with st.spinner("Scanning Backblaze B2 and rebuilding local metadata..."):
            try:
                objects = list(b2_storage.list_all_objects(config))
                result = database.rebuild_from_b2_objects(config.db_path, objects)
                st.success(
                    f"Sync complete. Inserted: {result['inserted']}, "
                    f"Updated: {result['updated']}, Folders: {result['folders']}."
                )
            except (b2_storage.B2Error, database.DatabaseError) as exc:
                st.error(str(exc))

    st.markdown("---")
    st.markdown("##### Known limitations")
    st.markdown(
        "- Folder rename is intentionally **not implemented** in this version "
        "(only file rename is supported), to avoid a partially-working bulk key rewrite.\n"
        "- SQLite runs on local/ephemeral storage; use 'Sync Database From B2' after "
        "redeploying on a host that wipes local disk between restarts.\n"
        "- Downloads are buffered in memory for the duration of the download, so very "
        "large files close to the configured maximum will use a comparable amount of RAM."
    )


def render_home_page(config: AppConfig) -> None:
    st.markdown('<div class="section-title">🏠 Home</div>', unsafe_allow_html=True)
    try:
        stats = database.get_stats(config.db_path)
    except database.DatabaseError as exc:
        st.error(str(exc))
        return

    c1, c2, c3 = st.columns(3)
    c1.metric("Files", stats["file_count"])
    c2.metric("Folders", stats["folder_count"])
    c3.metric("Storage used", utils.format_size(stats["total_size"]))

    st.markdown("##### Recent files")
    try:
        all_files = database.list_all_files(config.db_path)
    except database.DatabaseError as exc:
        st.error(str(exc))
        return

    recent = sorted(all_files, key=lambda f: f.updated_at, reverse=True)[:8]
    if not recent:
        st.info("No files yet. Head to **Upload** in the sidebar to add your first file.")
        return

    for file in recent:
        _render_file_row(config, file)


# ---------------------------------------------------------------------------
# Main entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    inject_css()

    if not is_configured():
        st.markdown("## ☁️ My Cloud Storage — Setup Required")
        render_setup_instructions()
        st.stop()

    config = load_config()

    try:
        database.init_db(config.db_path)
    except database.DatabaseError as exc:
        st.error(f"Could not initialize the local database: {exc}")
        st.stop()

    if not auth.require_auth(config):
        st.stop()

    _init_session_state()

    try:
        used_bytes = database.calculate_total_usage(config.db_path)
    except database.DatabaseError:
        used_bytes = 0

    render_header(config, used_bytes)
    _render_flash_messages()
    page = render_sidebar()

    search_query = st.session_state.get("search_query", "").strip()
    if search_query:
        render_search_page(config, query=search_query)
        return

    if page == "Home":
        render_home_page(config)
    elif page == "My Files":
        render_file_browser_page(config)
    elif page == "Upload":
        render_upload_page(config)
    elif page == "Search":
        render_search_page(config)
    elif page == "Storage":
        render_storage_page(config)
    elif page == "Settings":
        render_settings_page(config)
    else:
        render_home_page(config)


if __name__ == "__main__":
    main()
