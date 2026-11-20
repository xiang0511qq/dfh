import os, sys, json, shutil, subprocess, threading, time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

APP='网盘四合一'
BASE=os.path.dirname(os.path.abspath(sys.executable if getattr(sys,'frozen',False) else __file__))
DATA=os.path.join(os.path.expanduser('~'), '.网盘四合一')
CFG=os.path.join(DATA,'config.json')

def bundled(name):
    p=os.path.join(BASE,'tools',name)
    if os.path.exists(p): return p
    return os.path.join(BASE,name)

TOOLS={
 '夸克网盘': '夸克网盘上传工具.exe',
 '百度网盘': '百度网盘分享链接版.exe',
 '迅雷网盘': os.path.join('迅雷网盘','迅雷网盘上传分享工具.exe'),
 'UC网盘': 'UC网盘上传工具-7.exe',
}
class App:
 def __init__(self,root):
  self.root=root; root.title(APP); root.geometry('1040x700'); root.minsize(900,600)
  self.cfg=self.load(); self.vars={k:tk.BooleanVar(value=self.cfg.get('selected',{}).get(k,True)) for k in TOOLS}
  self.cookie_vars={k:tk.StringVar(value=self.cfg.get('cookies',{}).get(k,'')) for k in TOOLS}
  self.links={k:tk.StringVar(value=self.cfg.get('links',{}).get(k,'')) for k in TOOLS}
  self.status={k:tk.StringVar(value=self.cfg.get('status',{}).get(k,'未登录')) for k in TOOLS}
  self.build(); self.log('欢迎使用网盘四合一，总控界面已就绪。')
 def load(self):
  try:
   with open(CFG,'r',encoding='utf8') as f:return json.load(f)
  except:return {}
 def save(self):
  os.makedirs(DATA,exist_ok=True)
  d={'selected':{k:v.get() for k,v in self.vars.items()},'cookies':{k:v.get() for k,v in self.cookie_vars.items()},'links':{k:v.get() for k,v in self.links.items()},'status':{k:v.get() for k,v in self.status.items()}}
  with open(CFG,'w',encoding='utf8') as f:json.dump(d,f,ensure_ascii=False,indent=2)
  self.log('设置和 Cookie 已保存到本机。')
 def build(self):
  style=ttk.Style(); style.theme_use('clam'); style.configure('Title.TLabel',font=('Microsoft YaHei UI',22,'bold'),foreground='#173b7a'); style.configure('Sub.TLabel',foreground='#667085'); style.configure('Card.TLabelframe',padding=12)
  top=ttk.Frame(self.root,padding=(22,18,22,8)); top.pack(fill='x'); ttk.Label(top,text='网盘四合一',style='Title.TLabel').pack(anchor='w'); ttk.Label(top,text='统一选择 · 同时启动 · Cookie 保存 · 分享链接管理',style='Sub.TLabel').pack(anchor='w',pady=(4,0))
  body=ttk.Frame(self.root,padding=22); body.pack(fill='both',expand=True); body.columnconfigure(0,weight=1); body.columnconfigure(1,weight=2); body.rowconfigure(1,weight=1)
  left=ttk.LabelFrame(body,text=' 选择网盘 ',style='Card.TLabelframe'); left.grid(row=0,column=0,rowspan=2,sticky='nsew',padx=(0,14));
  ttk.Label(left,text='选择后可批量打开上传工具',style='Sub.TLabel').pack(anchor='w',pady=(0,12))
  for k in TOOLS:
   f=ttk.Frame(left); f.pack(fill='x',pady=6); ttk.Checkbutton(f,text=k,variable=self.vars[k],command=self.save).pack(side='left'); ttk.Label(f,textvariable=self.status[k],foreground='#16803c').pack(side='left',padx=8); ttk.Button(f,text='登录/打开',command=lambda x=k:self.login_or_open(x)).pack(side='right')
  ttk.Separator(left).pack(fill='x',pady=14); ttk.Button(left,text='一键打开已选择平台',command=self.launch_selected).pack(fill='x',pady=4); ttk.Button(left,text='选择上传文件',command=self.choose_file).pack(fill='x',pady=4); ttk.Button(left,text='保存 Cookie / 设置',command=self.save).pack(fill='x',pady=4)
  self.file_var=tk.StringVar(); ttk.Entry(left,textvariable=self.file_var,state='readonly').pack(fill='x',pady=(16,0))
  right=ttk.LabelFrame(body,text=' Cookie 与分享链接 ',style='Card.TLabelframe'); right.grid(row=0,column=1,sticky='nsew'); right.columnconfigure(1,weight=1)
  for i,k in enumerate(TOOLS):
   ttk.Label(right,text=k).grid(row=i,column=0,sticky='w',padx=(0,10),pady=5); ttk.Entry(right,textvariable=self.cookie_vars[k],show='•').grid(row=i,column=1,sticky='ew',pady=5); ttk.Button(right,text='显示',command=lambda x=k:self.toggle_cookie(x)).grid(row=i,column=2,padx=(8,0))
   ttk.Label(right,text='分享链接').grid(row=i,column=3,padx=(18,5)); ttk.Entry(right,textvariable=self.links[k]).grid(row=i,column=4,sticky='ew',pady=5); ttk.Button(right,text='复制',command=lambda x=k:self.copy_link(x)).grid(row=i,column=5,padx=(8,0))
   if k=='迅雷网盘': ttk.Label(right,text='原工具登录').grid(row=i,column=6,padx=(8,0))
  right.columnconfigure(4,weight=1)
  logf=ttk.LabelFrame(body,text=' 运行日志 ',style='Card.TLabelframe'); logf.grid(row=1,column=1,sticky='nsew',pady=(14,0)); logf.rowconfigure(0,weight=1); logf.columnconfigure(0,weight=1)
  self.text=tk.Text(logf,height=10,bg='#f8fafc',fg='#334155',relief='flat',font=('Consolas',10)); self.text.grid(row=0,column=0,sticky='nsew'); sb=ttk.Scrollbar(logf,command=self.text.yview); sb.grid(row=0,column=1,sticky='ns'); self.text.configure(yscrollcommand=sb.set)
  bot=ttk.Frame(self.root,padding=(22,8,22,16)); bot.pack(fill='x'); ttk.Button(bot,text='清空日志',command=lambda:self.text.delete('1.0','end')).pack(side='left'); ttk.Button(bot,text='导出链接',command=self.export_links).pack(side='left',padx=8); ttk.Label(bot,text='提示：分享链接由各原上传工具生成后粘贴到对应框中。',style='Sub.TLabel').pack(side='right')
 def toggle_cookie(self,k):
  # tkinter Entry show change
  self.log(f'{k} Cookie 输入框可编辑；保存后下次自动恢复。')
 def choose_file(self):
  p=filedialog.askopenfilename();
  if p:self.file_var.set(p); self.log('已选择文件：'+p)
 def selected(self): return [k for k in TOOLS if self.vars[k].get()]
 def launch_selected(self): self.launch(self.selected())
 def login_or_open(self,k):
  self.launch([k])
  if k=='迅雷网盘': self.log('迅雷：请在原工具窗口内登录，登录状态由原工具保存在本机。')
  else: self.log(f'{k}：请使用原工具完成登录；Cookie 可在上方保存。')
 def launch(self,names):
  if not names: messagebox.showinfo('提示','请先选择至少一个网盘。'); return
  for k in names:
   rel=TOOLS[k]; p=bundled(rel) if not os.path.isabs(rel) else os.path.join(BASE,'tools',rel)
   if os.path.exists(p):
    try: subprocess.Popen([p],cwd=os.path.dirname(p)); self.log(f'已启动：{k}')
    except Exception as e:self.log(f'{k} 启动失败：{e}')
   else:self.log(f'未找到 {k} 工具文件：{p}')
  self.save()
 def copy_link(self,k):
  s=self.links[k].get().strip();
  if not s:return
  self.root.clipboard_clear(); self.root.clipboard_append(s); self.log(f'已复制 {k} 分享链接。')
 def export_links(self):
  p=filedialog.asksaveasfilename(defaultextension='.txt',initialfile='网盘分享链接.txt');
  if p:
   with open(p,'w',encoding='utf8') as f:
    for k in TOOLS:f.write(f'{k}\n{self.links[k].get().strip()}\n\n')
   self.log('分享链接已导出：'+p)
 def log(self,s): self.text.insert('end',time.strftime('[%H:%M:%S] ')+s+'\n'); self.text.see('end')
if __name__=='__main__':
 root=tk.Tk(); App(root); root.mainloop()
