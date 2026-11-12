import sys, os, re, json, time, base64, hashlib, random, string, urllib.parse, traceback, unicodedata
from pathlib import Path
from difflib import SequenceMatcher

from datetime import datetime, timezone, timedelta
from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QMessageBox,
    QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QComboBox)
import requests
from playwright.sync_api import sync_playwright

APP_NAME = "中国移动云盘直链助手 V2.5"
PERSONAL_API = "https://personal-kd-njs.yun.139.com"
PROFILE_DIR = Path.home() / ".139_link_parser" / "browser_profile"
STATE_DIR = Path.home() / ".139_link_parser"
STATE_DIR.mkdir(parents=True, exist_ok=True)



def normalize_search_text(value):
    """Normalize text for case-insensitive, punctuation-tolerant search."""
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[\s\-_.,;:，。；：()（）\[\]【】]+", " ", value).strip()


def search_field_matches(query, field):
    """Match partial Chinese/English fields; tolerate small English typos."""
    q = normalize_search_text(query)
    value = normalize_search_text(field)
    if not q or not value:
        return False
    if q in value:
        return True
    # Split multi-keyword queries: every keyword may match a different part of the filename/extension.
    words = [w for w in q.split() if w]
    parts = [w for w in value.split() if w]
    for word in words:
        if word in value:
            continue
        # Fuzzy fallback is intended for English/number fragments, not arbitrary Chinese strings.
        if re.fullmatch(r"[a-z0-9]+", word) and len(word) >= 3:
            candidates = parts or [value]
            if not any(SequenceMatcher(None, word, part).ratio() >= 0.76 for part in candidates):
                return False
        else:
            return False
    return bool(words)


def file_matches_search(item, query):
    q = normalize_search_text(query)
    if not q:
        return False
    name = str(item.get("name") or "")
    extension = str(item.get("fileExtension") or "")
    # Extension search such as .exe / exe; otherwise search name and extension together.
    if q.startswith("."):
        suffix = q.lstrip(".")
        return normalize_search_text(extension).lstrip(".") == suffix or normalize_search_text(name).endswith(q)
    return search_field_matches(q, name) or search_field_matches(q, extension) or search_field_matches(q, name + " " + extension)



def md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()






class CloudClient:
    def __init__(self, cookies=None):
        self.cookies = cookies or {}
        self.session = requests.Session()




    def personal_post(self, endpoint, body):
        """Call the new personal-cloud API using the existing local login session."""
        if not self.cookies.get("authorization"):
            raise PermissionError("登录态中没有 authorization，请重新扫码登录。")
        # The service expects China-local time in the request signature.
        china_tz = timezone(timedelta(hours=8))
        stamp = datetime.now(china_tz).strftime("%Y-%m-%d %H:%M:%S")
        nonce = ''.join(random.choices(string.ascii_letters + string.digits, k=16))
        compact = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
        encoded = urllib.parse.quote(compact, safe="~()*!.'-")
        sorted_text = ''.join(sorted(encoded))
        first = md5(base64.b64encode(sorted_text.encode()).decode())
        signature = md5(first + md5(stamp + ":" + nonce)).upper()
        headers = {
            "Authorization": self.cookies["authorization"],
            "Caller": "web",
            "Content-Type": "application/json;charset=UTF-8",
            "Origin": "https://yun.139.com",
            "Referer": "https://yun.139.com/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36",
            "CMS-DEVICE": "default",
            "Mcloud-Channel": "1000101",
            "Mcloud-Client": "10701",
            "Mcloud-Sign": f"{stamp},{nonce},{signature}",
            "Mcloud-Version": "7.14.2",
            "X-DeviceInfo": "||9|7.17.0|edge||||windows 10||zh-CN|||",
            "X-Huawei-ChannelSrc": "10000034",
            "X-Inner-Ntwk": "2",
            "X-M4C-Caller": "PC",
            "X-M4C-Src": "10002",
            "X-SvcType": "1",
            "X-Yun-Api-Version": "v1",
            "X-Yun-App-Channel": "10000034",
            "X-Yun-Channel-Source": "10000034",
            "X-Yun-Client-Info": "||9|7.17.0|edge||||windows 10||zh-CN|||||",
            "X-Yun-Module-Type": "100",
            "X-Yun-Svc-Type": "1",
            "X-Yun-Url-Type": "3",
        }
        r = self.session.post(PERSONAL_API + endpoint, json=body, headers=headers, timeout=30)
        r.raise_for_status()
        try:
            data = r.json()
        except Exception:
            raise RuntimeError(f"个人云盘接口返回非 JSON 内容（HTTP {r.status_code}）")
        if not data.get("success", False):
            code = str(data.get("code", "未知"))
            message = str(data.get("message", "请求失败"))
            if code in ("401", "403", "200000401") or "鉴权" in message or "登录" in message:
                raise PermissionError(f"个人云盘鉴权失败（{code}）：{message}")
            raise RuntimeError(f"个人云盘接口错误（{code}）：{message}")
        return data

    def list_personal_files(self, parent_id="/"):
        items_all = []
        cursor = None
        while True:
            body = {
                "pageInfo": {"pageSize": 100, "pageCursor": cursor},
                "orderBy": "updated_at",
                "orderDirection": "DESC",
                "parentFileId": parent_id,
                "imageThumbnailStyleList": ["Small", "Large"],
            }
            result = self.personal_post("/hcy/file/list", body)
            data = result.get("data") or {}
            items_all.extend(data.get("items") or [])
            cursor = data.get("nextPageCursor")
            if not cursor:
                break
        return items_all

    def get_personal_direct(self, file_id):
        result = self.personal_post("/hcy/file/getDownloadUrl", {"fileId": file_id})
        data = result.get("data") or {}
        url = data.get("cdnUrl") or data.get("url")
        if not url:
            raise RuntimeError("接口请求成功，但响应中没有 cdnUrl 或 url 字段。")
        return str(url)


class LoginWorker(QThread):
    log = Signal(str)
    done = Signal(bool, str)

    def run(self):
        try:
            self.log.emit("正在打开中国移动云盘网页，请在浏览器中使用移动云盘 App 扫码登录……")
            with sync_playwright() as p:
                browser_type = p.chromium
                launch_args = {"headless": False, "args": ["--disable-blink-features=AutomationControlled"]}
                # Prefer installed Microsoft Edge; fall back to installed Google Chrome.
                try:
                    context = browser_type.launch_persistent_context(str(PROFILE_DIR), channel="msedge", **launch_args)
                except Exception:
                    context = browser_type.launch_persistent_context(str(PROFILE_DIR), channel="chrome", **launch_args)
                pages = context.pages
                page = pages[0] if pages else context.new_page()
                page.goto("https://yun.139.com/", wait_until="domcontentloaded", timeout=60000)
                self.log.emit("浏览器已打开。请完成扫码；程序正在等待登录态 Cookie……")
                deadline = time.time() + 240
                found = {}
                while time.time() < deadline:
                    try:
                        all_cookies = context.cookies(["https://yun.139.com", "https://139.com", "https://personal-kd-njs.yun.139.com"])
                        found = {c["name"]: c["value"] for c in all_cookies}
                        # Persist all first-party cookies in the Playwright profile; only required names are checked.
                        if found.get("authorization") and (found.get("auth_token") or found.get("skey")):
                            break
                    except Exception:
                        pass
                    time.sleep(2)
                context.close()
                if not found.get("authorization"):
                    self.done.emit(False, "等待超时或未发现有效登录 Cookie。请确认已在网页中完成扫码登录后重试。")
                    return
                wanted = ["authorization", "auth_token", "token", "skey", "a_k", "ud_id", "ORCHES-I-ACCOUNT-ENCRYPT"]
                saved = {k: found[k] for k in wanted if found.get(k)}
                (STATE_DIR / "cookies.json").write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
                self.done.emit(True, "扫码登录态已保存到本机。不会上传到第三方服务器。")
        except Exception as e:
            self.done.emit(False, f"扫码登录失败：{e}")




class PersonalListWorker(QThread):
    done = Signal(bool, object, str)
    def __init__(self, parent_id):
        super().__init__(); self.parent_id = parent_id
    def run(self):
        try:
            cookies = json.loads((STATE_DIR / "cookies.json").read_text(encoding="utf-8"))
            items = CloudClient(cookies).list_personal_files(self.parent_id)
            self.done.emit(True, items, "读取完成")
        except Exception as e:
            self.done.emit(False, [], str(e))


class FolderSizeWorker(QThread):
    """Recursively calculate total file bytes under each folder in the current result set."""
    done = Signal(bool, object, str)

    def __init__(self, folders):
        super().__init__()
        self.folders = [dict(x) for x in folders]

    def run(self):
        try:
            cookies = json.loads((STATE_DIR / "cookies.json").read_text(encoding="utf-8"))
            client = CloudClient(cookies)
            cache = {}
            active = set()
            def folder_total(folder_id, depth=0):
                folder_id = str(folder_id)
                if folder_id in cache:
                    return cache[folder_id]
                if folder_id in active:
                    return 0
                if depth > 100:
                    raise RuntimeError("文件夹层级超过 100 层，已停止统计以防止循环。")
                active.add(folder_id)
                total = 0
                children = client.list_personal_files(folder_id)
                for child in children:
                    if child.get("type") == "folder":
                        child_id = child.get("fileId")
                        if child_id:
                            total += folder_total(child_id, depth + 1)
                    else:
                        try:
                            total += max(0, int(child.get("size") or 0))
                        except (TypeError, ValueError):
                            pass
                active.remove(folder_id)
                cache[folder_id] = total
                return total

            result = {}
            for folder in self.folders:
                folder_id = folder.get("fileId")
                if folder_id:
                    result[str(folder_id)] = folder_total(folder_id)
            self.done.emit(True, result, "文件夹大小统计完成")
        except Exception as e:
            self.done.emit(False, {}, str(e))


class PersonalSearchWorker(QThread):
    done = Signal(bool, object, str)
    def __init__(self, query):
        super().__init__(); self.query = query
    @staticmethod
    def matches(item, query):
        return file_matches_search(item, query)
    def run(self):
        try:
            cookies = json.loads((STATE_DIR / "cookies.json").read_text(encoding="utf-8"))
            client = CloudClient(cookies)
            found, visited = [], set()
            stack = [("/", [("我的云盘", "/")])]
            while stack:
                parent_id, path_items = stack.pop()
                if parent_id in visited:
                    continue
                visited.add(parent_id)
                items = client.list_personal_files(parent_id)
                for item in items:
                    item = dict(item)
                    item["_search_path"] = path_items
                    if self.matches(item, self.query):
                        found.append(item)
                    if item.get("type") == "folder" and item.get("fileId"):
                        folder_id = str(item.get("fileId"))
                        folder_name = str(item.get("name") or "文件夹")
                        stack.append((folder_id, path_items + [(folder_name, folder_id)]))
            self.done.emit(True, found, f"全云盘搜索完成，共找到 {len(found)} 项")
        except Exception as e:
            self.done.emit(False, [], str(e))


class PersonalDirectWorker(QThread):
    done = Signal(bool, str, str)
    def __init__(self, file_id, filename):
        super().__init__(); self.file_id = file_id; self.filename = filename
    def run(self):
        try:
            cookies = json.loads((STATE_DIR / "cookies.json").read_text(encoding="utf-8"))
            url = CloudClient(cookies).get_personal_direct(self.file_id)
            self.done.emit(True, self.filename, url)
        except Exception as e:
            self.done.emit(False, self.filename, str(e))






class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1120, 720)
        self.setMinimumSize(900, 600)
        self.current_parent_id = "/"
        self.current_path = [("我的云盘", "/")]
        # Local display sorting only; it never changes cloud-side file order.
        self.personal_sort_column = 3
        self.personal_sort_ascending = False
        self.personal_items = []
        self.folder_size_cache = {}
        self.folder_size_worker = None
        self.folder_size_calculating = False
        self.search_results_mode = False
        self.search_worker = None
        self.direct_url = ""
        self.list_worker = None
        self.direct_worker = None
        self.login_worker = None

        root = QWidget(); self.setCentralWidget(root)
        layout = QVBoxLayout(root); layout.setSpacing(10)
        title_row = QHBoxLayout()
        title = QLabel("中国移动云盘直链助手")
        title.setStyleSheet("font-size:23px;font-weight:700;color:#1f2937")
        self.login_status = QLabel("登录状态：未检测")
        self.login_status.setStyleSheet("color:#64748b")
        self.login_btn = QPushButton("扫码登录")
        self.login_btn.clicked.connect(self.login)
        title_row.addWidget(title); title_row.addStretch(1); title_row.addWidget(self.login_status); title_row.addWidget(self.login_btn)
        layout.addLayout(title_row)
        subtitle = QLabel("读取个人云盘文件；解析后只复制下载直链，不会自动下载文件。")
        subtitle.setStyleSheet("color:#64748b")
        layout.addWidget(subtitle)

        self.tabs = QTabWidget(); layout.addWidget(self.tabs, 1)
        # Personal cloud tab
        personal = QWidget(); pv = QVBoxLayout(personal)
        toolbar = QHBoxLayout()
        self.path_container = QWidget()
        self.path_layout = QHBoxLayout(self.path_container)
        self.path_layout.setContentsMargins(0, 0, 0, 0)
        self.path_layout.setSpacing(4)
        # Create navigation control before any breadcrumb refresh can run.
        self.up_btn = QPushButton("返回上级")
        self.up_btn.clicked.connect(self.go_parent)
        self.search_scope = QComboBox()
        self.search_scope.addItems(["当前目录", "全云盘"])
        self.search_box = QLineEdit(); self.search_box.setPlaceholderText("输入名称关键词或扩展名，例如 .wim、.exe")
        self.search_box.returnPressed.connect(self.execute_personal_search)
        self.search_btn = QPushButton("搜索"); self.search_btn.clicked.connect(self.execute_personal_search)
        self.search_clear_btn = QPushButton("清除搜索"); self.search_clear_btn.clicked.connect(self.clear_personal_search)
        self.refresh_btn = QPushButton("刷新列表"); self.refresh_btn.clicked.connect(self.load_personal_files)
        toolbar.addWidget(self.path_container, 3); toolbar.addWidget(self.search_scope); toolbar.addWidget(self.search_box, 3)
        toolbar.addWidget(self.search_btn); toolbar.addWidget(self.search_clear_btn); toolbar.addWidget(self.up_btn); toolbar.addWidget(self.refresh_btn)
        pv.addLayout(toolbar)
        self.personal_table = QTableWidget(0, 5)
        self.personal_table.setHorizontalHeaderLabels(["文件名称", "类型", "大小", "修改时间", "操作"])
        self.personal_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.personal_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.personal_table.verticalHeader().setVisible(False)
        self.personal_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in (1,2,3,4): self.personal_table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.personal_table.horizontalHeader().sectionClicked.connect(self.sort_personal_table)
        self.personal_table.cellDoubleClicked.connect(self.personal_row_double_clicked)
        pv.addWidget(self.personal_table, 1)
        self.personal_status = QLabel("请先扫码登录，然后点击“刷新列表”。")
        self.personal_status.setStyleSheet("color:#64748b")
        pv.addWidget(self.personal_status)
        self.tabs.addTab(personal, "我的云盘")

        foot = QLabel("安全提示：登录凭证仅保存在当前 Windows 用户目录；不要分享 cookies.json。直链可能过期，请获取后及时使用。")
        foot.setWordWrap(True); foot.setStyleSheet("color:#94a3b8;font-size:11px")
        layout.addWidget(foot)
        self.update_breadcrumb()
        if (STATE_DIR / "cookies.json").exists():
            self.login_status.setText("登录状态：已保存登录信息")

    def login(self):
        self.login_btn.setEnabled(False)
        self.login_status.setText("登录状态：等待扫码…")
        self.personal_status.setText("正在打开浏览器，请完成扫码登录……")
        self.login_worker = LoginWorker()
        self.login_worker.log.connect(self.personal_status.setText)
        self.login_worker.done.connect(self.login_done)
        self.login_worker.start()

    def login_done(self, ok, message):
        self.login_btn.setEnabled(True)
        self.login_status.setText("登录状态：已登录" if ok else "登录状态：未登录")
        self.personal_status.setText(("✓ " if ok else "✗ ") + message)
        if ok:
            self.load_personal_files()
        else:
            QMessageBox.warning(self, "扫码登录", message)

    def execute_personal_search(self):
        query = self.search_box.text().strip()
        if not query:
            self.clear_personal_search()
            return
        if not (STATE_DIR / "cookies.json").exists():
            QMessageBox.information(self, "需要登录", "请先扫码登录，再搜索云盘文件。")
            return
        if self.search_scope.currentText() == "当前目录":
            self.search_results_mode = False
            self.filter_personal_table(query)
            visible = sum(not self.personal_table.isRowHidden(r) for r in range(self.personal_table.rowCount()))
            self.personal_status.setText(f"当前目录搜索完成：显示 {visible} 项（支持大小写不敏感和扩展名搜索）")
            return
        self.search_btn.setEnabled(False)
        self.search_clear_btn.setEnabled(False)
        self.personal_status.setText("正在遍历全云盘目录搜索，请稍候…")
        self.search_worker = PersonalSearchWorker(query)
        self.search_worker.done.connect(self.personal_search_done)
        self.search_worker.start()

    def personal_search_done(self, ok, items, message):
        self.search_btn.setEnabled(True)
        self.search_clear_btn.setEnabled(True)
        if not ok:
            self.personal_status.setText("全云盘搜索失败：" + message)
            QMessageBox.warning(self, "全云盘搜索失败", message)
            return
        self.search_results_mode = True
        self.personal_items = items
        self.render_personal_table(items)
        self.personal_status.setText(message + "；点击表头可排序")

    def clear_personal_search(self):
        self.search_box.clear()
        self.search_results_mode = False
        self.load_personal_files()

    def load_personal_files(self):
        self.search_results_mode = False
        cookie_file = STATE_DIR / "cookies.json"
        if not cookie_file.exists():
            QMessageBox.information(self, "需要登录", "请先点击右上角“扫码登录”，完成登录后再读取云盘文件。")
            return
        self.refresh_btn.setEnabled(False)
        self.personal_status.setText("正在读取文件列表…")
        self.list_worker = PersonalListWorker(self.current_parent_id)
        self.list_worker.done.connect(self.personal_list_done)
        self.list_worker.start()

    def personal_list_done(self, ok, items, message):
        self.refresh_btn.setEnabled(True)
        if not ok:
            self.personal_status.setText("读取失败：" + message)
            if "鉴权" in message or "登录" in message:
                self.login_status.setText("登录状态：需要重新登录")
            QMessageBox.warning(self, "读取云盘失败", message)
            return
        self.personal_items = items
        self.render_personal_table(items)
        self.personal_status.setText(f"读取完成：{len(items)} 个项目")

    @staticmethod
    def format_size(size):
        try:
            size = int(size or 0)
        except Exception:
            return "—"
        if size <= 0: return "—"
        units = ["B", "KB", "MB", "GB", "TB"]
        value = float(size)
        for unit in units:
            if value < 1024 or unit == units[-1]: return f"{value:.1f} {unit}"
            value /= 1024

    def personal_sort_value(self, item, column):
        if column == 0:
            return str(item.get("name") or "").casefold()
        if column == 1:
            return "文件夹" if item.get("type") == "folder" else str(item.get("fileExtension") or "文件").casefold()
        if column == 2:
            try:
                value = item.get("_computed_size")
                if value is None:
                    value = self.folder_size_cache.get(str(item.get("fileId")), item.get("size"))
                return int(value or 0)
            except (TypeError, ValueError):
                return -1
        if column == 3:
            return str(item.get("updatedAt") or "")
        return ""

    def sort_personal_table(self, column):
        if column not in (0, 1, 2, 3):
            return
        if self.personal_sort_column == column:
            self.personal_sort_ascending = not self.personal_sort_ascending
        else:
            self.personal_sort_column = column
            self.personal_sort_ascending = True

        if column == 2:
            folders = [x for x in self.personal_items if x.get("type") == "folder"]
            missing = [x for x in folders
                       if str(x.get("fileId")) not in self.folder_size_cache
                       and x.get("_computed_size") is None]
            if missing:
                self.personal_table.setEnabled(False)
                self.personal_status.setText(
                    f"正在递归统计 {len(missing)} 个文件夹的总大小；目录较多时可能需要一些时间……"
                )
                self.folder_size_calculating = True
                self.folder_size_worker = FolderSizeWorker(missing)
                self.folder_size_worker.done.connect(self.folder_size_done)
                self.folder_size_worker.start()
                return
        self.render_personal_table(self.personal_items)

    def folder_size_done(self, ok, sizes, message):
        self.folder_size_calculating = False
        self.personal_table.setEnabled(True)
        if not ok:
            self.personal_status.setText("文件夹大小统计失败：" + message)
            QMessageBox.warning(
                self, "文件夹大小统计失败",
                "无法取得部分文件夹的完整内容，因此没有将未知大小当作 0 排序。\n\n" + message
            )
            return
        self.folder_size_cache.update(sizes)
        for item in self.personal_items:
            file_id = str(item.get("fileId") or "")
            if item.get("type") == "folder" and file_id in sizes:
                item["_computed_size"] = sizes[file_id]
        self.render_personal_table(self.personal_items)
        self.personal_status.setText(
            f"{message}：已统计 {len(sizes)} 个文件夹；文件夹和文件分别排序，文件夹保持置顶。"
        )

    def update_personal_sort_headers(self):
        labels = ["文件名称", "类型", "大小", "修改时间", "操作"]
        if self.personal_sort_column in (0, 1, 2, 3):
            arrow = " ↑" if self.personal_sort_ascending else " ↓"
            labels[self.personal_sort_column] += arrow
        self.personal_table.setHorizontalHeaderLabels(labels)

    def update_breadcrumb(self):
        # Rebuild clickable path segments, e.g. 我的云盘 / 文档 / 项目。
        while self.path_layout.count():
            child = self.path_layout.takeAt(0)
            widget = child.widget()
            if widget:
                widget.deleteLater()
        for index, (name, _folder_id) in enumerate(self.current_path):
            if index:
                sep = QLabel("/")
                sep.setStyleSheet("color:#94a3b8")
                self.path_layout.addWidget(sep)
            button = QPushButton(name)
            button.setFlat(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet("QPushButton{color:#2563eb;padding:3px 5px;border:0;background:transparent;} QPushButton:hover{text-decoration:underline;color:#1d4ed8;}")
            button.setEnabled(index != len(self.current_path) - 1)
            button.clicked.connect(lambda checked=False, target=index: self.navigate_to_path(target))
            self.path_layout.addWidget(button)
        self.path_layout.addStretch(1)
        # Defensive guard: early breadcrumb refresh must never crash startup.
        if hasattr(self, "up_btn"):
            self.up_btn.setEnabled(len(self.current_path) > 1)

    def navigate_to_path(self, index):
        if index < 0 or index >= len(self.current_path):
            return
        self.current_path = self.current_path[:index + 1]
        self.current_parent_id = self.current_path[-1][1]
        self.update_breadcrumb()
        self.load_personal_files()

    def render_personal_table(self, items):
        self.update_personal_sort_headers()
        # Sort folders and files independently; folders always stay above files.
        folders = [item for item in items if item.get("type") == "folder"]
        files = [item for item in items if item.get("type") != "folder"]
        reverse = not self.personal_sort_ascending
        folders.sort(
            key=lambda item: self.personal_sort_value(item, self.personal_sort_column),
            reverse=reverse,
        )
        files.sort(
            key=lambda item: self.personal_sort_value(item, self.personal_sort_column),
            reverse=reverse,
        )
        items = folders + files
        self.personal_table.setRowCount(0)
        for item in items:
            row = self.personal_table.rowCount(); self.personal_table.insertRow(row)
            is_folder = item.get("type") == "folder"
            name = str(item.get("name") or "未命名")
            name_item = QTableWidgetItem(("📁  " if is_folder else "📄  ") + name)
            name_item.setData(Qt.ItemDataRole.UserRole, item)
            self.personal_table.setItem(row, 0, name_item)
            self.personal_table.setItem(row, 1, QTableWidgetItem("文件夹" if is_folder else (item.get("fileExtension") or "文件")))
            if is_folder:
                folder_size = item.get("_computed_size")
                if folder_size is None:
                    folder_size = self.folder_size_cache.get(str(item.get("fileId")))
                size_text = self.format_size(folder_size) if folder_size is not None else "点击大小表头统计"
            else:
                size_text = self.format_size(item.get("size"))
            self.personal_table.setItem(row, 2, QTableWidgetItem(size_text))
            self.personal_table.setItem(row, 3, QTableWidgetItem(str(item.get("updatedAt") or "—").replace("T", " ")[:19]))
            if is_folder:
                open_btn = QPushButton("打开文件夹")
                open_btn.clicked.connect(lambda checked=False, data=item: self.open_folder(data))
                self.personal_table.setCellWidget(row, 4, open_btn)
            else:
                copy_btn = QPushButton("解析并复制")
                copy_btn.clicked.connect(lambda checked=False, data=item: self.get_personal_direct(data))
                self.personal_table.setCellWidget(row, 4, copy_btn)
        self.filter_personal_table(self.search_box.text())

    def filter_personal_table(self, text):
        needle = (text or "").strip().casefold()
        for row in range(self.personal_table.rowCount()):
            cell = self.personal_table.item(row, 0)
            data = cell.data(Qt.ItemDataRole.UserRole) if cell else {}
            if not needle:
                matched = True
            else:
                matched = file_matches_search(data or {"name": cell.text() if cell else ""}, needle)
            self.personal_table.setRowHidden(row, not matched)

    def open_folder(self, item):
        self.current_parent_id = str(item.get("fileId"))
        search_path = item.get("_search_path")
        if search_path:
            self.current_path = list(search_path) + [(str(item.get("name") or "文件夹"), self.current_parent_id)]
        else:
            self.current_path.append((str(item.get("name") or "文件夹"), self.current_parent_id))
        self.search_box.clear()
        self.search_results_mode = False
        self.update_breadcrumb()
        self.load_personal_files()

    def go_parent(self):
        if len(self.current_path) <= 1: return
        self.navigate_to_path(len(self.current_path) - 2)

    def personal_row_double_clicked(self, row, column):
        item = self.personal_table.item(row, 0)
        data = item.data(Qt.ItemDataRole.UserRole) if item else None
        if data and data.get("type") == "folder": self.open_folder(data)

    def get_personal_direct(self, item):
        self.personal_status.setText("正在申请直链：" + str(item.get("name") or "文件"))
        self.direct_worker = PersonalDirectWorker(str(item.get("fileId")), str(item.get("name") or "文件"))
        self.direct_worker.done.connect(self.personal_direct_done)
        self.direct_worker.start()

    def personal_direct_done(self, ok, filename, result):
        if not ok:
            self.personal_status.setText("解析失败：" + result)
            QMessageBox.warning(self, "直链解析失败", result)
            return
        QApplication.clipboard().setText(result)
        self.personal_status.setText(f"已复制：{filename}")
        QMessageBox.information(self, "复制成功", f"文件：{filename}\n下载直链已复制到剪贴板。")



def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    w = MainWindow(); w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
