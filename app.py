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
from version import VERSION

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f'拾聊 {VERSION} · 微信聊天记录导出')
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
        self.status = tk.StringVar(value='请先登录电脑微信，然后点击“读取本地记录”。')
        self.selection_status = tk.StringVar(value='尚未读取会话')
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('.',font=('Microsoft YaHei UI',10))
        style.configure('Treeview',rowheight=29)
        body = ttk.Frame(self,padding=22)
        body.pack(fill='both',expand=True)
        ttk.Label(body,text='拾聊',font=('Microsoft YaHei UI',23,'bold')).pack(anchor='w')
        ttk.Label(body,text='批量导出本机聊天记录 · HTML / TXT / JSON · 无固定条数上限').pack(anchor='w',pady=(0,15))
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
        dates = ttk.Frame(body)
        dates.pack(fill='x',pady=12)
        ttk.Label(dates,text='日期范围').pack(side='left')
        ttk.Entry(dates,textvariable=self.start,width=13).pack(side='left',padx=8)
        ttk.Label(dates,text='至').pack(side='left')
        ttk.Entry(dates,textvariable=self.end,width=13).pack(side='left',padx=8)
        ttk.Label(dates,text='YYYY-MM-DD，留空为全部').pack(side='left')
        ttk.Checkbutton(dates,text='导出本地图片',variable=self.pictures).pack(side='right')
        outrow = ttk.Frame(body)
        outrow.pack(fill='x')
        ttk.Label(outrow,text='保存位置').pack(side='left')
        ttk.Entry(outrow,textvariable=self.output_dir).pack(side='left',fill='x',expand=True,padx=8)
        ttk.Button(outrow,text='选择位置',command=self.choose_output).pack(side='left')
        actions = ttk.Frame(body)
        actions.pack(fill='x',pady=12)
        self.export_button = ttk.Button(actions,text='导出所选会话',command=self.export,state='disabled')
        self.export_button.pack(side='left')
        self.cancel_button = ttk.Button(actions,text='取消任务',command=self.cancel.set,state='disabled')
        self.cancel_button.pack(side='left',padx=8)
        ttk.Button(actions,text='打开导出文件夹',command=self.open_output).pack(side='right')
        self.progressbar = ttk.Progressbar(body,mode='indeterminate')
        self.progressbar.pack(fill='x')
        ttk.Label(body,textvariable=self.status,wraplength=900).pack(anchor='w',pady=(8,0))
        self.protocol('WM_DELETE_WINDOW',self.on_close)
        self.after(100,self.poll)

    def busy(self):
        return self.worker is not None and self.worker.is_alive()

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
            self.events.put(('connected',s.chats))
        self.chats = {}
        self.selected.clear()
        self.render_chats()
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
        try:
            start = dt.datetime.strptime(self.start.get().strip(),'%Y-%m-%d') if self.start.get().strip() else None
            end = dt.datetime.strptime(self.end.get().strip(),'%Y-%m-%d')+dt.timedelta(days=1) if self.end.get().strip() else None
            if start and end and start >= end:
                raise ValueError()
        except ValueError:
            messagebox.showerror('日期格式','请输入 YYYY-MM-DD，并确保结束日期不早于开始日期。')
            return
        directory = self.output_dir.get().strip()
        if not directory:
            messagebox.showerror('保存位置','请选择保存位置。')
            return
        chats = [c for k,c in self.chats.items() if k in self.selected]
        pictures = self.pictures.get()
        def task():
            output,summary = export_chats(self.session,chats,directory,start.timestamp() if start else None,end.timestamp() if end else None,pictures)
            self.events.put(('exported',(output,summary)))
        self.status.set('正在准备导出…')
        self.run_task(task)

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
                    self.chats = {c['id']:c for c in value}
                    self.render_chats()
                    self.status.set('读取完成。请勾选会话后导出；图片仅导出电脑上已有的文件。')
                elif event == 'exported':
                    self.last_output,summary = value
                    self.status.set('导出完成：%d 条消息，%d 张可查看图片，%d 条图片记录未取得可查看图片。' % (summary['messages'],summary['images'],summary['unavailable_images']))
                elif event == 'done':
                    self.progressbar.stop()
                    self.connect_button.configure(state='normal')
                    self.export_button.configure(state='normal' if self.session else 'disabled')
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
        window.update_idletasks()
        window.destroy()
        output = io.StringIO()
        result = unittest.TextTestRunner(stream=output).run(unittest.defaultTestLoader.loadTestsFromModule(test_export))
        Path(sys.argv[2]).write_text(json.dumps({'passed':result.wasSuccessful(),'tests':result.testsRun,'details':output.getvalue()},ensure_ascii=False,indent=2),encoding='utf8')
        sys.exit(0 if result.wasSuccessful() else 1)
    else:
        App().mainloop()
