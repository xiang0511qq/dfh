from __future__ import annotations

import os
import re
import sys
import shutil
import threading
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
PROFILE_DIR = APP_DIR / "browser_profiles"


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
    candidates = []
    for env_name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        root = os.environ.get(env_name)
        if not root:
            continue
        candidates.extend([
            Path(root) / "Google/Chrome/Application/chrome.exe",
            Path(root) / "Microsoft/Edge/Application/msedge.exe",
            Path(root) / "Chromium/Application/chrome.exe",
            Path(root) / "360/360Chrome/Chrome/Application/360chrome.exe",
            Path(root) / "360Chrome/Chrome/Application/360chrome.exe",
            Path(root) / "360/360Chrome/360chrome.exe",
            Path(root) / "Tencent/QQBrowser/QQBrowser.exe",
            Path(root) / "QQBrowser/QQBrowser.exe",
            Path(root) / "SogouExplorer/SogouExplorer.exe",
            Path(root) / "2345Explorer/2345Explorer.exe",
            Path(root) / "Maxthon/Application/Maxthon.exe",
        ])
    for command in (
        "chrome.exe", "msedge.exe", "chromium.exe", "360chrome.exe",
        "qqbrowser.exe", "sogouexplorer.exe", "2345explorer.exe",
        "maxthon.exe", "chrome",
    ):
        found = shutil.which(command)
        if found:
            candidates.append(Path(found))
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


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
    # Selecting the visible filename works across the four providers and lets the user
    # keep the normal provider UI available if the layout changes.
    for locator in (
        page.get_by_text(target.name, exact=True),
        page.get_by_text(re.compile(re.escape(target.name), re.I)),
    ):
        try:
            if locator.count() and locator.first.is_visible():
                locator.first.click(timeout=3000)
                return True
        except Exception:
            continue
    return False


def extract_share_link(page, provider: Provider) -> Optional[str]:
    """Read the official provider link, preferring the clipboard from “复制链接”."""
    try:
        value = page.evaluate("navigator.clipboard.readText()")
        if isinstance(value, str):
            for candidate in URL_RE.findall(value):
                candidate = clean_url(candidate)
                if is_official_share_url(candidate, provider):
                    return candidate
    except Exception:
        pass
    text = visible_text(page)
    for candidate in URL_RE.findall(text):
        candidate = clean_url(candidate)
        if is_official_share_url(candidate, provider):
            return candidate
    try:
        hrefs = page.locator("a").evaluate_all("els => els.map(e => e.href).filter(Boolean)")
        for href in reversed(hrefs):
            if isinstance(href, str) and href.startswith("http"):
                if is_official_share_url(href, provider):
                    return clean_url(href)
    except Exception:
        pass
    return None


def create_share_link(page, provider: Provider, target: Path, log: Callable[[str], None]) -> Optional[str]:
    if not choose_uploaded_item(page, target):
        log("未定位到已上传文件，请在网页中手动选中后重试")
        return None
    if not click_text(page, provider.share_words):
        log("未找到分享按钮，请在网页中手动点击分享")
        return None
    time.sleep(1.2)
    # Some providers require a second confirmation inside a dialog.
    # UC and Baidu expose a separate public-share choice after opening Share.
    if provider.public_share_words:
        click_text(page, provider.public_share_words, timeout=1800)
    click_text(page, ("创建链接", "生成链接", "确定", "确认"), timeout=1500)
    time.sleep(1.2)
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
        launch_options = {
            "headless": False,
            "accept_downloads": True,
            "permissions": ["clipboard-read", "clipboard-write"],
            "viewport": {"width": 1440, "height": 900},
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        browser_executable = find_installed_browser()
        if browser_executable:
            self.log(f"使用已安装浏览器：{Path(browser_executable).name}")
            launch_options["executable_path"] = browser_executable
        else:
            self.log("未找到可用的 Chromium 浏览器，请先安装 Chrome、Edge、360、QQ、搜狗、2345或遨游浏览器")
        context = self.playwright.chromium.launch_persistent_context(str(profile), **launch_options)
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(provider.url, wait_until="domcontentloaded", timeout=30000)
        self.contexts[provider.key] = context
        self.pages[provider.key] = page
        return page

    def _do_open_selected(self, providers: list[Provider]) -> None:
        for provider in providers:
            try:
                self.log(f"打开 {provider.name}")
                self._do_open_provider(provider)
            except Exception as exc:
                self.log(f"{provider.name} 打开失败：{exc}")

    def _do_upload_selected(self, providers: list[Provider], target: Path) -> dict[str, str]:
        results: dict[str, str] = {}
        for provider in providers:
            page = self.pages.get(provider.key)
            if page is None or page.is_closed():
                try:
                    page = self._do_open_provider(provider)
                except Exception as exc:
                    self.log(f"{provider.name} 打开失败：{exc}")
                    continue
            try:
                self.log(f"[{provider.name}] 开始上传 {target.name}")
                if not click_upload_and_choose(page, provider, target):
                    self.log(f"[{provider.name}] 未找到上传入口，请在网页中手动点击上传")
                    continue
                wait_upload_finished(page, target, lambda msg, p=provider: self.log(f"[{p.name}] {msg}"))
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

    def upload_selected(self, providers: list[Provider], target: Path) -> dict[str, str]:
        return self._call("upload_selected", providers, target)

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
            ttk.Checkbutton(providers_box, text=provider.name, variable=var).pack(side="left", padx=(0, 18))
        ttk.Button(providers_box, text="打开并登录", command=self.open_login).pack(side="right")

        action_box = ttk.Frame(root)
        action_box.pack(fill="x", pady=12)
        self.upload_btn = ttk.Button(action_box, text="一键上传并生成分享链接", command=self.start_upload)
        self.upload_btn.pack(side="left")
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

    def open_login(self):
        providers = self.selected_providers()
        if not providers:
            messagebox.showwarning("缺少网盘", "请至少勾选一个网盘。")
            return
        self._set_busy(True, "正在打开网盘窗口")
        threading.Thread(target=self._login_worker, args=(providers,), daemon=True).start()

    def _login_worker(self, providers: list[Provider]):
        try:
            self.worker = BrowserWorker(self._log)
            self.worker.open_selected(providers)
            self.events.put(("status", "网盘窗口已打开，可手动登录"))
        except Exception as exc:
            self.events.put(("error", str(exc)))
        finally:
            self.events.put(("busy", False))

    def start_upload(self):
        target, providers = self._validate()
        if target is None:
            return
        self.links.delete("1.0", "end")
        self._set_busy(True, "正在上传，请稍候")
        threading.Thread(target=self._upload_worker, args=(providers, target), daemon=True).start()

    def _upload_worker(self, providers: list[Provider], target: Path):
        try:
            if self.worker is None:
                self.worker = BrowserWorker(self._log)
            results = self.worker.upload_selected(providers, target)
            self.events.put(("results", results))
            self.events.put(("status", f"完成，获取到 {len(results)} 个分享链接"))
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
