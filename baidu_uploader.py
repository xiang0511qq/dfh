import os, threading, tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from playwright.sync_api import sync_playwright

class App(tk.Tk):
    def __init__(self):
        super().__init__(); self.title('百度网盘文件上传'); self.geometry('760x610'); self.resizable(False, False)
        self.path=tk.StringVar(); self.target=tk.StringVar(value='/'); self.mode='file'; self.ctx=None; self.pw=None
        ttk.Label(self,text='百度网盘 Cookie（只在本机使用）').pack(anchor='w',padx=14,pady=(12,3))
        self.cookie=tk.Text(self,height=4,width=98); self.cookie.pack(padx=14)
        row=ttk.Frame(self); row.pack(fill='x',padx=14,pady=8)
        ttk.Label(row,text='目标文件夹路径').pack(side='left'); ttk.Entry(row,textvariable=self.target,width=42).pack(side='left',padx=8)
        ttk.Button(row,text='选择文件',command=self.choose_file).pack(side='left',padx=4); ttk.Button(row,text='选择文件夹',command=self.choose_folder).pack(side='left',padx=4)
        ttk.Entry(self,textvariable=self.path,width=98).pack(padx=14,pady=6)
        self.bar=ttk.Progressbar(self,length=730,mode='determinate'); self.bar.pack(padx=14,pady=8)
        ttk.Button(self,text='开始上传',command=self.start).pack(pady=4)
        self.logbox=tk.Text(self,height=20,width=98,state='disabled'); self.logbox.pack(padx=14,pady=8)
    def log(self,s): self.after(0,lambda:(self.logbox.configure(state='normal'),self.logbox.insert('end',s+'\n'),self.logbox.see('end'),self.logbox.configure(state='disabled')))
    def choose_file(self):
        p=filedialog.askopenfilename()
        if p:self.path.set(p);self.mode='file'
    def choose_folder(self):
        p=filedialog.askdirectory()
        if p:self.path.set(p);self.mode='folder'
    def parse_cookie(self,s):
        out=[]
        for item in s.replace('\n',' ').split(';'):
            item=item.strip()
            if '=' in item:
                n,v=item.split('=',1); out.append({'name':n.strip(),'value':v.strip(),'domain':'.baidu.com','path':'/'})
        return out
    def start(self):
        ck=self.cookie.get('1.0','end').strip(); p=self.path.get().strip()
        if not ck or not p:return messagebox.showwarning('提示','请填写 Cookie 并选择文件或文件夹')
        threading.Thread(target=self.run,args=(ck,p,self.mode),daemon=True).start()
    def run(self,ck,p,mode):
        try:
            self.pw=sync_playwright().start(); profile=os.path.join(os.path.dirname(os.path.abspath(__file__)),'百度网页会话')
            self.ctx=self.pw.chromium.launch_persistent_context(profile,headless=False, channel='msedge')
            self.ctx.add_cookies(self.parse_cookie(ck))
            page=self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page(); page.goto('https://pan.baidu.com/disk/main',wait_until='domcontentloaded',timeout=60000)
            self.log('已打开百度网盘，请确认账号已登录，并打开目标文件夹。')
            files=[p] if mode=='file' else [str(x) for x in Path(p).rglob('*') if x.is_file()]
            self.log(f'共准备 {len(files)} 个文件。请在网页中点击上传按钮后继续。')
            self.log('百度网盘的验证码和上传流程由网页处理。')
            inputs=page.locator('input[type="file"]')
            if inputs.count()==0:
                self.log('网页当前没有上传控件，请在网页中点击“上传”后重试。'); return
            inputs.first.set_input_files(files); self.log('已提交上传，请在百度网页右上角查看进度。')
        except Exception as e:self.log('失败：'+str(e))
    def destroy(self):
        try:
            if self.ctx:self.ctx.close()
            if self.pw:self.pw.stop()
        finally:super().destroy()
if __name__=='__main__': App().mainloop()
