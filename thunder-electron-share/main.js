const {app,BrowserWindow,Menu,ipcMain,dialog,session}=require('electron');
const path=require('path');
const fs=require('fs');
const {S3Client}=require('@aws-sdk/client-s3');
const {Upload}=require('@aws-sdk/lib-storage');

let win,loginWin,captured={},captchaMeta=null,notifiedAuth='',lastUploadedId='',lastUploadedName='';

function log(m){if(win&&!win.isDestroyed())win.webContents.send('log',m)}

function openLogin(){
  if(loginWin&&!loginWin.isDestroyed()){loginWin.show();loginWin.focus();return}
  loginWin=new BrowserWindow({width:1120,height:820,parent:win,backgroundColor:'#fff',webPreferences:{contextIsolation:true,nodeIntegration:false}});
  loginWin.loadURL('https://pan.xunlei.com');
  loginWin.on('closed',()=>{loginWin=null});
}

function create(){
  win=new BrowserWindow({width:1280,height:900,minWidth:980,minHeight:700,backgroundColor:'#eef1f5',webPreferences:{preload:path.join(__dirname,'preload.js'),contextIsolation:true,nodeIntegration:false}});
  const menu=Menu.buildFromTemplate([
    {label:'文件',submenu:[{role:'close',label:'关闭'}]},
    {label:'编辑',submenu:[{role:'cut',label:'剪切'},{role:'copy',label:'复制'},{role:'paste',label:'粘贴'},{role:'selectAll',label:'全选'}]},
    {label:'查看',submenu:[{role:'reload',label:'刷新'},{role:'toggledevtools',label:'开发者工具'}]},
    {label:'窗口',submenu:[{role:'minimize',label:'最小化'},{role:'zoom',label:'缩放'}]},
    {label:'帮助',submenu:[{label:'关于迅雷网盘自动上传工具',click:()=>dialog.showMessageBox(win,{type:'info',title:'关于',message:'迅雷网盘自动上传工具'})}]}
  ]);
  Menu.setApplicationMenu(menu);
  win.loadFile(path.join(__dirname,'index.html'));

  session.defaultSession.webRequest.onBeforeRequest({urls:['*://xluser-ssl.xunlei.com/v1/shield/captcha/init*']},(d,cb)=>{
    try{
      if(d.uploadData&&d.uploadData.length){
        const raw=Buffer.concat(d.uploadData.filter(x=>x.bytes).map(x=>Buffer.from(x.bytes))).toString();
        const body=JSON.parse(raw);
        if(body.meta||body.client_id)captchaMeta=body;
      }
    }catch{}
    cb({cancel:false});
  });

  session.defaultSession.webRequest.onBeforeSendHeaders({urls:['*://pan.xunlei.com/*','*://api-pan.xunlei.com/*','*://x-api-pan.xunlei.com/*','*://xluser-ssl.xunlei.com/*']},(d,cb)=>{
    const h=d.requestHeaders||{};
    if(h.Authorization)captured.authorization=h.Authorization;
    if(h['X-Captcha-Token'])captured.captcha=h['X-Captcha-Token'];
    if(h['x-captcha-token'])captured.captcha=h['x-captcha-token'];
    if(h['captcha-token'])captured.captcha=h['captcha-token'];
    if(h['x-captcha-sign'])captured.captchaSign=h['x-captcha-sign'];
    if(h['x-device-id'])captured.device=h['x-device-id'];
    if(h['X-Device-Id'])captured.device=h['X-Device-Id'];
    if(h['x-client-id'])captured.client=h['x-client-id'];
    if(h.clientid)captured.client=h.clientid;
    if(h['x-client-version'])captured.version=h['x-client-version'];
    if(captured.authorization&&captured.device&&win&&!win.isDestroyed()&&notifiedAuth!==captured.authorization){
      notifiedAuth=captured.authorization;
      if(loginWin&&!loginWin.isDestroyed())loginWin.hide();
      win.webContents.send('auth-ready',{loggedIn:true});
    }
    cb({cancel:false,requestHeaders:d.requestHeaders});
  });
}

async function syncWebSession(){
  if(!loginWin||loginWin.isDestroyed())return;
  try{
    const w=await loginWin.webContents.executeJavaScript("(function(){const o={};for(let i=0;i<localStorage.length;i++){const k=localStorage.key(i),v=localStorage.getItem(k)||'';if(k.startsWith('credentials_')){try{const j=JSON.parse(v);if(j.access_token)o.authorization=(j.token_type||'Bearer')+' '+j.access_token;o.client=k.substring(k.indexOf('_')+1)}catch(e){}}if(k.startsWith('captcha')){try{const j=JSON.parse(v);if(j.token)o.captcha=j.token}catch(e){}}if(k==='deviceid')o.device=v.includes('.')?v.slice(v.indexOf('.')+1,v.indexOf('.')+33):v}return o})()");
    if(w.authorization)captured.authorization=w.authorization;
    if(w.device)captured.device=w.device;
    if(w.client)captured.client=w.client;
    if(w.captcha&&!captured.captcha)captured.captcha=w.captcha;
  }catch{}
}

function apiHeaders(){
  return {'Content-Type':'application/json','Accept':'application/json','Authorization':captured.authorization,'X-Captcha-Token':captured.captcha,'x-captcha-token':captured.captcha,'x-device-id':captured.device,'x-client-id':captured.client||'XW5SkOhLDjnOZP7J','clientid':captured.client||'XW5SkOhLDjnOZP7J','x-client-version':captured.version||'1.0.0'};
}

async function uploadApi(body){
  await syncWebSession();
  if(!captured.authorization||!captured.captcha||!captured.device)throw Error('登录会话或验证码尚未读取，请完成扫码后稍等几秒');
  const r=await fetch('https://api-pan.xunlei.com/drive/v1/files',{method:'POST',headers:apiHeaders(),body:JSON.stringify(body)});
  const t=await r.text();
  if(!r.ok)throw Error('HTTP '+r.status+' '+t);
  return JSON.parse(t);
}

async function createShare(fileId,name){
  await syncWebSession();
  if(!loginWin||loginWin.isDestroyed())throw Error('登录窗口已关闭，请重新打开登录');
  const input={fileId:String(fileId||''),name:String(name||lastUploadedName||''),captcha:captured.captcha||'',meta:captchaMeta||{},authorization:captured.authorization||'',device:captured.device||'',client:captured.client||'',version:captured.version||''};
  const script="(async function(i){function b64(s){try{return JSON.parse(atob(s.replace(/-/g,'+').replace(/_/g,'/')+'='.repeat((4-s.length%4)%4)))}catch(e){return {}}}const ls={};for(let n=0;n<localStorage.length;n++){const k=localStorage.key(n),v=localStorage.getItem(k)||'';if(k.startsWith('credentials_')){try{const j=JSON.parse(v);if(j.access_token){ls.authorization=(j.token_type||'Bearer')+' '+j.access_token;ls.client=k.substring(k.indexOf('_')+1)}}catch(e){}}if(k.startsWith('captcha')){try{const j=JSON.parse(v);if(j.token)ls.captcha=j.token}catch(e){}}if(k==='deviceid')ls.device=v.includes('.')?v.slice(v.indexOf('.')+1,v.indexOf('.')+33):v}const authorization=ls.authorization||i.authorization,client=ls.client||i.client||'XW5SkOhLDjnOZP7J',device=ls.device||i.device,oldCaptcha=ls.captcha||i.captcha;const payload=JSON.parse(JSON.stringify(i.meta||{}));payload.action='POST:/drive/v1/share';payload.client_id=client;payload.device_id=device;payload.captcha_token=oldCaptcha||'';payload.meta=payload.meta||{};payload.meta.package_name=payload.meta.package_name||'pan.xunlei.com';payload.meta.client_version=payload.meta.client_version||i.version||'1.0.0';payload.meta.timestamp=String(Date.now());const part=(authorization||'').split('.')[1];if(part){const p=b64(part);if(p.sub)payload.meta.user_id=String(p.sub)}let token=oldCaptcha;try{const ir=await fetch('https://xluser-ssl.xunlei.com/v1/shield/captcha/init',{method:'POST',credentials:'include',headers:{'Content-Type':'application/json','Origin':'https://pan.xunlei.com','Referer':'https://pan.xunlei.com/','x-client-id':client,'x-device-id':device,'x-client-version':payload.meta.client_version},body:JSON.stringify(payload)});const ij=await ir.json();if(ij.captcha_token)token=ij.captcha_token}catch(e){}const h={'Content-Type':'application/json','Accept':'application/json','Authorization':authorization,'X-Captcha-Token':token,'x-captcha-token':token,'x-device-id':device,'x-client-id':client,'clientid':client,'x-client-version':payload.meta.client_version};const body={file_ids:[i.fileId],share_to:'copy',expiration_days:0,pass_code_option:'NO_PASS_CODE',params:{},title:i.name};const r=await fetch('https://api-pan.xunlei.com/drive/v1/share',{method:'POST',credentials:'include',headers:h,body:JSON.stringify(body)});return {status:r.status,text:await r.text()}})("+JSON.stringify(input)+")";
  const result=await loginWin.webContents.executeJavaScript(script);
  if(!result||result.status<200||result.status>=300)throw Error('创建分享 HTTP '+(result?result.status:'未知')+' '+(result?result.text:''));
  let j={};try{j=JSON.parse(result.text)}catch{}
  const d=j.data||j;
  const shareId=d.share_id||d.id||'';
  const url=d.share_url||d.url||(shareId?'https://pan.xunlei.com/s/'+shareId:'');
  const pass=d.pass_code||d.password||'';
  if(!url)throw Error('创建分享成功但返回中没有分享链接：'+result.text.slice(0,300));
  return url+(pass?' 提取码：'+pass:'');
}

ipcMain.handle('set-auth',async(_,a)=>{
  if(a&&a.authorization)captured.authorization=a.authorization;
  if(a&&a.device)captured.device=a.device;
  if(a&&a.client)captured.client=a.client;
  if(a&&a.version)captured.version=a.version;
  if(a&&a.captcha)captured.captcha=a.captcha;
  if(captured.authorization&&captured.device&&win&&!win.isDestroyed()&&notifiedAuth!==captured.authorization){
    notifiedAuth=captured.authorization;
    win.webContents.send('auth-ready',{loggedIn:true});
  }
  return true;
});

ipcMain.handle('get-auth',async()=>{
  await syncWebSession();
  return {authorization:captured.authorization||'',device:captured.device||'',captcha:captured.captcha||'',client:captured.client||'',version:captured.version||''};
});

ipcMain.handle('open-login',async()=>{openLogin();return true});
ipcMain.handle('generate-share',async(_,name)=>{
  if(!lastUploadedId)throw Error('没有找到刚上传文件的文件 ID');
  const url=await createShare(lastUploadedId,name);
  log('永久分享链接：'+url);
  return url;
});
ipcMain.handle('choose-file',async()=>{
  const r=await dialog.showOpenDialog(win,{properties:['openFile']});
  return r.canceled?null:r.filePaths[0];
});
ipcMain.handle('upload',async(_,p)=>{
  await syncWebSession();
  if(!captured.authorization||!captured.device)throw Error('请先扫码登录');
  const size=fs.statSync(p.file).size;
  const name=path.basename(p.file);
  log('预创建文件 '+name);
  const pre=await uploadApi({kind:'drive#file',parent_id:p.parent||'',name,size,upload_type:'UPLOAD_TYPE_RESUMABLE'});
  lastUploadedId=pre.id||pre.file_id||(pre.file&&pre.file.id)||(pre.resource&&pre.resource.id)||'';
  lastUploadedName=name;
  const q=pre.resumable&&pre.resumable.params;
  if(!q)throw Error('预创建接口未返回分片参数：'+JSON.stringify(pre).slice(0,300));
  let endpoint=q.endpoint;
  if(endpoint.startsWith(q.bucket+'.'))endpoint='https://'+endpoint.slice(q.bucket.length+1);
  if(!endpoint.startsWith('http'))endpoint='https://'+endpoint;
  const client=new S3Client({region:'xunlei',endpoint,credentials:{accessKeyId:q.access_key_id,secretAccessKey:q.access_key_secret,sessionToken:q.security_token}});
  const up=new Upload({client,params:{Bucket:q.bucket,Key:q.key,Body:fs.createReadStream(p.file),ContentType:'application/octet-stream'},partSize:8*1024*1024,queueSize:3});
  up.on('httpUploadProgress',x=>win.webContents.send('progress',{done:x.loaded||0,total:size}));
  await up.done();
  log('分片上传完成，服务器已合并文件');
  if(!lastUploadedId)log('已上传，但预创建响应没有返回文件 ID，将尝试网页端定位');
  return true;
});

app.whenReady().then(create);
app.on('window-all-closed',()=>{if(process.platform!=='darwin')app.quit()});
