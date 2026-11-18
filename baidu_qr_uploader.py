import os, threading, tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from playwright.sync_api import sync_playwright
from baidu_direct_uploader import BaiduClient

class App(tk.Tk):
    def __init__(self):
        super().__init__(); self.title('百度网盘扫码上传'); self.geometry('760x610'); self.resizable(False,False)
        self.path=tk.StringVar(); self.target=tk.StringVar(value='/'); self.mode='file'; self.ctx=None; self.pw=None; self.cookie=''
        ttk.Label(self,text='百度网盘扫码登录（登录状态只在本机使用）').pack(anchor='w',padx=14,pady=(12,3))
        row=ttk.Frame(self); row.pack(fill='x',padx=14,pady=8)
        ttk.Button(row,text='扫码登录',command=self.login).pack(side='left'); self.status=ttk.Label(row,text='未登录'); self.status.pack(side='left',padx=12)
        ttk.Label(row,text='目标文件夹路径').pack(side='left',padx=(30,0)); ttk.Entry(row,textvariable=self.target,width=25).pack(side='left',padx=8)
        ttk.Button(row,text='选择文件',command=self.choose_file).pack(side='left',padx=4); ttk.Button(row,text='选择文件夹',command=self.choose_folder).pack(side='left',padx=4)
        ttk.Entry(self,textvariable=self.path,width=98).pack(padx=14,pady=6); self.bar=ttk.Progressbar(self,length=730,mode='determinate'); self.bar.pack(padx=14,pady=8)
        ttk.Button(self,text='开始上传',command=self.start).pack(pady=4); self.logbox=tk.Text(self,height=20,width=98,state='disabled'); self.logbox.pack(padx=14,pady=8)
    def log(self,s): self.after(0,lambda:(self.logbox.configure(state='normal'),self.logbox.insert('end',s+'\n'),self.logbox.see('end'),self.logbox.configure(state='disabled')))
    def progress(self,n): self.after(0,lambda:self.bar.configure(value=n))
    def choose_file(self):
        p=filedialog.askopenfilename();
        if p:self.path.set(p);self.mode='file'
    def choose_folder(self):
        p=filedialog.askdirectory();
        if p:self.path.set(p);self.mode='folder'
    def login(self): threading.Thread(target=self._login,daemon=True).start()
    def _login(self):
        try:
            self.pw=sync_playwright().start(); profile=os.path.join(os.path.dirname(os.path.abspath(__file__)),'百度扫码会话')
            self.ctx=self.pw.chromium.launch_persistent_context(profile,headless=False,channel='msedge')
            page=self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page(); page.goto('https://pan.baidu.com/disk/main',wait_until='domcontentloaded',timeout=60000)
            self.after(0,lambda:self.status.configure(text='请在打开的网页中扫码登录'))
            self.log('已打开百度网盘，请扫码登录。')
            for _ in range(180):
                cs=self.ctx.cookies(['https://pan.baidu.com/','https://passport.baidu.com/'])
                names={c['name']:c['value'] for c in cs}
                if names.get('BDUSS') or names.get('BDUSS_BFESS'):
                    self.cookie='; '.join(f'{k}={v}' for k,v in names.items())
                    try:
                        self.ctx.close()
                        self.pw.stop()
                    except Exception:
                        pass
                    self.ctx=None; self.pw=None
                    self.after(0,lambda:self.status.configure(text='已登录，浏览器已关闭'))
                    self.log('扫码登录成功，浏览器已自动关闭。')
                    return
                page.wait_for_timeout(1000)
            self.log('扫码登录超时，请重试。')
        except Exception as e:
            self.log('登录失败: '+str(e))
            self.after(0,lambda:self.status.configure(text='登录失败，请重新扫码'))
    def start(self):
        if not self.cookie:return messagebox.showwarning('提示','请先点击“扫码登录”并完成登录')
        p=self.path.get().strip(); target=self.target.get().strip() or '/'
        if not p:return messagebox.showwarning('提示','请先选择文件或文件夹')
        threading.Thread(target=self.run,args=(p,target,self.mode),daemon=True).start()
    def run(self,p,target,mode):
        try:
            c=BaiduClient(self.cookie,self.log,self.progress)
            if mode=='folder': c.upload_folder(p,target); self.log('文件夹上传完成')
            else:
                fid=c.upload_file(p,(target.rstrip('/')+'/'+os.path.basename(p)).replace('//','/')); link,pwd=c.share(fid); sep='&' if '?' in str(link) else '?'; self.log('永久分享链接: '+str(link)+sep+'pwd='+pwd)
            self.log('全部完成')
        except Exception as e:self.log('失败: '+str(e)); self.after(0,lambda:messagebox.showerror('失败',str(e)))
if __name__=='__main__': App().mainloop()
