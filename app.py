"""Shiliao Windows desktop GUI."""
import datetime as dt
import os
import sys
from pathlib import Path
from offline_guard import enforce_offline
enforce_offline()
# Source builds use the official Tcl/Tk scripts vendored with this project.
if not getattr(sys,'frozen',False):
    vendor = Path(__file__).resolve().parent/'vendor'
    if (vendor/'tcl8.6'/'init.tcl').is_file():
        os.chdir(Path(__file__).resolve().parent)
        os.environ['TCL_LIBRARY'] = 'vendor/tcl8.6'
        os.environ['TK_LIBRARY'] = 'vendor/tk8.6'
else:
    os.chdir(sys._MEIPASS)
    os.environ['TCL_LIBRARY'] = '_tcl_data'
    os.environ['TK_LIBRARY'] = '_tk_data'
import queue
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from database import Session
from exporter import export_chats
from moments import export_moments
from version import VERSION
from time_filter import parse_time_range, quick_range

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f'拾聊 {VERSION} · 微信聊天与朋友圈导出')
        self.geometry('980x740')
        self.minsize(780,600)
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.session = None
        self.worker = None
        self.closing = False
        self.selected = set()
        self.chats = {}
        self.last_output = None
        self.root_dir = tk.StringVar()
        self.output_dir = tk.StringVar(value=str(Path.home()/'Documents'/'拾聊导出'))
        self.search = tk.StringVar()
        self.start = tk.StringVar()
        self.end = tk.StringVar()
        self.pictures = tk.BooleanVar(value=True)
        self.moment_cache = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value='请先登录电脑微信，然后点击“读取本地记录”。')
        self.selection_status = tk.StringVar(value='尚未读取会话')
        self.moment_status = tk.StringVar(value='尚未读取朋友圈')
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('.',font=('Microsoft YaHei UI',10))
        style.configure('Treeview',rowheight=29)
        body = ttk.Frame(self,padding=22)
        body.pack(fill='both',expand=True)
        ttk.Label(body,text='拾聊',font=('Microsoft YaHei UI',23,'bold')).pack(anchor='w')
        ttk.Label(body,text='导出本机聊天记录与朋友圈 · HTML / TXT / JSON').pack(anchor='w',pady=(0,15))
        source = ttk.Frame(body)
        source.pack(fill='x')
        ttk.Label(source,text='数据目录').pack(side='left')
        self.source_entry = ttk.Entry(source,textvariable=self.root_dir)
        self.source_entry.pack(side='left',fill='x',expand=True,padx=8)
        ttk.Button(source,text='选择目录',command=self.choose_source).pack(side='left')
        self.connect_button = ttk.Button(source,text='读取本地记录',command=self.connect)
        self.connect_button.pack(side='left',padx=(8,0))
        ttk.Label(body,text='留空可自动查找；也可选择 xwechat_files 或其中的账号目录。').pack(anchor='w',pady=(3,12))
        searchrow = ttk.Frame(body)
        searchrow.pack(fill='x')
        ttk.Label(searchrow,text='查找会话').pack(side='left')
        ttk.Entry(searchrow,textvariable=self.search).pack(side='left',fill='x',expand=True,padx=8)
        ttk.Button(searchrow,text='全选当前结果',command=self.select_visible).pack(side='left')
        ttk.Button(searchrow,text='清空选择',command=self.clear_selection).pack(side='left',padx=(8,0))
        self.search.trace_add('write',lambda *_:self.render_chats())
        listframe = ttk.Frame(body)
        listframe.pack(fill='both',expand=True,pady=9)
        self.tree = ttk.Treeview(listframe,columns=('selected','name','count','last'),show='headings',selectmode='none')
        for col,title,width in [('selected','选择',55),('name','会话',420),('count','消息数',100),('last','最近消息',160)]:
            self.tree.heading(col,text=title)
            self.tree.column(col,width=width,stretch=col=='name')
        self.tree.pack(side='left',fill='both',expand=True)
        scroll = ttk.Scrollbar(listframe,orient='vertical',command=self.tree.yview)
        scroll.pack(side='right',fill='y')
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind('<Button-1>',self.toggle)
        ttk.Label(body,textvariable=self.selection_status).pack(anchor='w')
        ttk.Label(body,textvariable=self.moment_status).pack(anchor='w')
        dates = ttk.Frame(body)
        dates.pack(fill='x',pady=(12,4))
        ttk.Label(dates,text='时间范围').pack(side='left')
        ttk.Entry(dates,textvariable=self.start,width=17).pack(side='left',padx=8)
        ttk.Label(dates,text='至').pack(side='left')
        ttk.Entry(dates,textvariable=self.end,width=17).pack(side='left',padx=8)
        ttk.Checkbutton(dates,text='导出本地图片',variable=self.pictures).pack(side='right')
        ttk.Label(body,text='可填 2026-11-09-1 表示 1:00；结束整点不包含，结束日期包含全天。留空不限。').pack(anchor='w')
        shortcuts = ttk.Frame(body)
        shortcuts.pack(fill='x',pady=(4,12))
        self.time_buttons = {}
        for preset,label in [('today','今天'),('yesterday','昨天'),('week','一周前至今'),('all','全部时间')]:
            button = ttk.Button(shortcuts,text=label,command=lambda p=preset:self.set_time_range(p))
            button.pack(side='left',padx=(0,8))
            self.time_buttons[preset] = button
        ttk.Checkbutton(shortcuts,text='附带整个朋友圈图片缓存（所有日期）',
                        variable=self.moment_cache).pack(side='right')
        outrow = ttk.Frame(body)
        outrow.pack(fill='x')
        ttk.Label(outrow,text='保存位置').pack(side='left')
        ttk.Entry(outrow,textvariable=self.output_dir).pack(side='left',fill='x',expand=True,padx=8)
        ttk.Button(outrow,text='选择位置',command=self.choose_output).pack(side='left')
        actions = ttk.Frame(body)
        actions.pack(fill='x',pady=12)
        self.export_button = ttk.Button(actions,text='导出所选会话',command=self.export,state='disabled')
        self.export_button.pack(side='left')
        self.moment_button = ttk.Button(actions,text='导出朋友圈',command=self.export_moments,state='disabled')
        self.moment_button.pack(side='left',padx=(8,0))
        self.cancel_button = ttk.Button(actions,text='取消任务',command=self.cancel.set,state='disabled')
        self.cancel_button.pack(side='left',padx=8)
        ttk.Button(actions,text='打开导出文件夹',command=self.open_output).pack(side='right')
        self.progressbar = ttk.Progressbar(body,mode='indeterminate')
        self.progressbar.pack(fill='x')
        ttk.Label(body,textvariable=self.status,wraplength=900).pack(anchor='w',pady=(8,0))
        self.protocol('WM_DELETE_WINDOW',self.on_close)
        self.after(100,self.poll)

    def busy(self):
        return self.worker is not None

    def choose_source(self):
        if not self.busy():
            value = filedialog.askdirectory(title='选择微信数据目录')
            if value:
                self.root_dir.set(value)

    def choose_output(self):
        value = filedialog.askdirectory(title='选择导出位置')
        if value:
            self.output_dir.set(value)

    def run_task(self,fn):
        if self.busy():
            return
        self.cancel.clear()
        self.connect_button.configure(state='disabled')
        self.export_button.configure(state='disabled')
        self.moment_button.configure(state='disabled')
        self.cancel_button.configure(state='normal')
        self.progressbar.start(12)
        def target():
            try:
                fn()
            except InterruptedError:
                self.events.put(('status','任务已取消；未完成的导出会保留在“.未完成”文件夹。'))
            except Exception as e:
                self.events.put(('error',str(e)))
            finally:
                self.events.put(('done',None))
        self.worker = threading.Thread(target=target,daemon=False)
        self.worker.start()

    def connect(self):
        root = self.root_dir.get().strip() or None
        def task():
            if self.session:
                self.session.close()
                self.session = None
            s = Session(progress=lambda msg:self.events.put(('status',msg)),cancel=self.cancel)
            try:
                s.connect(root)
            except BaseException:
                s.close()
                raise
            self.session = s
            self.events.put(('connected',(s.chats,s.moment_status)))
        self.chats = {}
        self.selected.clear()
        self.render_chats()
        self.moment_status.set('正在读取朋友圈…')
        self.status.set('正在读取本地记录，请稍候…')
        self.run_task(task)

    def render_chats(self):
        self.tree.delete(*self.tree.get_children())
        term = self.search.get().casefold().strip()
        for key,c in self.chats.items():
            if term and term not in (c['name']+' '+c['username']).casefold():
                continue
            self.tree.insert('', 'end',iid=key,values=('☑' if key in self.selected else '☐',c['name'],c['count'],dt.datetime.fromtimestamp(c['last']).strftime('%Y-%m-%d %H:%M')))
        self.update_selection()

    def update_selection(self):
        self.selection_status.set('共 %d 个会话，已选 %d 个 · %s 条消息（筛选日期前）' % (len(self.chats),len(self.selected),format(sum(self.chats[k]['count'] for k in self.selected),',')))

    def toggle(self,event):
        key = self.tree.identify_row(event.y)
        if key and not self.busy():
            if key in self.selected:
                self.selected.remove(key)
            else:
                self.selected.add(key)
            self.tree.set(key,'selected','☑' if key in self.selected else '☐')
            self.update_selection()

    def select_visible(self):
        if not self.busy():
            self.selected.update(self.tree.get_children())
            self.render_chats()

    def clear_selection(self):
        if not self.busy():
            self.selected.clear()
            self.render_chats()

    def export(self):
        if not self.session or not self.selected:
            messagebox.showinfo('选择会话','请先勾选需要导出的会话。')
            return
        chats = [c for k,c in self.chats.items() if k in self.selected]
        self.start_export(lambda *args: export_chats(self.session,chats,*args),'exported')

    def export_moments(self):
        include_cache = self.moment_cache.get()
        self.start_export(lambda *args: export_moments(self.session,*args,include_cache=include_cache),
                          'moments_exported')

    def start_export(self, exporter, event):
        try:
            start,end = parse_time_range(self.start.get(),self.end.get())
        except ValueError as exc:
            messagebox.showerror('时间范围',str(exc))
            return
        directory = self.output_dir.get().strip()
        if not directory:
            messagebox.showerror('保存位置','请选择保存位置。')
            return
        pictures = self.pictures.get()
        def task():
            self.events.put((event,exporter(directory,start,end,pictures)))
        self.status.set('正在准备导出…')
        self.run_task(task)

    def set_time_range(self, preset):
        start,end = quick_range(preset)
        self.start.set(start)
        self.end.set(end)

    def open_output(self):
        path = self.last_output or Path(self.output_dir.get())
        if path.is_dir():
            os.startfile(str(path))

    def poll(self):
        try:
            while True:
                event,value = self.events.get_nowait()
                if event == 'status':
                    self.status.set(value)
                elif event == 'error':
                    self.status.set('未完成：'+value)
                    if not self.closing:
                        messagebox.showerror('任务未完成',value)
                elif event == 'connected':
                    chats,moment_status = value
                    self.chats = {c['id']:c for c in chats}
                    self.moment_status.set(moment_status)
                    self.render_chats()
                    self.status.set('读取完成。时间范围和图片选项适用于两种导出。')
                elif event == 'exported':
                    self.last_output,summary = value
                    self.status.set('导出完成：%d 条消息，%d 张可查看图片，%d 条图片记录未取得可查看图片。' % (summary['messages'],summary['images'],summary['unavailable_images']))
                elif event == 'moments_exported':
                    self.last_output,summary = value
                    status = f"导出完成 · {summary['moments']:,} 条朋友圈，{summary['links']:,} 个链接"
                    if summary['include_images']:
                        status += (f"，{summary['verified_images']:,} 张已校验图片，"
                                   f"{summary['candidate_images']:,} 张待核对，"
                                   f"{summary['unavailable_images']:,} 张未匹配")
                    if summary['include_cache']:
                        status += f"，本机图片缓存 {summary['cached_images']:,} 张"
                    self.status.set(status + '。')
                elif event == 'done':
                    self.worker = None
                    self.progressbar.stop()
                    self.connect_button.configure(state='normal')
                    self.export_button.configure(state='normal' if self.session else 'disabled')
                    self.moment_button.configure(state='normal' if self.session and self.session.moment_count else 'disabled')
                    if self.session is None:
                        self.moment_status.set('尚未读取朋友圈')
                    self.cancel_button.configure(state='disabled')
        except queue.Empty:
            pass
        if self.closing and not self.busy():
            if self.session:
                self.session.close()
            self.destroy()
            return
        self.after(100,self.poll)

    def on_close(self):
        self.closing = True
        self.cancel.set()
        self.status.set('正在停止任务并清理临时快照…')

if __name__ == '__main__':
    import sys
    if len(sys.argv) == 3 and sys.argv[1] == '--self-test':
        import io
        import json
        import unittest
        import av
        import zstandard
        import test_export
        window = App()
        window.withdraw()
        window.chats = {'a':{'id':'a','name':'测试会话','username':'test','count':302,'last':1700000000}}
        window.render_chats()
        window.select_visible()
        assert window.selected == {'a'}
        window.search.set('不存在')
        assert not window.tree.get_children()
        assert window.selected == {'a'}
        window.clear_selection()
        for preset,button in window.time_buttons.items():
            button.invoke()
            assert (window.start.get(),window.end.get()) == quick_range(preset)
        test_export.check_moments_button(window)
        window.update_idletasks()
        window.destroy()
        output = io.StringIO()
        result = unittest.TextTestRunner(stream=output).run(unittest.defaultTestLoader.loadTestsFromModule(test_export))
        Path(sys.argv[2]).write_text(json.dumps({'passed':result.wasSuccessful(),'tests':result.testsRun,'details':output.getvalue()},ensure_ascii=False,indent=2),encoding='utf8')
        sys.exit(0 if result.wasSuccessful() else 1)
    else:
        App().mainloop()
