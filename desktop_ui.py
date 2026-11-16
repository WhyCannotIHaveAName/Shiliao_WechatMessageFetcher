"""Two independent workspaces for the Windows desktop application."""
import datetime as dt
import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, font, messagebox, ttk

from database import Session
from exporter import export_chats
from moments import export_moments, load_moments
from time_filter import parse_time_range, quick_range
from version import VERSION

PAPER = '#f8f6f1'
PANEL = '#fffdf9'
SIDEBAR = '#ede9e1'
LINE = '#ded7cb'
INK = '#28241f'
MUTED = '#665e53'
ACCENT = '#9f462f'
ACTIVE = '#f0ded3'
PREVIEW_LIMIT = 200


def configure_style(window):
    style = ttk.Style(window)
    style.theme_use('clam')
    family = 'Microsoft YaHei UI'
    default = (family, 10)
    style.configure('.', font=default, background=PAPER, foreground=INK)
    style.configure('Sidebar.TFrame', background=SIDEBAR)
    style.configure('Card.TFrame', background=PANEL)
    style.configure('Card.TLabel', background=PANEL)
    style.configure('Section.TLabel', background=PANEL, font=(family, 11, 'bold'))
    style.configure('Hint.TLabel', foreground=MUTED, background=PANEL, font=(family, 9))
    style.configure('Status.TLabel', background=PANEL, foreground=MUTED, font=(family, 9))
    style.configure('Title.TLabel', font=(family, 22, 'bold'))
    style.configure('Subtitle.TLabel', foreground=MUTED)
    style.configure('Brand.TLabel', background=SIDEBAR, font=(family, 19, 'bold'))
    style.configure('Version.TLabel', background=SIDEBAR, foreground=MUTED, font=(family, 9))
    style.configure('TButton', width=0, background=PANEL, bordercolor=LINE, lightcolor=PANEL,
                    darkcolor=PANEL, padding=(9, 6))
    style.map('TButton', background=[('pressed', SIDEBAR), ('active', PAPER)],
              bordercolor=[('focus', ACCENT)], foreground=[('disabled', '#81786a')])
    style.configure('Accent.TButton', background=ACCENT, foreground='white',
                    bordercolor=ACCENT, lightcolor=ACCENT, darkcolor=ACCENT, padding=(12, 6))
    style.map('Accent.TButton', background=[('disabled', '#e9e0d5'), ('pressed', '#763625'),
                                           ('active', '#8d3f2b')],
              foreground=[('disabled', '#6c6052'), ('!disabled', 'white')],
              bordercolor=[('focus', INK)])
    for name, background in (('Nav', SIDEBAR), ('Selected.Nav', ACTIVE)):
        style.configure(f'{name}.TButton', background=background, borderwidth=0,
                        lightcolor=background, darkcolor=background, bordercolor=background,
                        anchor='w', padding=(12, 9))
        style.map(f'{name}.TButton', background=[('active', ACTIVE)], bordercolor=[('focus', ACCENT)])
    style.configure('TEntry', fieldbackground='#ffffff', bordercolor=LINE,
                    lightcolor='#ffffff', darkcolor='#ffffff', padding=5)
    style.map('TEntry', bordercolor=[('focus', ACCENT)])
    style.configure('TCheckbutton', background=PANEL, padding=(0, 4))
    style.map('TCheckbutton', background=[('active', PANEL)])
    style.configure('Treeview', fieldbackground='#ffffff', background='#ffffff',
                    bordercolor=LINE, rowheight=font.Font(window, font=default).metrics('linespace') + 11)
    style.configure('Treeview.Heading', background=SIDEBAR, foreground=INK,
                    bordercolor=LINE, lightcolor=SIDEBAR, darkcolor=SIDEBAR, padding=(4, 5))
    style.map('Treeview', background=[('selected', ACTIVE)], foreground=[('selected', INK)])
    style.configure('Vertical.TScrollbar', troughcolor=PAPER, background='#d7cec0',
                    bordercolor=LINE, lightcolor='#d7cec0', darkcolor='#d7cec0', arrowsize=12)
    style.map('Vertical.TScrollbar', background=[('active', '#b9a893')])
    style.configure('Horizontal.TProgressbar', background=ACCENT, troughcolor=PANEL,
                    bordercolor=PANEL, lightcolor=ACCENT, darkcolor=ACCENT, thickness=3)


class ScrollBody(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, background=PAPER, highlightthickness=0, yscrollincrement=24)
        scrollbar = ttk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.content = ttk.Frame(self.canvas, padding=(20, 14, 16, 14))
        self.window_id = self.canvas.create_window(0, 0, anchor='nw', window=self.content)
        self.content.bind('<Configure>', self.update_region)
        self.canvas.bind('<Configure>', self.fit_width)

    def fit_width(self, event):
        self.canvas.itemconfigure(self.window_id, width=event.width)
        self.update_region()

    def update_region(self, event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox(self.window_id))

    def contains(self, widget):
        return widget is self.canvas or str(widget).startswith(str(self.content) + '.')

    def show_focused(self, widget):
        if not self.contains(widget) or widget is self.canvas:
            return
        top = widget.winfo_rooty() - self.canvas.winfo_rooty()
        bottom = top + widget.winfo_height()
        height = self.canvas.winfo_height()
        if top < 0 or bottom > height:
            shift = top - 8 if top < 0 else bottom - height + 8
            offset = self.canvas.canvasy(0) + shift
            self.canvas.yview_moveto(offset / max(1, self.content.winfo_height()))


class ExportPage(ttk.Frame):
    def __init__(self, parent, app, scope):
        super().__init__(parent)
        self.app = app
        self.scope = scope
        self.is_chat = scope == 'chat'
        self.title = '聊天记录' if self.is_chat else '朋友圈'
        self.session = None
        self.timeline = None
        self.loaded_root = None
        self.last_output = None
        self.selected = set()
        self.chats = {}
        self.root_dir = tk.StringVar(self)
        self.output_dir = tk.StringVar(self, value=str(Path.home() / 'Documents' / '拾聊导出'))
        self.search = tk.StringVar(self)
        self.start = tk.StringVar(self)
        self.end = tk.StringVar(self)
        self.pictures = tk.BooleanVar(self, value=True)
        self.include_cache = tk.BooleanVar(self, value=False)
        self.status = tk.StringVar(self, value=f'请登录电脑微信，然后点击「读取{self.title}」。')
        self.selection = tk.StringVar(self)
        self.preview_job = None
        self.body = ScrollBody(self)
        self.body.grid(row=0, column=0, sticky='nsew')
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        content = self.body.content
        ttk.Label(content, text=self.title, style='Title.TLabel').pack(anchor='w')
        subtitle = '查找会话，导出选中的聊天记录。' if self.is_chat else '按正文、发布者或分享链接查找本机动态。'
        self.wrap_label(content, text=subtitle, style='Subtitle.TLabel', pady=(0, 11))

        source = self.card(content, '微信数据目录')
        source_line = ttk.Frame(source, style='Card.TFrame')
        source_line.pack(fill='x')
        self.source_entry = ttk.Entry(source_line, textvariable=self.root_dir, width=8)
        self.source_entry.pack(side='left', fill='x', expand=True)
        self.source_button = ttk.Button(source_line, text='选择目录',
                                        command=lambda: self.choose_directory(self.root_dir, '选择微信数据目录'))
        self.source_button.pack(side='left', padx=(8, 0))
        self.read_button = ttk.Button(source_line, text=f'读取{self.title}', command=self.read,
                                      style='Accent.TButton')
        self.read_button.pack(side='left', padx=(8, 0))
        self.wrap_label(source, text='留空自动查找，也可指定 xwechat_files 或账号目录。',
                        style='Hint.TLabel', pady=(4, 0))

        results = self.card(content, '会话' if self.is_chat else '动态预览')
        search_line = ttk.Frame(results, style='Card.TFrame')
        search_line.pack(fill='x', pady=(0, 7))
        label = '查找会话' if self.is_chat else '关键词'
        ttk.Label(search_line, text=label, style='Card.TLabel').pack(side='left', padx=(0, 8))
        self.search_entry = ttk.Entry(search_line, textvariable=self.search, width=1)
        self.search_entry.pack(side='left', fill='x', expand=True)
        if self.is_chat:
            ttk.Button(search_line, text='全选结果', command=self.select_visible).pack(side='left', padx=(8, 0))
            ttk.Button(search_line, text='清空选择', command=self.clear_selection).pack(side='left', padx=(8, 0))
        self.make_results(results)
        self.wrap_label(results, textvariable=self.selection, style='Hint.TLabel', pady=(5, 0))

        settings = self.card(content, '导出设置')
        dates = ttk.Frame(settings, style='Card.TFrame')
        dates.pack(fill='x')
        dates.columnconfigure((1, 3), weight=1, uniform='date')
        ttk.Label(dates, text='时间范围', style='Card.TLabel').grid(row=0, column=0, padx=(0, 8))
        self.start_entry = ttk.Entry(dates, textvariable=self.start, width=1)
        self.start_entry.grid(row=0, column=1, sticky='ew')
        ttk.Label(dates, text='至', style='Card.TLabel').grid(row=0, column=2, padx=7)
        ttk.Entry(dates, textvariable=self.end, width=1).grid(row=0, column=3, sticky='ew')
        self.wrap_label(settings, text='格式 YYYY-MM-DD 或 YYYY-MM-DD-H，留空不限。',
                        style='Hint.TLabel', pady=(4, 4))
        shortcuts = ttk.Frame(settings, style='Card.TFrame')
        shortcuts.pack(fill='x')
        self.time_buttons = {}
        for preset, label in [('today', '今天'), ('yesterday', '昨天'), ('week', '一周前至今'), ('all', '全部时间')]:
            button = ttk.Button(shortcuts, text=label, command=lambda name=preset: self.set_time_range(name))
            self.time_buttons[preset] = button
        shortcuts.bind('<Configure>', self.layout_shortcuts)
        self.layout_shortcuts()
        ttk.Checkbutton(settings, text='导出本地图片', variable=self.pictures).pack(anchor='w', pady=(5, 0))
        if not self.is_chat:
            ttk.Checkbutton(settings, text='附带全部图片缓存（所有日期）',
                            variable=self.include_cache).pack(anchor='w')
        destination = ttk.Frame(settings, style='Card.TFrame')
        destination.pack(fill='x', pady=(5, 0))
        ttk.Label(destination, text='保存位置', style='Card.TLabel').pack(side='left', padx=(0, 8))
        self.output_entry = ttk.Entry(destination, textvariable=self.output_dir, width=8)
        self.output_entry.pack(side='left', expand=True, fill='x')
        ttk.Button(destination, text='选择位置',
                   command=lambda: self.choose_directory(self.output_dir, '选择导出位置')).pack(side='left', padx=(8, 0))

        footer = ttk.Frame(self, style='Card.TFrame', padding=(20, 7, 16, 7))
        footer.grid(row=1, column=0, sticky='ew')
        self.footer = footer
        footer.columnconfigure(2, weight=1)
        self.progressbar = ttk.Progressbar(footer, mode='indeterminate')
        self.progressbar.grid(row=0, column=0, columnspan=3, sticky='ew', pady=(0, 3))
        self.status_label = ttk.Label(footer, textvariable=self.status, style='Status.TLabel')
        self.status_label.grid(row=1, column=0, columnspan=3, sticky='ew', pady=(0, 5))
        export_title = '导出所选会话' if self.is_chat else '导出朋友圈'
        self.export_button = ttk.Button(footer, text=export_title, style='Accent.TButton', command=self.export)
        self.export_button.grid(row=2, column=0, sticky='w')
        self.cancel_button = ttk.Button(footer, text='取消任务', command=app.cancel.set)
        self.cancel_button.grid(row=2, column=1, sticky='w', padx=8)
        self.open_button = ttk.Button(footer, text='打开导出文件夹', command=self.open_output)
        self.open_button.grid(row=2, column=2, sticky='e')
        footer.bind('<Configure>', self.layout_footer)
        self.root_dir.trace_add('write', self.source_changed)
        self.search.trace_add('write', self.queue_preview)
        self.render_results()

    @staticmethod
    def card(parent, title):
        border = tk.Frame(parent, background=LINE, padx=1, pady=1)
        border.pack(fill='x', pady=(0, 9))
        card = ttk.Frame(border, style='Card.TFrame', padding=(11, 7, 11, 8))
        card.pack(fill='x')
        ttk.Label(card, text=title, style='Section.TLabel').pack(anchor='w', pady=(0, 5))
        return card

    @staticmethod
    def wrap_label(parent, *, pady, **options):
        label = ttk.Label(parent, **options)
        label.pack(fill='x', pady=pady)
        label.bind('<Configure>', lambda event: label.configure(wraplength=max(1, event.width - 2)))

    def make_results(self, parent):
        frame = ttk.Frame(parent, style='Card.TFrame')
        frame.pack(fill='x')
        columns = (('marked', '选中', 46, 38), ('name', '会话', 190, 76),
                   ('count', '消息数', 68, 55), ('time', '最近消息', 146, 106))
        if not self.is_chat:
            columns = (('author', '发布者', 105, 62), ('body', '正文或分享链接', 200, 110),
                       ('time', '发布时间', 146, 106))
        self.tree = ttk.Treeview(frame, columns=[column[0] for column in columns], show='headings',
                                 height=6, selectmode='browse')
        table_font = font.Font(self, font=ttk.Style(self).lookup('Treeview', 'font'))
        for key, label, width, minimum in columns:
            sample = '2000-00-00 00:00' if key == 'time' else label
            minimum = max(minimum, table_font.measure(sample) + 12)
            self.tree.heading(key, text=label, anchor='w')
            self.tree.column(key, width=max(width, minimum), minwidth=minimum, stretch=key in ('name', 'body'))
        self.tree.tag_configure('alternate', background='#faf7f1')
        scrollbar = ttk.Scrollbar(frame, orient='vertical', command=self.tree.yview)
        horizontal = ttk.Scrollbar(frame, orient='horizontal', command=self.tree.xview)

        def resize_scrollbar(first, last):
            horizontal.set(first, last)
            if float(first) == 0 and float(last) == 1:
                horizontal.grid_remove()
            else:
                horizontal.grid(row=1, column=0, sticky='ew')

        self.tree.configure(yscrollcommand=scrollbar.set, xscrollcommand=resize_scrollbar)
        frame.columnconfigure(0, weight=1)
        self.tree.grid(row=0, column=0, sticky='nsew')
        scrollbar.grid(row=0, column=1, sticky='ns')
        self.tree.bind('<MouseWheel>', self.scroll_results)
        if self.is_chat:
            self.tree.bind('<Button-1>', self.click_chat)
            self.tree.bind('<space>', self.toggle_focused_chat)

    def layout_shortcuts(self, event=None):
        buttons = list(self.time_buttons.values())
        minimum = sum(button.winfo_reqwidth() + 6 for button in buttons)
        columns = 2 if event and event.width < minimum else 4
        for index, button in enumerate(buttons):
            button.grid(row=index // columns, column=index % columns, padx=(0, 6), pady=(0, 3), sticky='w')

    def layout_footer(self, event):
        width = max(100, event.width - 36)
        self.status_label.configure(wraplength=width)
        needed = sum(button.winfo_reqwidth() for button in
                     (self.export_button, self.cancel_button, self.open_button)) + 26
        if width < needed:
            self.open_button.grid_configure(row=3, column=0, columnspan=3, pady=(5, 0))
        else:
            self.open_button.grid_configure(row=2, column=2, columnspan=1, pady=0)

    def scroll_results(self, event):
        if not event.delta:
            return 'break'
        steps = max(1, abs(event.delta) // 120)
        steps *= -1 if event.delta > 0 else 1
        low, high = self.tree.yview()
        if (event.delta > 0 and low > 0) or (event.delta < 0 and high < 1):
            self.tree.yview_scroll(steps * 3, 'units')
        else:
            self.body.canvas.yview_scroll(steps, 'units')
        return 'break'

    def choose_directory(self, variable, title):
        value = filedialog.askdirectory(parent=self.app, title=title)
        if value:
            variable.set(value)

    def source_changed(self, *_):
        if self.session and not self.app.busy:
            text = (f'当前使用已读取的{self.title}。' if self.session_current
                    else f'目录已修改，请重新读取{self.title}。')
            self.status.set(text)
        self.refresh_buttons()

    @property
    def session_current(self):
        return self.session is not None and self.root_dir.get().strip() == self.loaded_root

    def read(self):
        root = self.root_dir.get().strip()

        def load(progress):
            session = Session(progress=progress, cancel=self.app.cancel)
            try:
                session.connect(root or None, scope=self.scope)
                timeline = None
                if not self.is_chat and session.moment_count:
                    progress('正在整理朋友圈搜索结果…')
                    timeline = load_moments(session)
                session.check()
                return session, timeline, root
            except BaseException:
                session.close()
                raise

        self.app.run_task(self, load, self.set_session, f'正在读取{self.title}…')

    def set_session(self, result):
        session, self.timeline, self.loaded_root = result
        if self.session is not None:
            self.session.close()
        self.session = session
        self.selected.clear()
        self.chats = {chat['id']: chat for chat in session.chats}
        self.render_results()
        if self.is_chat:
            self.status.set(f'读取完成 · {len(self.chats):,} 个会话')
        else:
            self.status.set(session.moment_status)

    def queue_preview(self, *_):
        if self.preview_job is not None:
            self.after_cancel(self.preview_job)
        self.preview_job = self.after(100, self.render_results)

    def render_results(self):
        if self.preview_job is not None:
            self.after_cancel(self.preview_job)
        self.preview_job = None
        self.tree.delete(*self.tree.get_children())
        term = self.search.get().strip().casefold()
        if self.is_chat:
            for index, (key, chat) in enumerate(self.chats.items()):
                if term not in (chat['name'] + ' ' + chat['username']).casefold():
                    continue
                last = dt.datetime.fromtimestamp(chat['last']).strftime('%Y-%m-%d %H:%M')
                mark = '✓' if key in self.selected else '□'
                self.tree.insert('', 'end', iid=key, tags=('alternate',) if index % 2 else (),
                                 values=(mark, chat['name'], f"{chat['count']:,}", last))
            self.update_selection()
        else:
            posts = self.timeline.search(term) if self.timeline else []
            for index, post in enumerate(posts[:PREVIEW_LIMIT]):
                links = post['links']
                excerpt = post['text'] or (links[0]['title'] or links[0]['url'] if links else '')
                if not excerpt:
                    excerpt = f"{len(post['images'])} 张图片" if post['images'] else post.get('warning', '')
                excerpt = ' '.join(excerpt.split())
                if len(excerpt) > 96:
                    excerpt = excerpt[:96] + '…'
                time = post['time'][:16].replace('T', ' ') if post['time'] else '未知'
                self.tree.insert('', 'end', tags=('alternate',) if index % 2 else (),
                                 values=(post['author'], excerpt, time))
            count = f'关键词匹配 {len(posts):,} 条' if self.timeline else '尚无本地动态可预览'
            if len(posts) > PREVIEW_LIMIT:
                count += f' · 显示最新 {PREVIEW_LIMIT} 条'
            if posts:
                count += ' · 时间范围在导出时应用'
            self.selection.set(count)
        self.refresh_buttons()

    def update_selection(self):
        messages = sum(self.chats[key]['count'] for key in self.selected)
        self.selection.set(f'{len(self.tree.get_children()):,} 个会话 · 已选 {len(self.selected):,} 个'
                           f'（筛选日期前 {messages:,} 条消息）')
        self.refresh_buttons()

    def click_chat(self, event):
        key = self.tree.identify_row(event.y)
        if key:
            self.toggle_chat(key)

    def toggle_focused_chat(self, event):
        self.toggle_chat(self.tree.focus())
        return 'break'

    def toggle_chat(self, key):
        if key and not self.app.busy:
            if key in self.selected:
                self.selected.remove(key)
            else:
                self.selected.add(key)
            self.tree.set(key, 'marked', '✓' if key in self.selected else '□')
            self.update_selection()

    def select_visible(self):
        if not self.app.busy:
            self.selected.update(self.tree.get_children())
            self.render_results()

    def clear_selection(self):
        if not self.app.busy:
            self.selected.clear()
            self.render_results()

    def set_time_range(self, preset):
        start, end = quick_range(preset)
        self.start.set(start)
        self.end.set(end)

    def refresh_buttons(self):
        active = self.app.task_page is self
        busy = self.app.busy
        self.read_button.state(['disabled' if busy else '!disabled'])
        self.read_button.configure(style='TButton' if self.session_current else 'Accent.TButton')
        self.source_button.state(['disabled' if busy else '!disabled'])
        self.source_entry.state(['disabled' if busy else '!disabled'])
        has_data = bool(self.selected) if self.is_chat else bool(self.timeline)
        can_export = not busy and self.session_current and has_data
        self.export_button.state(['!disabled' if can_export else 'disabled'])
        self.cancel_button.state(['!disabled' if active else 'disabled'])

    def export(self):
        if self.app.busy or not self.session_current:
            return
        try:
            start, end = parse_time_range(self.start.get(), self.end.get())
        except ValueError as exc:
            messagebox.showerror('时间范围', str(exc), parent=self.app)
            return
        directory = self.output_dir.get().strip()
        if not directory:
            messagebox.showerror('保存位置', '请选择保存位置。', parent=self.app)
            return
        pictures = self.pictures.get()
        session = self.session
        if self.is_chat:
            chats = [chat for key, chat in self.chats.items() if key in self.selected]

            def save(progress):
                return export_chats(session, chats, directory, start, end, pictures)
        else:
            timeline, keyword, include_cache = self.timeline, self.search.get().strip(), self.include_cache.get()

            def save(progress):
                return export_moments(session, directory, start, end, pictures,
                                      include_cache=include_cache, timeline=timeline, keyword=keyword)
        self.app.run_task(self, save, self.finish_export, '正在准备导出…')

    def finish_export(self, result):
        self.last_output, stats = result
        if self.is_chat:
            status = (f"导出完成 · {stats['messages']:,} 条消息，{stats['images']:,} 张图片，"
                      f"{stats['unavailable_images']:,} 个图片条目未取得可查看图片")
        else:
            status = f"导出完成 · {stats['moments']:,} 条朋友圈，{stats['links']:,} 个链接"
            if stats['include_images']:
                status += (f"，{stats['verified_images']:,} 张已校验，{stats['candidate_images']:,} 张待核对，"
                           f"{stats['unavailable_images']:,} 张未匹配")
            if stats['include_cache']:
                status += f"，本机图片缓存 {stats['cached_images']:,} 张"
        self.status.set(status)

    def open_output(self):
        folder = self.last_output or Path(self.output_dir.get())
        if folder.is_dir():
            os.startfile(str(folder))
        else:
            self.status.set('完成导出后，可在这里打开保存位置。')


class App(tk.Tk):
    def __init__(self, *, scaling=None):
        super().__init__()
        if scaling is not None:
            self.tk.call('tk', 'scaling', scaling)
        self.title(f'拾聊 {VERSION}')
        self.configure(background=PAPER)
        self.geometry('1000x740')
        self.minsize(660, 370)
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.worker = None
        self.task_page = None
        self.closing = False
        configure_style(self)

        shell = ttk.Frame(self)
        shell.pack(fill='both', expand=True)
        shell.columnconfigure(1, weight=1)
        shell.rowconfigure(0, weight=1)
        sidebar = ttk.Frame(shell, style='Sidebar.TFrame', padding=(13, 14))
        sidebar.grid(row=0, column=0, sticky='ns')
        ttk.Label(sidebar, text='拾聊', style='Brand.TLabel').pack(anchor='w', padx=8, pady=(0, 14))
        ttk.Label(sidebar, text=f'Windows {VERSION}', style='Version.TLabel').pack(side='bottom', anchor='w', padx=8)
        stage = ttk.Frame(shell)
        stage.grid(row=0, column=1, sticky='nsew')
        stage.rowconfigure(0, weight=1)
        stage.columnconfigure(0, weight=1)

        self.pages = {}
        self.nav = {}
        for scope in ('chat', 'moments'):
            page = ExportPage(stage, self, scope)
            self.pages[scope] = page
            page.grid(row=0, column=0, sticky='nsew')
            page.grid_remove()
            button = ttk.Button(sidebar, text=page.title, style='Nav.TButton',
                                command=lambda key=scope: self.show_page(key))
            button.pack(fill='x', pady=(0, 4))
            self.nav[scope] = button
        self.show_page('chat')
        self.protocol('WM_DELETE_WINDOW', self.on_close)
        self.bind('<MouseWheel>', self.scroll_page)
        self.bind('<FocusIn>', self.scroll_to_focus)
        self.after(60, self.poll)

    @property
    def busy(self):
        return self.worker is not None

    def show_page(self, scope):
        if hasattr(self, 'page'):
            self.page.grid_remove()
        self.page = self.pages[scope]
        self.page.grid()
        for key, button in self.nav.items():
            button.configure(style='Selected.Nav.TButton' if key == scope else 'Nav.TButton')

    def scroll_page(self, event):
        body = self.page.body
        if body.contains(event.widget) and event.delta:
            count = max(1, abs(event.delta) // 120)
            body.canvas.yview_scroll(count * (-1 if event.delta > 0 else 1), 'units')
            return 'break'

    def scroll_to_focus(self, event):
        self.page.body.show_focused(event.widget)

    def run_task(self, page, work, complete, status):
        if self.busy or self.closing:
            return
        self.cancel.clear()
        self.task_page = page
        page.status.set(status)
        page.progressbar.start(12)

        def progress(message):
            self.events.put(('status', message))

        def target():
            try:
                self.events.put(('complete', work(progress)))
            except InterruptedError:
                self.events.put(('status', '任务已取消。未完成的导出保存在「.未完成」文件夹。'))
            except Exception as exc:
                self.events.put(('error', str(exc)))
            finally:
                self.events.put(('done', None))

        self.complete = complete
        self.worker = threading.Thread(target=target, daemon=False)
        for workspace in self.pages.values():
            workspace.refresh_buttons()
        self.worker.start()

    def poll(self):
        try:
            while True:
                event, value = self.events.get_nowait()
                page = self.task_page
                if event == 'status':
                    page.status.set(value)
                elif event == 'complete':
                    self.complete(value)
                elif event == 'error':
                    page.status.set('未完成 · ' + value)
                    if not self.closing:
                        messagebox.showerror('任务未完成', value, parent=self)
                elif event == 'done':
                    page.progressbar.stop()
                    self.task_page = None
                    self.worker = None
                    self.complete = None
                    for workspace in self.pages.values():
                        workspace.refresh_buttons()
        except queue.Empty:
            pass
        if self.closing and not self.busy:
            for page in self.pages.values():
                if page.session is not None:
                    page.session.close()
            self.destroy()
            return
        self.after(60, self.poll)

    def on_close(self):
        self.closing = True
        self.cancel.set()
        self.page.status.set('正在停止任务并清理临时快照…')
