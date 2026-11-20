"""web ui for media download"""

import logging
import os
import threading
import time

from flask import Flask, jsonify, render_template, request
from flask_login import LoginManager, UserMixin, login_required, login_user

import utils
from module import db
from module.app import Application
from module.download_stat import (
    DownloadState,
    get_download_result,
    get_download_state,
    get_total_download_speed,
    set_download_state,
)
from utils.crypto import AesBase64
from utils.format import format_byte

log = logging.getLogger("werkzeug")
log.setLevel(logging.ERROR)

_flask_app = Flask(__name__)

_flask_app.secret_key = "tdl"
_login_manager = LoginManager()
_login_manager.login_view = "login"
_login_manager.init_app(_flask_app)
web_login_users: dict = {}
deAesCrypt = AesBase64("1234123412ABCDEF", "ABCDEF1234123412")
_web_start_time: float = time.time()


class User(UserMixin):
    """Web Login User"""

    def __init__(self):
        self.sid = "root"

    @property
    def id(self):
        """ID"""
        return self.sid


@_login_manager.user_loader
def load_user(_):
    """
    Load a user object from the user ID.

    Returns:
        User: The user object.
    """
    return User()


def get_flask_app() -> Flask:
    """get flask app instance"""
    return _flask_app


def run_web_server(app: Application):
    """
    Runs a web server using the Flask framework.
    """

    get_flask_app().run(
        app.web_host, app.web_port, debug=app.debug_web, use_reloader=False
    )


# pylint: disable = W0603
def init_web(app: Application):
    """
    Set the value of the users variable.

    Args:
        users: The list of users to set.

    Returns:
        None.
    """
    global web_login_users
    if app.web_login_secret:
        web_login_users = {"root": app.web_login_secret}
    else:
        _flask_app.config["LOGIN_DISABLED"] = True
    if app.debug_web:
        threading.Thread(target=run_web_server, args=(app,)).start()
    else:
        threading.Thread(
            target=get_flask_app().run, daemon=True, args=(app.web_host, app.web_port)
        ).start()


@_flask_app.route("/login", methods=["GET", "POST"])
def login():
    """
    Function to handle the login route.

    Parameters:
    - No parameters

    Returns:
    - If the request method is "POST" and the username and
      password match the ones in the web_login_users dictionary,
      it returns a JSON response with a code of "1".
    - Otherwise, it returns a JSON response with a code of "0".
    - If the request method is not "POST", it returns the rendered "login.html" template.
    """
    if request.method == "POST":
        username = "root"
        web_login_form = {}
        for key, value in request.form.items():
            if value:
                value = deAesCrypt.decrypt(value)
            web_login_form[key] = value

        if not web_login_form.get("password"):
            return jsonify({"code": "0"})

        password = web_login_form["password"]
        if username in web_login_users and web_login_users[username] == password:
            user = User()
            login_user(user)
            return jsonify({"code": "1"})

        return jsonify({"code": "0"})

    return render_template("login.html")


@_flask_app.route("/")
@login_required
def index():
    """Index html"""
    return render_template(
        "index.html",
        download_state=(
            "pause" if get_download_state() is DownloadState.Downloading else "continue"
        ),
    )


@_flask_app.route("/get_download_status")
@login_required
def get_download_speed():
    """Get download speed"""
    return jsonify(
        {
            "download_speed": f"{format_byte(get_total_download_speed())}/s",
            "upload_speed": "0.00 B/s",
        }
    )


@_flask_app.route("/set_download_state", methods=["POST"])
@login_required
def web_set_download_state():
    """Set download state"""
    state = request.args.get("state")

    if state == "continue" and get_download_state() is DownloadState.StopDownload:
        set_download_state(DownloadState.Downloading)
        return "pause"

    if state == "pause" and get_download_state() is DownloadState.Downloading:
        set_download_state(DownloadState.StopDownload)
        return "continue"

    return state


@_flask_app.route("/get_app_version")
@login_required
def get_app_version():
    """Get telegram_media_downloader version"""
    return utils.__version__


@_flask_app.route("/get_stats")
@login_required
def get_stats():
    """Session statistics for the dashboard"""
    download_result = get_download_result()
    downloading_count = 0
    downloaded_count = 0
    total_bytes = 0
    for messages in download_result.values():
        for value in messages.values():
            total_size = value["total_size"]
            if total_size and value["down_byte"] >= total_size:
                downloaded_count += 1
            else:
                downloading_count += 1
            total_bytes += value["down_byte"]

    return jsonify(
        {
            "downloading_count": downloading_count,
            "downloaded_count": downloaded_count,
            "total_bytes": total_bytes,
            "total_size": format_byte(total_bytes),
            "download_speed": f"{format_byte(get_total_download_speed())}/s",
            "uptime": int(time.time() - _web_start_time),
            "version": utils.__version__,
        }
    )


@_flask_app.route("/get_tasks")
@login_required
def get_tasks():
    """Recent bot download tasks from the sqlite database"""
    return jsonify([dict(row) for row in db.get_recent_tasks(30)])


@_flask_app.route("/task_action", methods=["POST"])
@login_required
def task_action():
    """Manage bot download tasks from the dashboard: stop / resume / delete."""
    import asyncio

    from module import bot as bot_module

    data = request.get_json(silent=True) or {}
    try:
        task_db_id = int(data.get("id"))
    except (TypeError, ValueError):
        return jsonify({"error": "invalid task id"}), 400

    action = data.get("action")
    if action not in ("stop", "resume", "delete"):
        return jsonify({"error": "invalid action"}), 400

    if not bot_module.is_bot_ready():
        return jsonify({"error": "bot is not running"}), 409

    if action == "stop":
        # only sets a flag - safe to call from the flask thread
        bot_module.stop_task_by_db_id(task_db_id)
        db.update_task_status(task_db_id, "stopped")
        return jsonify({"ok": True, "action": action})

    if action == "delete":
        bot_module.remove_task_by_db_id(task_db_id)
        removed = db.delete_task(task_db_id)
        return jsonify({"ok": True, "action": action, "removed": removed})

    # action == "resume": re-enqueue the task on the bot event loop
    live = [
        node
        for node in bot_module.find_task_nodes_by_db_id(task_db_id)
        if not node.is_finish()
    ]
    if live:
        return jsonify({"error": "task is already running"}), 409

    # progress messages are sent to the first numeric allowed user;
    # report_bot_status swallows send errors so 0 is a safe fallback
    from_user_id = 0
    for uid in bot_module._bot.allowed_user_ids:
        if isinstance(uid, int) or str(uid).lstrip("-").isdigit():
            from_user_id = int(uid)
            break

    future = asyncio.run_coroutine_threadsafe(
        bot_module.requeue_task(task_db_id, from_user_id),
        bot_module._bot.app.loop,
    )
    try:
        resumed = future.result(timeout=30)
    except Exception:  # pylint: disable = broad-except
        return jsonify({"error": "failed to resume task"}), 500
    if not resumed:
        return jsonify({"error": "task not found"}), 404
    return jsonify({"ok": True, "action": action})


@_flask_app.route("/get_download_list")
@login_required
def get_download_list():
    """get download list"""
    if request.args.get("already_down") is None:
        return "[]"

    already_down = request.args.get("already_down") == "true"

    download_result = get_download_result()
    result = []
    for chat_id, messages in download_result.items():
        for idx, value in messages.items():
            is_already_down = value["down_byte"] == value["total_size"]

            if already_down and not is_already_down:
                continue

            total_size = value["total_size"]
            progress = (
                round(value["down_byte"] / total_size * 100, 1)
                if total_size
                else 0
            )
            download_speed = format_byte(value["download_speed"]) + "/s"
            result.append(
                {
                    "chat": f"{chat_id}",
                    "id": f"{idx}",
                    "filename": os.path.basename(value["file_name"]),
                    "total_size": format_byte(total_size),
                    "download_progress": progress,
                    "download_speed": download_speed,
                    "save_path": value["file_name"].replace("\\", "/"),
                }
            )

    return jsonify(result)
