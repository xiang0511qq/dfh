import hashlib
import json
import os
import struct
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import zstandard as zstd

MAGIC = b"LYXCOMP1"


def compress_file(src, dst, progress):
    size = os.path.getsize(src)
    digest = hashlib.sha256()
    header = {"name": os.path.basename(src), "size": size}
    with open(src, "rb") as inp, open(dst, "wb") as out:
        raw = json.dumps(header, ensure_ascii=False).encode("utf-8")
        out.write(MAGIC + struct.pack("<I", len(raw)) + raw)
        compressor = zstd.ZstdCompressor(level=19).stream_writer(out)
        done = 0
        try:
            while True:
                block = inp.read(8 * 1024 * 1024)
                if not block:
                    break
                digest.update(block)
                compressor.write(block)
                done += len(block)
                progress(done / size * 100 if size else 100)
        finally:
            compressor.flush(zstd.FLUSH_FRAME)
            compressor.close()
        out.write(digest.digest())


def decompress_file(src, target_dir, progress):
    with open(src, "rb") as inp:
        if inp.read(len(MAGIC)) != MAGIC:
            raise ValueError("不是本工具生成的压缩包")
        header_len = struct.unpack("<I", inp.read(4))[0]
        header = json.loads(inp.read(header_len).decode("utf-8"))
        reader = zstd.ZstdDecompressor().stream_reader(inp)
        name = os.path.basename(header["name"])
        out_path = os.path.join(target_dir, name)
        digest = hashlib.sha256()
        with open(out_path, "wb") as out:
            done = 0
            while True:
                block = reader.read(8 * 1024 * 1024)
                if not block:
                    break
                out.write(block)
                digest.update(block)
                done += len(block)
                progress(done / header["size"] * 100 if header["size"] else 100)
        reader.close()
        expected = inp.read(32)
        if digest.digest() != expected:
            raise ValueError("校验失败，压缩包可能损坏")
    return out_path


class App:
    def __init__(self, root):
        self.root = root
        root.title("通用文件压缩工具")
        root.geometry("520x250")
        root.resizable(False, False)
        ttk.Label(root, text="通用文件压缩工具", font=("Microsoft YaHei", 16, "bold")).pack(pady=(18, 8))
        ttk.Label(root, text="支持大文件压缩和完整恢复，压缩率取决于文件内容").pack()
        bar = ttk.Frame(root)
        bar.pack(pady=22)
        ttk.Button(bar, text="选择文件并压缩", command=self.compress).grid(row=0, column=0, padx=10, ipadx=14, ipady=8)
        ttk.Button(bar, text="选择压缩包并解压", command=self.decompress).grid(row=0, column=1, padx=10, ipadx=14, ipady=8)
        self.progress = ttk.Progressbar(root, length=440, mode="determinate")
        self.progress.pack(pady=5)
        self.status = ttk.Label(root, text="等待操作")
        self.status.pack(pady=8)

    def run_thread(self, fn, title):
        self.progress["value"] = 0
        self.status["text"] = title
        def work():
            try:
                result = fn(lambda value: self.root.after(0, self.progress.configure, {"value": value}))
                self.root.after(0, lambda: (self.status.configure(text="完成"), messagebox.showinfo("完成", str(result))))
            except Exception as exc:
                self.root.after(0, lambda: (self.status.configure(text="失败"), messagebox.showerror("操作失败", str(exc))))
        threading.Thread(target=work, daemon=True).start()

    def compress(self):
        src = filedialog.askopenfilename(title="选择要压缩的文件")
        if not src:
            return
        dst = filedialog.asksaveasfilename(title="保存压缩包", defaultextension=".lyxcomp", filetypes=[("LYX压缩包", "*.lyxcomp")])
        if dst:
            self.run_thread(lambda p: (compress_file(src, dst, p), dst)[1], "正在压缩…")

    def decompress(self):
        src = filedialog.askopenfilename(title="选择压缩包", filetypes=[("LYX压缩包", "*.lyxcomp"), ("所有文件", "*.*")])
        if not src:
            return
        target = filedialog.askdirectory(title="选择解压目录")
        if target:
            self.run_thread(lambda p: decompress_file(src, target, p), "正在解压…")


if __name__ == "__main__":
    App(tk.Tk()).root.mainloop()
