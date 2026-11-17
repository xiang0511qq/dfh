from __future__ import annotations

import json
import os
import re
import sys
import shutil
import subprocess
import threading
import urllib.request
from datetime import datetime

if sys.platform == "win32":
    import winreg
import queue
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
except ImportError:
    sync_playwright = None
    PlaywrightTimeoutError = Exception

APP_TITLE = "多网盘一键上传与分享"
APP_DIR = Path(__file__).resolve().parent
if sys.platform == "win32":
    DATA_DIR = Path(os.environ.get("LOCALAPPDATA", str(APP_DIR))) / "NetdiskUploader"
else:
    DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", str(APP_DIR))) / "NetdiskUploader"
DATA_DIR.mkdir(parents=True, exist_ok=True)
PROFILE_DIR = DATA_DIR / "browser_profiles"
LOGIN_STATE_FILE = DATA_DIR / "login_state.json"
@dataclass(frozen=True)
class Provider:
    key: str
    name: str
    url: str
    share_words: tuple[str, ...]
    upload_words: tuple[str, ...]
    share_domains: tuple[str, ...]
    public_share_words: tuple[str, ...]


PROVIDERS = (
    Provider(
        "quark", "夸克网盘", "https://pan.quark.cn/",
        ("分享",),
        ("上传", "上传文件", "上传文件夹"),
        ("pan.quark.cn", "quark.cn"),
        (),
    ),
    Provider(
        "uc", "UC网盘", "https://drive.uc.cn/",
        ("分享", "公开分享"),
        ("上传", "上传文件", "上传文件夹"),
        ("drive.uc.cn", "uc.cn"),
        ("公开分享", "公开链接"),
    ),
    Provider(
        "xunlei", "迅雷云盘", "https://pan.xunlei.com/",
        ("分享",),
        ("上传", "上传文件", "上传文件夹"),
        ("pan.xunlei.com", "xunlei.com"),
        (),
    ),
    Provider(
        "baidu", "百度网盘", "https://pan.baidu.com/",
        ("分享", "公开分享"),
        ("上传", "上传文件", "上传文件夹"),
        ("pan.baidu.com", "yun.baidu.com", "baidu.com"),
        ("公开分享", "公开链接"),
    ),
)

URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.I)
SHARE_PATH_MARKERS = ("/s/", "/share/", "/link/", "share/init")


def is_official_share_url(value: str, provider: Provider) -> bool:
    lowered = value.lower()
    return any(domain in lowered for domain in provider.share_domains) and any(marker in lowered for marker in SHARE_PATH_MARKERS)


def clean_url(value: str) -> str:
    return value.rstrip(".,;，。；）)]}")


def find_installed_browser() -> Optional[str]:
    candidates: list[Path] = []
    roots = [
        os.environ.get("PROGRAMFILES"),
        os.environ.get("PROGRAMFILES(X86)"),
        os.environ.get("LOCALAPPDATA"),
        os.environ.get("APPDATA"),
    ]
    for root in (value for value in roots if value):
        candidates.extend([
            Path(root) / "Google/Chrome/Application/chrome.exe",
            Path(root) / "Microsoft/Edge/Application/msedge.exe",
            Path(root) / "Chromium/Application/chrome.exe",
            Path(root) / "360/360Chrome/Chrome/Application/360chrome.exe",
            Path(root) / "360Chrome/Chrome/Application/360chrome.exe",
            Path(root) / "360/360Chrome/360chrome.exe",
            Path(root) / "360/360安全浏览器/360se.exe",
            Path(root) / "360安全浏览器/360se.exe",
            Path(root) / "360se6/Application/360se.exe",
            Path(root) / "Tencent/QQBrowser/QQBrowser.exe",
            Path(root) / "QQBrowser/QQBrowser.exe",
            Path(root) / "SogouExplorer/SogouExplorer.exe",
            Path(root) / "2345Explorer/2345Explorer.exe",
            Path(root) / "Maxthon/Application/Maxthon.exe",
        ])

    for command in (
        "chrome.exe", "msedge.exe", "chromium.exe", "360se.exe", "360chrome.exe",
        "360browser.exe", "qqbrowser.exe", "sogouexplorer.exe", "2345explorer.exe",
        "maxthon.exe", "chrome",
    ):
        found = shutil.which(command)
        if found:
            candidates.append(Path(found))

    if sys.platform == "win32":
        registry_locations = (
            (winreg.HKEY_CURRENT_USER, r"Software\Clients\StartMenuInternet"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\Clients\StartMenuInternet"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Clients\StartMenuInternet"),
        )
        for hive, location in registry_locations:
            try:
                with winreg.OpenKey(hive, location) as root_key:
                    count = winreg.QueryInfoKey(root_key)[0]
                    for index in range(count):
                        browser_key = winreg.EnumKey(root_key, index)
                        command_path = location + "\\" + browser_key + r"\shell\open\command"
                        try:
                            with winreg.OpenKey(hive, command_path) as command_key:
                                command = winreg.QueryValueEx(command_key, None)[0]
                            match = re.search(r'"([^"\n]+\.exe)"', command, re.I)
                            if match:
                                candidates.append(Path(match.group(1)))
                            else:
                                match = re.search(r'([^\s]+\.exe)', command, re.I)
                                if match:
                                    candidates.append(Path(match.group(1)))
                        except OSError:
                            continue
            except OSError:
                continue

        search_roots = [Path(value) for value in roots if value]
        browser_names = {"360se.exe", "360chrome.exe", "360browser.exe", "chrome.exe", "msedge.exe", "qqbrowser.exe"}
        for root in search_roots:
            try:
                for path in root.rglob("*.exe"):
                    if path.name.lower() in browser_names:
                        candidates.append(path)
            except (OSError, PermissionError):
                continue

    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        if candidate.is_file():
            return str(candidate)
    return None


def load_login_state() -> dict[str, str]:
    try:
        return json.loads(LOGIN_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_login_state(provider_key: str) -> None:
    state = load_login_state()
    state[provider_key] = datetime.now().isoformat(timespec="seconds")
    try:
        LOGIN_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def clear_login_state(provider_key: str) -> None:
    state = load_login_state()
    if provider_key in state:
        state.pop(provider_key, None)
        try:
            LOGIN_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass


def saved_login_label(provider_key: str) -> str:
    value = load_login_state().get(provider_key)
    if not value:
        return "未登录"
    try:
        saved_at = datetime.fromisoformat(value)
        remaining = 30 - (datetime.now() - saved_at).days
    except ValueError:
        return "需重新登录"
    if remaining <= 0:
        return "需重新登录"
    return f"未验证（剩余{remaining}天）"


def page_looks_logged_in(page) -> bool:
    try:
        current_url = page.url.lower()
        text = page.locator("body").inner_text(timeout=5000)
    except Exception:
        return False
    if not text.strip():
        return False
    if any(token in current_url for token in ("/login", "/signin", "/passport", "/auth")):
        return False
    login_prompts = ("扫码登录", "手机号登录", "立即登录", "登录/注册", "账号登录", "密码登录")
    strong_logged_markers = (
        "退出登录", "我的网盘", "我的文件", "全部文件", "个人中心",
        "传输列表", "新建文件夹", "文件管理", "我的资源",
    )
    if any(marker in text for marker in login_prompts) and not any(
        marker in text for marker in ("退出登录", "个人中心")
    ):
        return False
    return any(marker in text for marker in strong_logged_markers)


def is_360_browser(path: Optional[str]) -> bool:
    return bool(path) and Path(path).name.lower() in {"360se.exe", "360chrome.exe", "360browser.exe"}


def debug_port_for(provider: Provider) -> int:
    return {"quark": 9222, "uc": 9223, "xunlei": 9224, "baidu": 9225}.get(provider.key, 9228)


def wait_for_cdp(port: int) -> str:
    endpoint = f"http://127.0.0.1:{port}"
    for _ in range(30):
        try:
            with urllib.request.urlopen(endpoint + "/json/version", timeout=1) as response:
                if response.status == 200:
                    return endpoint
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("360浏览器远程调试端口未响应")


def visible_text(page) -> str:
    try:
        return page.locator("body").inner_text(timeout=3000)
    except Exception:
        return ""


def click_text(page, words: tuple[str, ...], timeout: int = 2500) -> bool:
    for word in words:
        candidates = [
            page.get_by_role("button", name=re.compile(re.escape(word), re.I)),
            page.get_by_text(word, exact=True),
            page.get_by_text(re.compile(re.escape(word), re.I)),
        ]
        for locator in candidates:
            try:
                if locator.count() and locator.first.is_visible():
                    locator.first.click(timeout=timeout)
                    return True
            except Exception:
                continue
    return False


def click_share_action(page, provider: Provider) -> bool:
    words = provider.share_words + ("分享文件", "公开分享", "公开链接", "分享链接", "生成分享链接", "创建分享链接", "创建链接", "复制链接")
    if click_text(page, words):
        return True
    selectors = (
        '[aria-label*="分享"]', '[title*="分享"]', '[data-testid*="share"]',
        '[class*="share"]', 'button:has-text("分享")',
        '[aria-label*="公开"]', '[title*="公开"]',
    )
    for selector in selectors:
        try:
            locator = page.locator(selector)
            if locator.count() and locator.first.is_visible():
                locator.first.click(timeout=2500)
                return True
        except Exception:
            continue
    if click_text(page, ("更多", "操作", "菜单")):
        return click_text(page, words)
    return False


def set_file_input(page, target: Path) -> bool:
    inputs = page.locator('input[type="file"]')
    try:
        count = inputs.count()
    except Exception:
        count = 0
    for index in range(count):
        locator = inputs.nth(index)
        try:
            locator.set_input_files(str(target), timeout=5000)
            return True
        except Exception:
            continue
    return False


def click_upload_and_choose(page, provider: Provider, target: Path) -> bool:
    for word in provider.upload_words:
        locator = page.get_by_text(word, exact=True)
        try:
            if not locator.count() or not locator.first.is_visible():
                continue
            with page.expect_file_chooser(timeout=3500) as chooser_info:
                locator.first.click(timeout=2500)
            chooser_info.value.set_files(str(target))
            return True
        except Exception:
            continue
    return set_file_input(page, target)


def wait_upload_finished(page, target: Path, log: Callable[[str], None]) -> None:
    name = target.name
    end_at = time.time() + 90
    last_text = ""
    while time.time() < end_at:
        text = visible_text(page)
        if text != last_text:
            last_text = text
            if any(token in text for token in ("上传完成", "上传成功", "已完成", "完成上传")):
                log("检测到上传完成")
                return
        if name and name in text and any(token in text for token in ("100%", "完成", "成功")):
            return
        time.sleep(1)
    log("上传页面超过等待时间，继续尝试获取分享链接")


def choose_uploaded_item(page, target: Path) -> bool:
    try:
        page.reload(wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1200)
    except Exception:
        pass
    names = [target.name, target.stem]
    patterns = [re.compile(re.escape(name), re.I) for name in names if name]
    for _ in range(40):
        for pattern in patterns:
            try:
                rows = page.get_by_role("row").filter(has_text=pattern)
                if rows.count() and rows.first.is_visible():
                    rows.first.click(timeout=3000)
                    return True
            except Exception:
                pass
            try:
                locator = page.get_by_text(pattern)
                if locator.count() and locator.first.is_visible():
                    locator.first.click(timeout=3000)
                    return True
            except Exception:
                continue
        try:
            body = page.locator("body").inner_text(timeout=1500)
            normalized = re.sub(r"\s+", "", body).lower()
            if any(re.sub(r"\s+", "", name).lower() in normalized for name in names if name):
                page.keyboard.press("Escape")
        except Exception:
            pass
        try:
            page.wait_for_timeout(500)
        except Exception:
            time.sleep(0.5)
    return False


def extract_share_link(page, provider: Provider) -> Optional[str]:
    candidates: list[str] = []
    try:
        candidates.append(page.locator("body").inner_text(timeout=5000))
    except Exception:
        pass
    for selector in ("input", "textarea", "[contenteditable='true']", "[data-clipboard-text]", "[data-url]", "[data-link]"):
        try:
            locator = page.locator(selector)
            for item in locator.all():
                for attr in ("value", "data-clipboard-text", "data-url", "data-link", "title", "aria-label"):
                    value = item.get_attribute(attr, timeout=1000)
                    if value:
                        candidates.append(value)
                text = item.inner_text(timeout=1000)
                if text:
                    candidates.append(text)
        except Exception:
            continue
    try:
        candidates.append(page.evaluate("navigator.clipboard.readText()"))
    except Exception:
        pass
    url_pattern = re.compile(r"(?:https?://|www\.)[^\s\"'<>，。；）)]+", re.I)
    for text in candidates:
        if not isinstance(text, str):
            continue
        for candidate in url_pattern.findall(text):
            candidate = clean_url(candidate if candidate.startswith("http") else "https://" + candidate)
            if is_official_share_url(candidate, provider):
                return candidate
    return None


def create_share_link(page, provider: Provider, target: Path, log: Callable[[str], None]) -> Optional[str]:
    if not choose_uploaded_item(page, target):
        log("未定位到已上传文件，请在网页中手动选中后重试")
        return None
    if not click_share_action(page, provider):
        log("未找到分享按钮，请在网页中手动点击分享")
        return None
    time.sleep(1.2)
    # Some providers require a second confirmation inside a dialog.
    # UC and Baidu expose a separate public-share choice after opening Share.
    if provider.public_share_words:
        click_text(page, provider.public_share_words, timeout=1800)
    click_text(page, ("创建分享链接", "创建链接", "生成链接", "确定", "确认"), timeout=2000)
    time.sleep(1.2)
    try:
        page.wait_for_timeout(1200)
    except Exception:
        pass
    # Clear stale clipboard data, then click the provider's copy action.
    try:
        page.evaluate("navigator.clipboard.writeText('')")
    except Exception:
        pass
    if click_text(page, ("复制链接", "复制分享链接", "复制"), timeout=1800):
        time.sleep(0.4)
        link = extract_share_link(page, provider)
        if link:
            return link
    return extract_share_link(page, provider)


class BrowserWorker:
    """Own all Playwright objects on one dedicated thread."""

    def __init__(self, log: Callable[[str], None]):
        self.log = log
        self.commands: queue.Queue = queue.Queue()
        self.ready = threading.Event()
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        self.ready.wait(timeout=15)

    def _run(self):
        self.playwright = None
        self.contexts = {}
        self.pages = {}
        self.browser_checked = False
        self.browser_executable = None
        self.browser_mode = "system"
        self.remote_browsers = {}
        try:
            if sync_playwright is None:
                self.ready.set()
                return
            PROFILE_DIR.mkdir(parents=True, exist_ok=True)
            self.playwright = sync_playwright().start()
        finally:
            self.ready.set()
        while True:
            command = self.commands.get()
            if command is None:
                break
            method, args, result_queue = command
            try:
                result_queue.put((True, getattr(self, f"_do_{method}")(*args)))
            except Exception as exc:
                result_queue.put((False, exc))
        for browser in list(self.remote_browsers.values()):
            try:
                browser.close()
            except Exception:
                pass
        for context in list(self.contexts.values()):
            try:
                context.close()
            except Exception:
                pass
        if self.playwright:
            try:
                self.playwright.stop()
            except Exception:
                pass
        self.stopped.set()

    def _call(self, method: str, *args):
        if sync_playwright is None:
            raise RuntimeError("未安装 Playwright，请先运行 pip install -r requirements.txt")
        result_queue = queue.Queue(maxsize=1)
        self.commands.put((method, args, result_queue))
        ok, value = result_queue.get()
        if not ok:
            raise value
        return value

    def _do_open_provider(self, provider: Provider):
        profile = PROFILE_DIR / provider.key
        profile.mkdir(parents=True, exist_ok=True)
        if not self.browser_checked:
            self.browser_executable = find_installed_browser()
            self.browser_checked = True
        browser_executable = self.browser_executable
        if browser_executable:
            self.log(f"使用已安装浏览器：{Path(browser_executable).name}")
        else:
            self.log("未找到可用的系统浏览器，请先安装 Chrome、Edge、360、QQ、搜狗、2345或遨游浏览器")
        # 360 安全浏览器对 Playwright 直启兼容性不稳定，改用它的可见远程调试窗口。
        if is_360_browser(browser_executable):
            port = debug_port_for(provider)
            launch_args = [
                browser_executable,
                f"--remote-debugging-port={port}",
                f"--user-data-dir={profile}",
                "--no-first-run",
                "--no-default-browser-check",
                "--new-window",
                "--start-maximized",
                "--remote-allow-origins=*",
                provider.url,
            ]
            try:
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
                subprocess.Popen(launch_args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=creationflags)
                endpoint = wait_for_cdp(port)
                browser = self.playwright.chromium.connect_over_cdp(endpoint)
                context = browser.contexts[0] if browser.contexts else browser.new_context()
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(provider.url, wait_until="domcontentloaded", timeout=30000)
                page.bring_to_front()
                self.remote_browsers[provider.key] = browser
                self.contexts[provider.key] = context
                self.pages[provider.key] = page
                return page
            except Exception as exc:
                raise RuntimeError(f"360浏览器登录窗口打开失败：{exc}") from exc
        launch_options = {
            "headless": False,
            "accept_downloads": True,
            "permissions": ["clipboard-read", "clipboard-write"],
            "viewport": {"width": 1440, "height": 900},
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--start-maximized",
                "--no-first-run",
                "--no-default-browser-check",
            ],
        }
        if browser_executable:
            launch_options["executable_path"] = browser_executable
        context = self.playwright.chromium.launch_persistent_context(str(profile), **launch_options)
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(provider.url, wait_until="domcontentloaded", timeout=30000)
        page.bring_to_front()
        self.contexts[provider.key] = context
        self.pages[provider.key] = page
        return page

    def _switch_to_background(self, provider: Provider) -> None:
        return None

    def _do_open_background_provider(self, provider: Provider):
        # Keep using the visible persistent system-browser page so uploads and shares
        # operate on the same authenticated session the user can see.
        return self._do_open_provider(provider)

    def _do_open_background_selected(self, providers: list[Provider]) -> None:
        for provider in providers:
            try:
                self._do_open_background_provider(provider)
                self.log(f"{provider.name} 已使用保存的后台登录会话")
            except Exception as exc:
                self.log(f"{provider.name} 后台会话打开失败：{exc}")

    def _do_open_selected(self, providers: list[Provider]) -> None:
        for provider in providers:
            try:
                self.log(f"打开 {provider.name} 登录窗口")
                page = self._do_open_provider(provider)
                self.log(f"请在 {provider.name} 窗口完成登录，登录成功后自动继续下一个网盘")
                logged_in = False
                for _ in range(60):
                    if page_looks_logged_in(page):
                        save_login_state(provider.key)
                        logged_in = True
                        self.log(f"{provider.name} 已登录，登录状态已保存")
                        break
                    time.sleep(2)
                if not logged_in:
                    self.log(f"{provider.name} 等待登录超时，请点击“检测登录状态”后继续")
            except Exception as exc:
                self.log(f"{provider.name} 打开失败：{exc}")

    def _do_upload_selected(self, providers: list[Provider], target: Path, create_links: bool = True) -> dict[str, str]:
        results: dict[str, str] = {}
        for provider in providers:
            page = self.pages.get(provider.key)
            if page is None or page.is_closed():
                try:
                    page = self._do_open_background_provider(provider)
                except Exception as exc:
                    self.log(f"{provider.name} 打开失败：{exc}")
                    continue
            try:
                self.log(f"[{provider.name}] 开始上传 {target.name}")
                if not click_upload_and_choose(page, provider, target):
                    self.log(f"[{provider.name}] 未找到上传入口，请在网页中手动点击上传")
                    continue
                wait_upload_finished(page, target, lambda msg, p=provider: self.log(f"[{p.name}] {msg}"))
                if not create_links:
                    results[provider.name] = "已上传"
                    self.log(f"[{provider.name}] 上传完成")
                    continue
                link = create_share_link(page, provider, target, lambda msg, p=provider: self.log(f"[{p.name}] {msg}"))
                if link:
                    results[provider.name] = link
                    self.log(f"[{provider.name}] 分享链接已获取")
                else:
                    self.log(f"[{provider.name}] 暂未获取到链接")
            except Exception as exc:
                self.log(f"[{provider.name}] 处理失败：{exc}")
        return results

    def open_selected(self, providers: list[Provider]) -> None:
        self._call("open_selected", providers)

    def open_background_selected(self, providers: list[Provider]) -> None:
        self._call("open_background_selected", providers)

    def _do_check_login(self, providers: list[Provider]) -> dict[str, bool]:
        result = {}
        for provider in providers:
            page = self.pages.get(provider.key)
            logged_in = bool(page and not page.is_closed() and page_looks_logged_in(page))
            result[provider.key] = logged_in
            if logged_in:
                save_login_state(provider.key)
            else:
                clear_login_state(provider.key)
        return result

    def check_login(self, providers: list[Provider]) -> dict[str, bool]:
        return self._call("check_login", providers)

    def upload_selected(self, providers: list[Provider], target: Path) -> dict[str, str]:
        return self._call("upload_selected", providers, target, True)

    def upload_only(self, providers: list[Provider], target: Path) -> dict[str, str]:
        return self._call("upload_selected", providers, target, False)

    def close(self) -> None:
        if self.thread.is_alive():
            self.commands.put(None)
            self.stopped.wait(timeout=10)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("980x700")
        self.minsize(820, 560)
        self.worker: Optional[BrowserWorker] = None
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.path_var = tk.StringVar()
        self.status_var = tk.StringVar(value="请选择文件或文件夹")
        self.check_vars: dict[str, tk.BooleanVar] = {}
        self.login_status_vars: dict[str, tk.StringVar] = {}
        self.login_window: Optional[tk.Toplevel] = None
        self.login_window_status_vars: dict[str, tk.StringVar] = {}
        self._build_ui()
        self.after(150, self._drain_events)
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _build_ui(self):
        root = ttk.Frame(self, padding=16)
        root.pack(fill="both", expand=True)

        ttk.Label(root, text=APP_TITLE, font=("Microsoft YaHei UI", 18, "bold")).pack(anchor="w")
        ttk.Label(root, text="选择本地文件或文件夹，打开已登录网盘后批量上传并生成分享链接", foreground="#555").pack(anchor="w", pady=(3, 14))

        path_box = ttk.LabelFrame(root, text="本地内容", padding=10)
        path_box.pack(fill="x")
        ttk.Entry(path_box, textvariable=self.path_var).pack(side="left", fill="x", expand=True)
        ttk.Button(path_box, text="选择文件", command=self.choose_file).pack(side="left", padx=(8, 0))
        ttk.Button(path_box, text="选择文件夹", command=self.choose_folder).pack(side="left", padx=(8, 0))

        providers_box = ttk.LabelFrame(root, text="网盘账号窗口", padding=10)
        providers_box.pack(fill="x", pady=(12, 0))
        for provider in PROVIDERS:
            var = tk.BooleanVar(value=True)
            self.check_vars[provider.key] = var
            item = ttk.Frame(providers_box)
            item.pack(side="left", padx=(0, 12))
            ttk.Checkbutton(item, text=provider.name, variable=var).pack(side="left")
            status_var = tk.StringVar(value=saved_login_label(provider.key))
            self.login_status_vars[provider.key] = status_var
            ttk.Label(item, textvariable=status_var, foreground="#16803c").pack(side="left", padx=(4, 0))
        self.login_btn = ttk.Button(providers_box, text="打开并登录", command=self.open_login)
        self.login_btn.pack(side="right")
        self.check_login_btn = ttk.Button(providers_box, text="检测登录状态", command=self.refresh_login)
        self.check_login_btn.pack(side="right", padx=(0, 8))

        action_box = ttk.Frame(root)
        action_box.pack(fill="x", pady=12)
        self.upload_btn = ttk.Button(action_box, text="上传文件/文件夹", command=self.start_upload_only)
        self.upload_btn.pack(side="left")
        self.share_btn = ttk.Button(action_box, text="上传并生成分享链接", command=self.start_upload)
        self.share_btn.pack(side="left", padx=8)
        ttk.Button(action_box, text="复制全部链接", command=self.copy_links).pack(side="left", padx=8)
        ttk.Button(action_box, text="清空结果", command=lambda: self.links.delete("1.0", "end")).pack(side="left")
        ttk.Label(action_box, textvariable=self.status_var).pack(side="right")

        links_box = ttk.LabelFrame(root, text="分享链接（可直接复制）", padding=8)
        links_box.pack(fill="both", expand=True)
        self.links = tk.Text(links_box, height=10, wrap="word", font=("Consolas", 11))
        self.links.pack(side="left", fill="both", expand=True)
        links_scroll = ttk.Scrollbar(links_box, command=self.links.yview)
        links_scroll.pack(side="right", fill="y")
        self.links.configure(yscrollcommand=links_scroll.set)

        log_box = ttk.LabelFrame(root, text="运行日志", padding=8)
        log_box.pack(fill="both", expand=True, pady=(12, 0))
        self.log_text = tk.Text(log_box, height=8, wrap="word", state="disabled", font=("Consolas", 10))
        self.log_text.pack(side="left", fill="both", expand=True)
        log_scroll = ttk.Scrollbar(log_box, command=self.log_text.yview)
        log_scroll.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=log_scroll.set)

        ttk.Label(root, text="首次使用请在各网盘网页窗口中手动登录。登录状态保存在本程序目录的 browser_profiles 文件夹。", foreground="#666").pack(anchor="w", pady=(8, 0))

    def auto_check_saved_sessions(self):
        providers = [p for p in PROVIDERS if load_login_state().get(p.key)]
        if not providers:
            return
        self._set_busy(True, "正在后台验证保存的登录状态")
        threading.Thread(target=self._auto_check_worker, args=(providers,), daemon=True).start()

    def _auto_check_worker(self, providers: list[Provider]):
        try:
            if self.worker is None:
                self.worker = BrowserWorker(self._log)
            self.worker.open_background_selected(providers)
            results = self.worker.check_login(providers)
            self.events.put(("login_status", results))
            self.events.put(("status", "保存的登录状态已验证"))
        except Exception as exc:
            self.events.put(("error", str(exc)))
        finally:
            self.events.put(("busy", False))

    def selected_providers(self) -> list[Provider]:
        return [p for p in PROVIDERS if self.check_vars[p.key].get()]

    def choose_file(self):
        path = filedialog.askopenfilename(title="选择要上传的文件")
        if path:
            self.path_var.set(path)
            self.status_var.set(f"已选择：{Path(path).name}")

    def choose_folder(self):
        path = filedialog.askdirectory(title="选择要上传的文件夹")
        if path:
            self.path_var.set(path)
            self.status_var.set(f"已选择文件夹：{Path(path).name}")

    def _validate(self) -> tuple[Optional[Path], list[Provider]]:
        raw = self.path_var.get().strip()
        target = Path(raw) if raw else None
        providers = self.selected_providers()
        if not target or not target.exists():
            messagebox.showwarning("缺少文件", "请先选择存在的文件或文件夹。")
            return None, []
        if not providers:
            messagebox.showwarning("缺少网盘", "请至少勾选一个网盘。")
            return None, []
        return target, providers

    def _show_login_window(self, providers: list[Provider]):
        if self.login_window is not None and self.login_window.winfo_exists():
            self.login_window.lift()
            return
        self.login_window = tk.Toplevel(self)
        self.login_window.title("网盘登录窗口")
        self.login_window.geometry("560x310")
        self.login_window.resizable(False, False)
        ttk.Label(self.login_window, text="按顺序登录四个网盘", font=("Microsoft YaHei UI", 15, "bold")).pack(anchor="w", padx=18, pady=(16, 4))
        ttk.Label(self.login_window, text="登录页面由软件调用的浏览器窗口显示，完成一个后自动进入下一个。", foreground="#666").pack(anchor="w", padx=18, pady=(0, 12))
        box = ttk.Frame(self.login_window)
        box.pack(fill="x", padx=18)
        self.login_window_status_vars.clear()
        for provider in providers:
            row = ttk.Frame(box)
            row.pack(fill="x", pady=4)
            ttk.Label(row, text=provider.name, width=12).pack(side="left")
            status_var = tk.StringVar(value="等待登录")
            self.login_window_status_vars[provider.key] = status_var
            ttk.Label(row, textvariable=status_var, foreground="#16803c").pack(side="left")
        ttk.Button(self.login_window, text="关闭窗口", command=self.login_window.destroy).pack(side="right", padx=18, pady=18)

    def open_login(self):
        providers = self.selected_providers()
        if not providers:
            messagebox.showwarning("缺少网盘", "请至少勾选一个网盘。")
            return
        self._show_login_window(providers)
        self._set_busy(True, "正在按顺序打开登录窗口")
        threading.Thread(target=self._login_worker, args=(providers,), daemon=True).start()

    def _login_worker(self, providers: list[Provider]):
        try:
            self.worker = BrowserWorker(self._log)
            self.worker.open_selected(providers)
            login_results = self.worker.check_login(providers)
            self.events.put(("login_status", login_results))
            self.events.put(("status", "网盘窗口已打开。登录完成后点击“检测登录状态”"))
        except Exception as exc:
            self.events.put(("error", str(exc)))
        finally:
            self.events.put(("busy", False))

    def refresh_login(self):
        providers = self.selected_providers()
        if not providers:
            messagebox.showwarning("缺少网盘", "请至少勾选一个网盘。")
            return
        self._set_busy(True, "正在检测已保存登录状态")
        threading.Thread(target=self._refresh_login_worker, args=(providers,), daemon=True).start()

    def _refresh_login_worker(self, providers: list[Provider]):
        try:
            if self.worker is None:
                self.worker = BrowserWorker(self._log)
                self.worker.open_selected(providers)
            results = self.worker.check_login(providers)
            self.events.put(("login_status", results))
            self.events.put(("status", "登录状态已更新"))
        except Exception as exc:
            self.events.put(("error", str(exc)))
        finally:
            self.events.put(("busy", False))

    def _apply_login_status(self, results: dict[str, bool]):
        for provider in PROVIDERS:
            if results.get(provider.key):
                label = f"已登录（剩余{30}天）"
                self.login_status_vars[provider.key].set(label)
                if provider.key in self.login_window_status_vars:
                    self.login_window_status_vars[provider.key].set(label)
            elif provider.key in results:
                self.login_status_vars[provider.key].set("未登录")
                if provider.key in self.login_window_status_vars:
                    self.login_window_status_vars[provider.key].set("未登录")

    def start_upload(self):
        target, providers = self._validate()
        if target is None:
            return
        self.links.delete("1.0", "end")
        self._set_busy(True, "正在上传并生成分享链接，请稍候")
        threading.Thread(target=self._upload_worker, args=(providers, target, True), daemon=True).start()

    def start_upload_only(self):
        target, providers = self._validate()
        if target is None:
            return
        self.links.delete("1.0", "end")
        self._set_busy(True, "正在上传文件或文件夹，请稍候")
        threading.Thread(target=self._upload_worker, args=(providers, target, False), daemon=True).start()

    def _upload_worker(self, providers: list[Provider], target: Path, create_links: bool):
        try:
            if self.worker is None:
                self.worker = BrowserWorker(self._log)
                self.worker.open_selected(providers)
            results = self.worker.upload_selected(providers, target) if create_links else self.worker.upload_only(providers, target)
            self.events.put(("results", results))
            status = "上传并生成链接完成" if create_links else "文件或文件夹上传完成"
            self.events.put(("status", status))
        except Exception as exc:
            self.events.put(("error", str(exc)))
        finally:
            self.events.put(("busy", False))

    def _log(self, message: str):
        self.events.put(("log", message))

    def _drain_events(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self.log_text.configure(state="normal")
                    self.log_text.insert("end", f"[{time.strftime('%H:%M:%S')}] {payload}\n")
                    self.log_text.see("end")
                    self.log_text.configure(state="disabled")
                elif kind == "results":
                    for name, link in payload.items():
                        self.links.insert("end", f"{name}\n{link}\n\n")
                elif kind == "status":
                    self.status_var.set(str(payload))
                elif kind == "login_status":
                    self._apply_login_status(payload)
                elif kind == "busy":
                    self._set_busy(bool(payload))
                elif kind == "error":
                    self._set_busy(False)
                    messagebox.showerror("运行失败", str(payload))
        except queue.Empty:
            pass
        self.after(150, self._drain_events)

    def _set_busy(self, busy: bool, status: Optional[str] = None):
        self.upload_btn.configure(state="disabled" if busy else "normal")
        self.share_btn.configure(state="disabled" if busy else "normal")
        self.login_btn.configure(state="disabled" if busy else "normal")
        self.check_login_btn.configure(state="disabled" if busy else "normal")
        if status:
            self.status_var.set(status)

    def copy_links(self):
        value = self.links.get("1.0", "end").strip()
        if not value:
            messagebox.showinfo("没有链接", "当前还没有可复制的分享链接。")
            return
        self.clipboard_clear()
        self.clipboard_append(value)
        self.update()
        self.status_var.set("分享链接已复制到剪贴板")

    def _close(self):
        if self.worker:
            self.worker.close()
        self.destroy()


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
