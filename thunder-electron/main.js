const {app,BrowserWindow,ipcMain,dialog,session} = require('electron');
const path=require('path');
const fs=require('fs');
const {S3Client, PutObjectCommand}=require('@aws-sdk/client-s3');
const {Upload}=require('@aws-sdk/lib-storage');
let win, captured={};
function log(m){if(win) win.webContents.send('log',m)}
function create(){
 win=new BrowserWindow({width:1200,height:820,webPreferences:{preload:path.join(__dirname,'preload.js'),webviewTag:true,contextIsolation:true,nodeIntegration:false}});
 win.loadFile(path.join(__dirname,'index.html'));
 const ses=session.defaultSession;
 ses.webRequest.onBeforeSendHeaders({urls:['*://pan.xunlei.com/*','*://api-pan.xunlei.com/*','*://x-api-pan.xunlei.com/*']},(d,cb)=>{const h=d.requestHeaders||{}; if(h.Authorization) captured.authorization=h.Authorization; if(h['X-Captcha-Token']) captured.captcha=h['X-Captcha-Token']; if(h['x-captcha-token']) captured.captcha=h['x-captcha-token']; if(h['x-device-id']) captured.device=h['x-device-id']; if(h['x-client-id']) captured.client=h['x-client-id']; if(h['x-client-version']) captured.version=h['x-client-version']; cb({cancel:false,requestHeaders:d.requestHeaders})});
}
async function api(body){const h={'Content-Type':'application/json','Accept':'application/json',Authorization:captured.authorization,'X-Captcha-Token':captured.captcha,'x-device-id':captured.device,'x-client-id':captured.client||'XW5SkOhLDjn0ZP7J','x-client-version':captured.version||'1.0.0'}; const r=await fetch('https://api-pan.xunlei.com/drive/v1/files',{method:'POST',headers:h,body:JSON.stringify(body)}); const t=await r.text(); if(!r.ok) throw new Error('HTTP '+r.status+' '+t); return JSON.parse(t)}
ipcMain.handle('choose-file',async()=>{const r=await dialog.showOpenDialog(win,{properties:['openFile']}); return r.canceled?null:r.filePaths[0]});
ipcMain.handle('upload',async(_,p)=>{if(!captured.authorization||!captured.captcha||!captured.device) throw new Error('请先在上方迅雷页面登录，并刷新后再试'); const size=fs.statSync(p.file).size; log('预创建文件 '+path.basename(p.file)); const pre=await api({kind:'drive#file',parent_id:p.parent||'',name:path.basename(p.file),size,upload_type:'UPLOAD_TYPE_RESUMABLE'}); const q=pre.resumable.params; let endpoint=q.endpoint; if(endpoint.startsWith(q.bucket+'.')) endpoint='https://'+endpoint.slice(q.bucket.length+1); if(!endpoint.startsWith('http')) endpoint='https://'+endpoint; const client=new S3Client({region:'xunlei',endpoint,credentials:{accessKeyId:q.access_key_id,secretAccessKey:q.access_key_secret,sessionToken:q.security_token}}); const up=new Upload({client,params:{Bucket:q.bucket,Key:q.key,Body:fs.createReadStream(p.file),ContentType:'application/octet-stream'},partSize:8*1024*1024,queueSize:3}); up.on('httpUploadProgress',x=>win.webContents.send('progress',{done:x.loaded||0,total:size})); await up.done(); log('分片上传完成，服务器已合并文件'); return true});
app.whenReady().then(create); app.on('window-all-closed',()=>{if(process.platform!=='darwin')app.quit()});
