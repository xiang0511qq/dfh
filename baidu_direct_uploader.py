import hashlib, json, os, random, threading, time, tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from urllib.parse import quote
import requests

API='https://pan.baidu.com'
UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/132.0 Safari/537.36'
CHUNK=4*1024*1024

class BaiduClient:
    def __init__(self,cookie,log,progress):
        self.s=requests.Session(); self.s.headers.update({'User-Agent':UA,'Referer':'https://pan.baidu.com/','Cookie':cookie,'Origin':'https://pan.baidu.com','Accept-Encoding':'identity'})
        self.log,self.progress=log,progress; self.bdstoken=''
        self.cookie=dict((x.strip().split('=',1) for x in cookie.replace('\n',' ').split(';') if '=' in x))
    def get_token(self):
        r=self.s.get(API+'/api/gettemplatevariable',params={'prod':'pan','fields':'["bdstoken"]'},timeout=30)
        try:d=r.json()
        except Exception: raise RuntimeError('Cookie无效或接口返回异常')
        if isinstance(d,list):
            d=next((x for x in d if isinstance(x,dict)), {})
        result=d.get('result',{}) if isinstance(d,dict) else {}
        if isinstance(result,list): result=next((x for x in result if isinstance(x,dict)), {})
        self.bdstoken=(result.get('bdstoken') if isinstance(result,dict) else '') or (d.get('bdstoken') if isinstance(d,dict) else '') or ''
        if not self.bdstoken: raise RuntimeError('未获取到 bdstoken，请重新登录百度网盘并获取完整Cookie')
        return self.bdstoken
    def post(self,path,data,files=None):
        if not self.bdstoken:self.get_token()
        data=dict(data); params={'bdstoken':self.bdstoken,'channel':'chunlei','web':'1','app_id':'250528','clienttype':'0','logid':'MTU0ODQ2NzUyODMzMjAuMjMxNjA1MzgwMTEzNzc0OTY='}
        r=self.s.post(API+path,params=params,data=data,files=files,timeout=120)
        try:d=r.json()
        except Exception: raise RuntimeError(f'接口返回异常 HTTP {r.status_code}')
        if isinstance(d,list): d=next((x for x in d if isinstance(x,dict)), {})
        if d.get('errno',0)!=0: raise RuntimeError(f'百度接口错误 {d.get("errno")}: {d.get("errmsg",d)}')
        return d
    def md5s(self,path):
        arr=[]; size=os.path.getsize(path)
        with open(path,'rb') as f:
            while True:
                b=f.read(CHUNK)
                if not b: break
                arr.append(hashlib.md5(b).hexdigest())
        return arr,size
    def mkdir(self,path):
        try:self.post('/api/create',{'path':path,'isdir':'1','size':'0','block_list':'[]','rtype':'1'})
        except RuntimeError as e:
            if 'errno 31066' not in str(e): raise
    def upload_file(self,local,remote):
        blocks,size=self.md5s(local); self.log(f'准备上传: {os.path.basename(local)} ({size/1048576:.2f} MB)')
        pre=self.post('/api/precreate',{'path':remote,'target_path':str(Path(remote).parent).replace('\\','/'),'size':str(size),'isdir':'0','autoinit':'1','block_list':json.dumps(blocks,separators=(',',':')),'rtype':'1','startLogTime':str(int(time.time()*1000)),'local_mtime':str(int(os.path.getmtime(local))),'local_ctime':str(int(os.path.getctime(local)))})
        uploadid=pre.get('uploadid',''); need=pre.get('block_list',list(range(len(blocks))))
        if need and uploadid:
            with open(local,'rb') as f:
                for idx in need:
                    f.seek(idx*CHUNK); part=f.read(CHUNK)
                    params={'method':'upload','type':'tmpfile','path':remote,'uploadid':uploadid,'partseq':str(idx),'bdstoken':self.bdstoken,'app_id':'250528','BDUSS':self.cookie.get('BDUSS') or self.cookie.get('BDUSS_BFESS',''),'clienttype':'0','channel':'chunlei','web':'1'}
                    r=self.s.post('https://c3.pcs.baidu.com/rest/2.0/pcs/superfile2',params=params,files={'file':('blob',part)},timeout=180)
                    try:d=r.json()
                    except Exception: raise RuntimeError('分片上传返回异常')
                    if d.get('errno',0)!=0: raise RuntimeError(f'分片上传失败 {d}')
                    self.progress((idx+1)*100/max(1,len(need)))
        created=self.post('/api/create',{'path':remote,'size':str(size),'isdir':'0','uploadid':uploadid,'block_list':json.dumps(blocks,separators=(',',':')),'rtype':'1'})
        self.log('上传完成: '+remote)
        return created.get('fs_id') or created.get('fsid')
    def share(self, fs_id):
        if not fs_id: raise RuntimeError('上传接口没有返回文件ID，无法创建分享')
        if not self.bdstoken: self.get_token()
        params={'channel':'chunlei','clienttype':'0','web':'1','app_id':'250528','bdstoken':self.bdstoken}
        pwd=''.join(random.choice('abcdefghijklmnopqrstuvwxyz0123456789') for _ in range(4))
        data={'fid_list':json.dumps([int(fs_id)]),'schannel':'4','channel_list':'[]','period':'0','pwd':pwd,'eflag_disable':'true'}
        r=self.s.post(API+'/share/set',params=params,data=data,timeout=60)
        try:d=r.json()
        except Exception: raise RuntimeError(f'分享接口返回异常 HTTP {r.status_code}')
        if d.get('errno',0)!=0: raise RuntimeError(f'创建分享失败 {d}')
        link=d.get('link') or d.get('shorturl') or d.get('share_url') or (d.get('data') or {}).get('link')
        return link, pwd

    def upload_folder(self,folder,target):
        root=Path(folder); target=target.rstrip('/') or ''
        files=[p for p in root.rglob('*') if p.is_file()]; dirs=[p for p in root.rglob('*') if p.is_dir()]
        base=(target+'/'+root.name).replace('//','/')
        self.mkdir(base); self.log(f'文件夹: {root.name}，共 {len(files)} 个文件')
        for d in sorted(dirs,key=lambda x:len(x.parts)):
            self.mkdir(base+'/'+str(d.relative_to(root)).replace('\\','/'))
        for p in files:self.upload_file(str(p),base+'/'+str(p.relative_to(root)).replace('\\','/'))

class App(tk.Tk):
    def __init__(self):
        super().__init__(); self.title('百度网盘文件上传'); self.geometry('760x610'); self.resizable(False,False)
        self.path=tk.StringVar(); self.target=tk.StringVar(value='/'); self.mode='file'
        ttk.Label(self,text='百度网盘 Cookie（只在本机使用）').pack(anchor='w',padx=14,pady=(12,3)); self.cookie=tk.Text(self,height=4,width=98); self.cookie.pack(padx=14)
        row=ttk.Frame(self); row.pack(fill='x',padx=14,pady=8); ttk.Label(row,text='目标文件夹路径').pack(side='left'); ttk.Entry(row,textvariable=self.target,width=42).pack(side='left',padx=8)
        ttk.Button(row,text='选择文件',command=self.choose_file).pack(side='left',padx=4); ttk.Button(row,text='选择文件夹',command=self.choose_folder).pack(side='left',padx=4)
        ttk.Entry(self,textvariable=self.path,width=98).pack(padx=14,pady=6); self.bar=ttk.Progressbar(self,length=730,mode='determinate'); self.bar.pack(padx=14,pady=8); ttk.Button(self,text='开始上传',command=self.start).pack(pady=4)
        self.logbox=tk.Text(self,height=20,width=98,state='disabled'); self.logbox.pack(padx=14,pady=8)
    def log(self,s): self.after(0,lambda:(self.logbox.configure(state='normal'),self.logbox.insert('end',s+'\n'),self.logbox.see('end'),self.logbox.configure(state='disabled')))
    def progress(self,n): self.after(0,lambda:self.bar.configure(value=n))
    def choose_file(self):
        p=filedialog.askopenfilename();
        if p:self.path.set(p); self.mode='file'
    def choose_folder(self):
        p=filedialog.askdirectory();
        if p:self.path.set(p); self.mode='folder'
    def start(self):
        ck=self.cookie.get('1.0','end').strip(); p=self.path.get().strip(); target=self.target.get().strip() or '/'
        if not ck or not p:return messagebox.showwarning('提示','请填写Cookie并选择文件或文件夹')
        threading.Thread(target=self.run,args=(ck,p,target,self.mode),daemon=True).start()
    def run(self,ck,p,target,mode):
        try:
            c=BaiduClient(ck,self.log,self.progress); c.get_token()
            if mode=='folder':
                c.upload_folder(p,target)
                self.log('文件夹上传完成。文件夹分享请在百度网盘网页中右键分享。')
            else:
                fid=c.upload_file(p,(target.rstrip('/')+'/'+os.path.basename(p)).replace('//','/'))
                link,pwd=c.share(fid)
                sep='&' if '?' in str(link) else '?'
                self.log('永久分享链接: '+str(link)+sep+'pwd='+pwd)
            self.log('全部上传完成')
        except Exception as e:self.log('失败: '+str(e)); self.after(0,lambda:messagebox.showerror('上传失败',str(e)))
if __name__=='__main__': App().mainloop()
