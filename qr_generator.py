import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

try:
    import qrcode
    from PIL import ImageTk
except ImportError:
    qrcode = None

class QRApp:
    def __init__(self, root):
        self.root = root
        root.title('二维码生成工具')
        root.geometry('620x680')
        root.resizable(False, False)
        self.preview = None
        self.image = None
        frm = ttk.Frame(root, padding=18)
        frm.pack(fill='both', expand=True)
        ttk.Label(frm, text='二维码生成工具', font=('Microsoft YaHei UI', 18, 'bold')).pack(pady=(0, 12))
        ttk.Label(frm, text='输入文字、网址或机器编号：').pack(anchor='w')
        self.text = tk.Text(frm, height=6, width=66, font=('Microsoft YaHei UI', 11))
        self.text.pack(fill='x', pady=(6, 12))
        btns = ttk.Frame(frm); btns.pack(fill='x')
        ttk.Button(btns, text='生成二维码', command=self.generate).pack(side='left', padx=(0, 8))
        ttk.Button(btns, text='保存 PNG', command=self.save).pack(side='left')
        ttk.Button(btns, text='清空', command=lambda: self.text.delete('1.0', 'end')).pack(side='right')
        self.status = ttk.Label(frm, text='请输入内容后点击“生成二维码”', foreground='#666')
        self.status.pack(anchor='w', pady=10)
        self.canvas = tk.Label(frm, bg='white', width=420, height=420, relief='solid', borderwidth=1)
        self.canvas.pack(pady=6)
        ttk.Label(frm, text='支持中文、网址、序列号和机器编号；图片默认保存为 PNG。', foreground='#666').pack(pady=8)

    def generate(self):
        if qrcode is None:
            messagebox.showerror('缺少组件', '请先运行 build.bat 安装组件。'); return
        data = self.text.get('1.0', 'end').strip()
        if not data:
            messagebox.showwarning('提示', '请输入要生成二维码的内容。'); return
        qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=4)
        qr.add_data(data); qr.make(fit=True)
        self.image = qr.make_image(fill_color='black', back_color='white').convert('RGB')
        display = self.image.copy(); display.thumbnail((420, 420))
        self.preview = ImageTk.PhotoImage(display)
        self.canvas.configure(image=self.preview, width=420, height=420)
        self.status.configure(text=f'已生成，内容长度：{len(data)} 字符')

    def save(self):
        if self.image is None:
            self.generate()
        if self.image is None: return
        path = filedialog.asksaveasfilename(title='保存二维码', defaultextension='.png', filetypes=[('PNG 图片', '*.png')], initialfile='二维码.png')
        if path:
            self.image.save(path, 'PNG')
            self.status.configure(text=f'已保存：{Path(path).name}')
            messagebox.showinfo('保存成功', f'二维码已保存到：\n{path}')

if __name__ == '__main__':
    root = tk.Tk()
    QRApp(root)
    root.mainloop()
