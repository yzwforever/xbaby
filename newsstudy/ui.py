import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from datetime import timedelta
from tkinter import ttk, messagebox
from .settings import ROOT, SECRET_NAMES, load_config, load_secrets, save_secrets, atomic_json, validate_config, now

def launch():
    app = tk.Tk()
    app.title('新闻联播备考日报 · 本地版')
    app.geometry('980x790')
    app.minsize(880, 650)
    style = ttk.Style(app)
    style.theme_use('clam')
    style.configure('TLabel', font=('Microsoft YaHei', 10))
    style.configure('TButton', font=('Microsoft YaHei', 10), padding=5)
    outer = ttk.Frame(app, padding=16)
    outer.pack(fill='both', expand=True)
    ttk.Label(outer, text='新闻联播 · 每晚备考日报', font=('Microsoft YaHei', 21, 'bold')).pack(anchor='w')
    ttk.Label(outer, text='上海理工大学 701 / 870 · 江苏 / 上海 / 浙江 / 安徽 · 不下载视频').pack(anchor='w', pady=(4,12))
    notebook = ttk.Notebook(outer)
    notebook.pack(fill='both', expand=True)
    home, settings, help_tab = [ttk.Frame(notebook, padding=12) for _ in range(3)]
    notebook.add(home, text='运行与报告')
    notebook.add(settings, text='本地配置')
    notebook.add(help_tab, text='使用说明')
    c = load_config()
    try:
        secrets = load_secrets()
    except Exception:
        secrets = {}
        messagebox.showwarning('密钥未能读取', '请使用原Windows账号打开，或重新填写密钥。')
    variables = {}
    fields = [
        ('开始补跑日期', 'start_date', False), ('每日开始时间（北京时间）', 'run_time', False),
        ('单轮最多补跑天数', 'max_days_per_run', False),
        ('模型 API 基础地址（含 /v1 等前缀）', 'model_base_url', False), ('模型名称', 'model_name', False),
        ('模型 API Key', 'model_api_key', True),
        ('腾讯云 COS 区域，例如 ap-shanghai', 'cos_region', False), ('COS 桶名（包含 APPID）', 'cos_bucket', False),
        ('COS SecretId', 'cos_secret_id', True), ('COS SecretKey', 'cos_secret_key', True),
        ('PDF 链接有效小时数', 'link_hours', False), ('Server酱 SendKey', 'serverchan_sendkey', True),
    ]
    settings.columnconfigure(1, weight=1)
    for row, (label, key, hidden) in enumerate(fields):
        ttk.Label(settings, text=label).grid(row=row, column=0, sticky='w', pady=4, padx=(0,12))
        variables[key] = tk.StringVar(value=str(secrets.get(key, '') if hidden else c.get(key, '')))
        ttk.Entry(settings, textvariable=variables[key], show='•' if hidden else '', width=57).grid(row=row, column=1, sticky='ew', pady=4)
    for i, (key, label) in enumerate([('upload_enabled','上传 PDF 到私有 COS 存储'), ('push_enabled','微信通知（摘要＋下载链接）')], len(fields)):
        variables[key] = tk.BooleanVar(value=c[key])
        ttk.Checkbutton(settings, text=label, variable=variables[key]).grid(row=i, column=1, sticky='w', pady=4)
    status_var = tk.StringVar()
    def save():
        try:
            updated = load_config()
            for key, var in variables.items():
                if key not in SECRET_NAMES:
                    updated[key] = var.get()
            for key in ('max_days_per_run','link_hours'):
                updated[key] = int(updated[key])
            validate_config(updated)
            # Preserve encrypted values only when explicitly saved; empty means clear.
            save_secrets({k:variables[k].get().strip() for k in SECRET_NAMES})
            atomic_json(ROOT/'config.json', updated)
            refresh()
            status_var.set('配置已保存；更改运行时间后请重新安装自动任务。')
            return True
        except Exception as e:
            messagebox.showerror('配置未保存', str(e) if isinstance(e,(RuntimeError,ValueError)) else type(e).__name__)
            return False
    ttk.Button(settings, text='保存配置（密钥用 Windows 账号加密）', command=save).grid(row=len(fields)+2,column=1,sticky='w',pady=8)
    ttk.Label(settings, textvariable=status_var, wraplength=720).grid(row=len(fields)+3,column=0,columnspan=2,sticky='w')
    readiness = tk.StringVar()
    ttk.Label(home, textvariable=readiness, wraplength=850, foreground='#087E85').pack(anchor='w', pady=(0,10))
    row = ttk.Frame(home); row.pack(fill='x')
    day_var = tk.StringVar(value=(now().date()-timedelta(days=1)).isoformat())
    ttk.Label(row, text='指定日期：').pack(side='left')
    ttk.Entry(row, textvariable=day_var, width=13).pack(side='left',padx=5)
    events = queue.Queue()
    running = [False]
    output = tk.Text(home, height=13, wrap='word', font=('Microsoft YaHei',10), bg='#F3F7F9', relief='flat')
    def log(s):
        events.put(str(s))
    def background(fn):
        if running[0]:
            messagebox.showinfo('任务运行中','请等待当前任务结束。'); return
        if not save():
            return
        running[0] = True
        def worker():
            try:
                fn()
            except Exception as e:
                log(str(e) if isinstance(e,(RuntimeError,ValueError)) else '执行失败：'+type(e).__name__)
            finally:
                events.put(None)
        threading.Thread(target=worker, daemon=False).start()
    def run_task():
        from .pipeline import run
        run(log=log)
    def reading_task(day):
        from .pipeline import make_reading
        path = make_reading(day, load_config(), log=log)
        os.startfile(path)
    ttk.Button(row,text='生成原文阅读版（无需 API）',command=lambda: background(lambda d=day_var.get(): reading_task(d))).pack(side='left',padx=4)
    ttk.Button(row,text='执行自动补跑',command=lambda: background(run_task)).pack(side='left',padx=4)
    def open_folder():
        (ROOT/'output/pdf').mkdir(parents=True,exist_ok=True)
        os.startfile(ROOT/'output/pdf')
    ttk.Button(row,text='打开报告目录',command=open_folder).pack(side='left',padx=4)
    controls = ttk.Frame(home); controls.pack(fill='x',pady=10)
    def schedule(remove=False):
        if not remove and not save(): return
        if not remove:
            conf, sec = load_config(), load_secrets()
            if not all((conf['model_base_url'],conf['model_name'],sec.get('model_api_key'))):
                messagebox.showinfo('先配置模型','自动任务需要模型 API；现在可先使用原文阅读版。'); return
        try:
            kwargs = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
            cmd = ['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(ROOT/'scripts/task.ps1'),
                   '-PythonPath',sys.executable,'-At',load_config()['run_time']]
            if remove: cmd.append('-Remove')
            result = subprocess.run(cmd,capture_output=True,timeout=40,**kwargs)
            if result.returncode:
                raise RuntimeError('计划任务设置失败，请查看使用说明中的手动安装方法')
            messagebox.showinfo('计划任务','已移除自动任务。' if remove else '已安装：登录后检查，每30分钟补跑；21:30起处理当天新闻。')
        except Exception as e:
            messagebox.showerror('计划任务',str(e))
    ttk.Button(controls,text='安装/更新开机自动任务',command=schedule).pack(side='left',padx=3)
    ttk.Button(controls,text='移除自动任务',command=lambda:schedule(True)).pack(side='left',padx=3)
    tree = ttk.Treeview(home,columns=('date','status','delivery','error'),show='headings',height=7)
    for col,label,width in [('date','日期',100),('status','处理阶段',100),('delivery','微信状态',110),('error','待处理事项',510)]:
        tree.heading(col,text=label); tree.column(col,width=width,stretch=(col=='error'))
    tree.pack(fill='x',pady=8)
    def refresh():
        conf, sec = load_config(), load_secrets()
        ready = all((conf['model_base_url'], conf['model_name'], sec.get('model_api_key')))
        readiness.set(('模型已配置。' if ready else '尚未配置模型：可生成原文阅读版；AI考点分析尚未启用。') +
                      (' PDF上传已开启。' if conf['upload_enabled'] else ' PDF上传未开启。')+
                      (' 微信推送已开启。' if conf['push_enabled'] else ' 微信推送未开启。'))
        from .state import State
        state = State(ROOT)
        try:
            tree.delete(*tree.get_children())
            for r in state.all()[:50]:
                tree.insert('', 'end', values=(r['day'], {'pending':'待重试','rendered':'本地PDF','uploaded':'已上传'}.get(r['status'],r['status']),
                            {0:'未推送',1:'服务已接受',2:'结果待核对'}[r['notified']],r['error']))
        finally: state.close()
    def resolve(received):
        if running[0]:
            messagebox.showinfo('任务运行中','请等待任务结束后核对推送状态。'); return
        if messagebox.askyesno('核对推送', '将所有“结果待核对”标记为已收到？' if received else '确认已核对手机和服务后台？允许重试可能重复发送。'):
            from .pipeline import resolve_notifications
            resolve_notifications(ROOT,received); refresh()
    buttons = ttk.Frame(home); buttons.pack(fill='x')
    ttk.Button(buttons,text='刷新状态',command=refresh).pack(side='left',padx=3)
    ttk.Button(buttons,text='待核对通知：我已收到',command=lambda:resolve(True)).pack(side='left',padx=3)
    ttk.Button(buttons,text='待核对通知：允许重试',command=lambda:resolve(False)).pack(side='left',padx=3)
    output.pack(fill='both',expand=True,pady=10)
    output.insert('end','首次使用：可直接生成昨日原文阅读版。填写模型、COS、Server酱后启用完整流程。\n')
    help_text = tk.Text(help_tab,wrap='word',font=('Microsoft YaHei',10),relief='flat')
    help_text.pack(fill='both',expand=True)
    help_text.insert('end',(ROOT/'README.md').read_text(encoding='utf-8'))
    help_text.configure(state='disabled')
    def poll():
        try:
            while True:
                value = events.get_nowait()
                if value is None:
                    running[0] = False
                    refresh()
                else:
                    output.insert('end',value+'\n'); output.see('end')
        except queue.Empty: pass
        app.after(200,poll)
    def close():
        if running[0]:
            messagebox.showinfo('任务尚未结束','请等待任务完成再关闭窗口，或在任务管理器终止；已保存的结果会在下次继续。')
        else: app.destroy()
    app.protocol('WM_DELETE_WINDOW',close)
    refresh(); poll(); app.mainloop()
