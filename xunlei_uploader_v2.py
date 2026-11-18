import json
import os
import queue
import threading
import uuid
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError


API = "https://api-pan.xunlei.com/drive/v1/files"


def http_json(url, method="GET", body=None, token="", device_id=""):
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token.replace("Bearer ", "").strip()
    if device_id:
        headers["x-device-id"] = device_id.strip()
    headers["x-client-id"] = "XW5SkOhLDjn0ZP7J"
    headers["x-client-version"] = "1.0.0"
    req = urlrequest.Request(url, data=data, headers=headers, method=method)
    try:
        with urlrequest.urlopen(req, timeout=60) as response:
            raw = response.read().decode("utf-8", "replace")
            return json.loads(raw) if raw else {}
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"接口 HTTP {exc.code}: {detail[:500]}") from exc
    except URLError as exc:
        raise RuntimeError(f"网络连接失败: {exc.reason}") from exc


class UploadError(RuntimeError):
    pass


class Uploader:
    def __init__(self, token, parent_id, device_id, log, progress):
        self.token = token.replace("Bearer ", "").strip()
        self.parent_id = parent_id.strip()
        self.device_id = device_id.strip()
        self.log = log
        self.progress = progress

    def create_remote_folder(self, name, parent_id):
        payload = {"kind": "drive#folder", "name": name, "parent_id": parent_id}
        result = http_json(API, "POST", payload, self.token, self.device_id)
        return result.get("file", result).get("id", "")

    def create_upload(self, path, parent_id):
        size = os.path.getsize(path)
        payload = {
            "kind": "drive#file", "parent_id": parent_id,
            "name": os.path.basename(path), "size": size,
            "upload_type": "UPLOAD_TYPE_RESUMABLE",
        }
        result = http_json(API, "POST", payload, self.token, self.device_id)
        if result.get("upload_type") != "UPLOAD_TYPE_RESUMABLE":
            raise UploadError("服务器没有返回可分片上传参数")
        return result, size

    def multipart_upload(self, path, response, size):
        # The resumable response supplies temporary S3-compatible credentials.
        # boto3 performs the multipart create/upload/complete sequence.
        try:
            import boto3
            from boto3.s3.transfer import TransferConfig
        except ImportError as exc:
            raise UploadError("缺少 boto3 依赖，请先运行 pip install boto3") from exc
        params = response["resumable"]["params"]
        endpoint = params["endpoint"]
        bucket = params["bucket"]
        if endpoint.startswith(bucket + "."):
            endpoint = "https://" + endpoint[len(bucket) + 1:]
        if not endpoint.startswith("http"):
            endpoint = "https://" + endpoint
        client = boto3.client(
            "s3", region_name="xunlei", endpoint_url=endpoint,
            aws_access_key_id=params["access_key_id"],
            aws_secret_access_key=params["access_key_secret"],
            aws_session_token=params.get("security_token"),
        )
        config = TransferConfig(
            multipart_threshold=8 * 1024 * 1024,
            multipart_chunksize=8 * 1024 * 1024,
            max_concurrency=3, use_threads=True,
        )
        sent = 0

        def callback(count):
            nonlocal sent
            sent += count
            self.progress(sent, size)

        with open(path, "rb") as stream:
            client.upload_fileobj(stream, bucket, params["key"],
                                  ExtraArgs={"ContentType": "application/octet-stream"},
                                  Config=config, Callback=callback)

    def upload_file(self, path, parent_id):
        self.log(f"预创建文件: {os.path.basename(path)}")
        response, size = self.create_upload(path, parent_id)
        self.log(f"开始分片上传: {size:,} 字节")
        self.multipart_upload(path, response, size)
        self.log("分片上传完成，服务器正在合并文件")
        self.progress(size, size)

    def upload_path(self, selected):
        if os.path.isfile(selected):
            self.upload_file(selected, self.parent_id)
            return
        root_name = os.path.basename(os.path.normpath(selected))
        root_id = self.create_remote_folder(root_name, self.parent_id)
        if not root_id:
            raise UploadError("创建目标文件夹失败")
        files = []
        for base, _, names in os.walk(selected):
            for name in names:
                files.append(os.path.join(base, name))
        total = sum(os.path.getsize(p) for p in files)
        done = 0
        for path in files:
            rel = os.path.relpath(os.path.dirname(path), selected)
            target = root_id
            if rel != ".":
                for part in rel.split(os.sep):
                    target = self.create_remote_folder(part, target)
            self.upload_file(path, target)
            done += os.path.getsize(path)
            self.progress(done, total or 1)


class App:
    def __init__(self, root):
        self.root = root
        self.root.title("迅雷网盘自动分片上传工具")
        self.root.geometry("760x560")
        self.events = queue.Queue()
        self.token = tk.StringVar()
        self.parent = tk.StringVar()
        self.device_id = tk.StringVar(value=str(uuid.uuid4()))
        self.path = tk.StringVar()
        self.build_ui()
        self.root.after(100, self.flush_events)

    def build_ui(self):
        frame = ttk.Frame(self.root, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Bearer Token").grid(row=0, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self.token, show="*", width=78).grid(row=0, column=1, columnspan=2, sticky="ew", pady=4)
        ttk.Label(frame, text="设备 ID").grid(row=1, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self.device_id, width=55).grid(row=1, column=1, columnspan=2, sticky="ew", pady=4)
        ttk.Label(frame, text="目标文件夹 ID").grid(row=2, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self.parent, width=55).grid(row=2, column=1, sticky="ew", pady=4)
        ttk.Label(frame, text="根目录可留空").grid(row=2, column=2, sticky="w")
        ttk.Label(frame, text="上传内容").grid(row=3, column=0, sticky="w")
        ttk.Entry(frame, textvariable=self.path, width=55).grid(row=3, column=1, sticky="ew", pady=4)
        ttk.Button(frame, text="选择文件", command=self.choose_file).grid(row=3, column=2, padx=4)
        ttk.Button(frame, text="选择文件夹", command=self.choose_folder).grid(row=4, column=2, padx=4)
        self.start = ttk.Button(frame, text="开始自动上传", command=self.start_upload)
        self.start.grid(row=4, column=1, sticky="w", pady=8)
        self.bar = ttk.Progressbar(frame, mode="determinate", maximum=100)
        self.bar.grid(row=5, column=0, columnspan=3, sticky="ew", pady=8)
        self.status = ttk.Label(frame, text="等待上传")
        self.status.grid(row=6, column=0, columnspan=3, sticky="w")
        ttk.Label(frame, text="日志").grid(row=7, column=0, sticky="w", pady=(10, 2))
        self.log_box = tk.Text(frame, height=20, state="disabled")
        self.log_box.grid(row=8, column=0, columnspan=3, sticky="nsew")
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(8, weight=1)

    def choose_file(self):
        value = filedialog.askopenfilename()
        if value:
            self.path.set(value)

    def choose_folder(self):
        value = filedialog.askdirectory()
        if value:
            self.path.set(value)

    def write_log(self, message):
        self.events.put(("log", message))

    def update_progress(self, done, total):
        self.events.put(("progress", (done, total)))

    def start_upload(self):
        token = self.token.get().strip()
        path = self.path.get().strip()
        if not token or not path or not os.path.exists(path):
            messagebox.showwarning("信息不完整", "请填写 Token 并选择文件或文件夹")
            return
        self.start.configure(state="disabled")
        self.bar["value"] = 0
        threading.Thread(target=self.worker, args=(token, path), daemon=True).start()

    def worker(self, token, path):
        try:
            uploader = Uploader(token, self.parent.get(), self.device_id.get(), self.write_log, self.update_progress)
            uploader.upload_path(path)
            self.events.put(("done", "上传完成"))
        except Exception as exc:
            self.events.put(("error", str(exc)))

    def flush_events(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "log":
                    self.log_box.configure(state="normal")
                    self.log_box.insert("end", value + "\n")
                    self.log_box.see("end")
                    self.log_box.configure(state="disabled")
                elif kind == "progress":
                    done, total = value
                    self.bar["value"] = min(100, done * 100 / max(1, total))
                    self.status.configure(text=f"进度 {done:,} / {total:,} 字节")
                elif kind == "done":
                    self.write_log(value)
                    self.start.configure(state="normal")
                    self.status.configure(text=value)
                    messagebox.showinfo("完成", value)
                elif kind == "error":
                    self.write_log("错误: " + value)
                    self.start.configure(state="normal")
                    self.status.configure(text="上传失败")
                    messagebox.showerror("上传失败", value)
        except queue.Empty:
            pass
        self.root.after(100, self.flush_events)


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
