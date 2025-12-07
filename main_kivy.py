import threading
import os
import time
import hashlib
import re
import requests
from requests.adapters import HTTPAdapter
from concurrent.futures import ThreadPoolExecutor

from kivy.lang import Builder
from kivy.core.window import Window
from kivy.properties import StringProperty, NumericProperty, BooleanProperty, ObjectProperty
from kivy.clock import Clock
from kivy.metrics import dp
from kivy.utils import platform

from kivymd.app import MDApp
from kivymd.uix.screen import MDScreen
from kivymd.uix.card import MDCard
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.button import MDFillRoundFlatButton, MDIconButton
from kivymd.uix.textfield import MDTextField
from kivymd.uix.label import MDLabel
from kivymd.uix.progressbar import MDProgressBar
from kivymd.uix.snackbar import Snackbar

# --- Downloader Logic (Adapted for Kivy) ---

session = requests.Session()
adapter = HTTPAdapter(pool_connections=100, pool_maxsize=100)
session.mount('http://', adapter)
session.mount('https://', adapter)

class DownloadTask:
    def __init__(self, url, filename, total_size=0, ui_item=None):
        self.url = url
        self.filename = filename
        self.total_size = total_size
        self.downloaded = 0
        self.status = "pending"
        self.paused = False
        self.cancel = False
        self.chunks_done = set()
        self.lock = threading.Lock()
        self.start_time = 0.0
        self.ui_item = ui_item  # Reference to the Kivy Widget

class MultiThreadDownloader:
    def __init__(self, num_threads=64):
        self.num_threads = num_threads
        self.active_downloads = {}
    
    def get_url_hash(self, url):
        return hashlib.md5(url.encode()).hexdigest()[:12]
    
    def get_file_info(self, url):
        try:
            head = session.head(url, allow_redirects=True, timeout=15)
            total_size = int(head.headers.get('content-length', 0))
            accept_ranges = head.headers.get('accept-ranges', 'none') == 'bytes'
            
            cd = head.headers.get('content-disposition')
            filename = None
            if cd:
                fname = re.findall('filename="?([^"]+)"?', cd)
                filename = fname[0] if fname else None
            
            if not filename:
                filename = url.split("/")[-1].split("?")[0] or "download"
            
            filename = re.sub(r'[\\/*?:"<>|]', '_', filename)
            return filename, total_size, accept_ranges
        except Exception:
            return None, 0, False

    def download_chunk(self, task, chunk_id, start, end):
        chunk_file = f"{task.filename}.part{chunk_id}"
        expected_size = end - start + 1
        current_size = 0
        
        if os.path.exists(chunk_file):
            current_size = os.path.getsize(chunk_file)
            if current_size >= expected_size:
                return current_size
            start += current_size
        
        headers = {'Range': f'bytes={start}-{end}'}
        try:
            CHUNK_SIZE = 524288
            response = session.get(task.url, headers=headers, stream=True, timeout=60)
            if response.status_code not in [200, 206]:
                return current_size
            
            mode = 'ab' if current_size > 0 else 'wb'
            with open(chunk_file, mode, buffering=CHUNK_SIZE) as f:
                for data in response.iter_content(chunk_size=CHUNK_SIZE):
                    if task.paused or task.cancel:
                        f.flush()
                        return current_size
                    if data:
                        f.write(data)
                        data_len = len(data)
                        current_size += data_len
                        with task.lock:
                            task.downloaded += data_len
                f.flush()
            return current_size
        except Exception:
            return current_size

    def merge_chunks(self, task, num_chunks, expected_sizes):
        temp_filename = f"{task.filename}.tmp"
        try:
            with open(temp_filename, 'wb', buffering=1048576) as outfile:
                for i in range(num_chunks):
                    chunk_file = f"{task.filename}.part{i}"
                    if os.path.exists(chunk_file):
                        with open(chunk_file, 'rb', buffering=1048576) as infile:
                            expected = expected_sizes.get(i, 0)
                            bytes_read = 0
                            while bytes_read < expected:
                                to_read = min(1048576, expected - bytes_read)
                                data = infile.read(to_read)
                                if not data: break
                                outfile.write(data)
                                bytes_read += len(data)
                outfile.flush()
            
            if os.path.exists(task.filename):
                try:
                    os.remove(task.filename)
                except OSError:
                    pass
            os.rename(temp_filename, task.filename)
            
            for i in range(num_chunks):
                chunk_file = f"{task.filename}.part{i}"
                if os.path.exists(chunk_file):
                    os.remove(chunk_file)
        except Exception as e:
            if os.path.exists(temp_filename):
                os.remove(temp_filename)
            raise e

    def start_download(self, task, on_progress, on_complete, on_error):
        task.status = "downloading"
        task.paused = False
        
        try:
            test = session.head(task.url, allow_redirects=True, timeout=10)
            supports_range = test.headers.get('accept-ranges', 'none') == 'bytes'
        except:
            supports_range = False
        
        if task.total_size > 102400 and supports_range:
            self._multi_thread_download(task, on_progress, on_complete, on_error)
        else:
            self._single_thread_download(task, on_progress, on_complete, on_error)

    def _multi_thread_download(self, task, on_progress, on_complete, on_error):
        chunk_size = task.total_size // self.num_threads
        chunks = []
        chunk_expected_sizes = {}
        
        for i in range(self.num_threads):
            start = i * chunk_size
            end = start + chunk_size - 1 if i < self.num_threads - 1 else task.total_size - 1
            chunks.append((i, start, end))
            chunk_expected_sizes[i] = end - start + 1
        
        task.downloaded = 0
        for i, start, end in chunks:
            chunk_file = f"{task.filename}.part{i}"
            if os.path.exists(chunk_file):
                task.downloaded += os.path.getsize(chunk_file)
        
        last_update = time.time()
        last_downloaded = task.downloaded
        
        try:
            with ThreadPoolExecutor(max_workers=self.num_threads) as executor:
                futures = {
                    executor.submit(self.download_chunk, task, i, start, end): (i, start, end)
                    for i, start, end in chunks
                }
                
                while futures:
                    if task.cancel:
                        for f in futures: f.cancel()
                        return
                    if task.paused:
                        time.sleep(0.1)
                        continue
                    
                    current_time = time.time()
                    if current_time - last_update >= 0.25:
                        elapsed = current_time - last_update
                        speed = (task.downloaded - last_downloaded) / elapsed if elapsed > 0 else 0
                        on_progress(task, speed)
                        last_update = current_time
                        last_downloaded = task.downloaded
                    
                    done = [f for f in futures if f.done()]
                    for f in done:
                        chunk_info = futures.pop(f)
                        chunk_id = chunk_info[0]
                        task.chunks_done.add(chunk_id)
                    
                    if not done:
                        time.sleep(0.05)
            
            if task.paused or task.cancel:
                return
            
            all_complete = True
            for i, start, end in chunks:
                chunk_file = f"{task.filename}.part{i}"
                if not os.path.exists(chunk_file) or os.path.getsize(chunk_file) < chunk_expected_sizes[i]:
                    all_complete = False
                    break
            
            if not all_complete:
                on_error(task, "Incomplete chunks")
                return
            
            task.status = "merging"
            on_progress(task, 0)
            self.merge_chunks(task, self.num_threads, chunk_expected_sizes)
            
            task.status = "completed"
            on_complete(task)
            
        except Exception as e:
            on_error(task, str(e))

    def _single_thread_download(self, task, on_progress, on_complete, on_error):
        headers = {}
        file_size = 0
        if os.path.exists(task.filename):
            file_size = os.path.getsize(task.filename)
            headers['Range'] = f'bytes={file_size}-'
            task.downloaded = file_size
        
        last_update = time.time()
        last_downloaded = task.downloaded
        
        try:
            response = session.get(task.url, headers=headers, stream=True, timeout=30)
            if task.total_size == 0:
                task.total_size = int(response.headers.get('content-length', 0)) + file_size
            
            mode = 'ab' if file_size else 'wb'
            with open(task.filename, mode, buffering=1048576) as f:
                for chunk in response.iter_content(chunk_size=1048576):
                    if task.paused or task.cancel: return
                    if chunk:
                        f.write(chunk)
                        task.downloaded += len(chunk)
                        current_time = time.time()
                        if current_time - last_update >= 0.25:
                            speed = (task.downloaded - last_downloaded) / (current_time - last_update)
                            on_progress(task, speed)
                            last_update = current_time
                            last_downloaded = task.downloaded
            
            if not task.paused and not task.cancel:
                task.status = "completed"
                on_complete(task)
        except Exception as e:
            task.status = "failed"
            on_error(task, str(e))

# --- Kivy UI Components ---

KV = '''
<DownloadItemCard>:
    orientation: "vertical"
    size_hint_y: None
    height: "140dp"
    padding: "16dp"
    spacing: "8dp"
    elevation: 3
    radius: [12,]
    md_bg_color: 1, 1, 1, 1
    shadow_offset: 0, 2
    shadow_radius: 8

    MDBoxLayout:
        orientation: "horizontal"
        size_hint_y: None
        height: "32dp"
        spacing: "12dp"

        MDIcon:
            icon: "download"
            theme_text_color: "Custom"
            text_color: 0.2, 0.6, 1, 1
            size_hint_x: None
            width: "24dp"
            pos_hint: {"center_y": .5}

        MDLabel:
            text: root.filename
            bold: True
            font_style: "Subtitle1"
            shorten: True
            shorten_from: 'right'
            pos_hint: {"center_y": .5}

    MDBoxLayout:
        orientation: "horizontal"
        size_hint_y: None
        height: "24dp"
        
        MDLabel:
            text: root.progress_text
            font_style: "Body2"
            theme_text_color: "Primary"

        MDLabel:
            text: root.speed_text
            font_style: "Body2"
            theme_text_color: "Custom"
            text_color: 0.2, 0.8, 0.2, 1
            halign: "right"

    MDProgressBar:
        value: root.progress_value
        max: 100
        size_hint_y: None
        height: "6dp"
        color: 0.2, 0.6, 1, 1
        back_color: 0.9, 0.9, 0.9, 1

    MDBoxLayout:
        orientation: "horizontal"
        size_hint_y: None
        height: "40dp"
        spacing: "8dp"
        padding: [0, 8, 0, 0]

        MDLabel:
            text: root.eta_text
            font_style: "Caption"
            theme_text_color: "Secondary"
            size_hint_x: 0.7
            pos_hint: {"center_y": .5}

        MDIconButton:
            icon: root.pause_icon
            theme_text_color: "Custom"
            text_color: 1, 0.5, 0, 1
            size_hint_x: None
            width: "40dp"
            on_release: root.toggle_pause()
            pos_hint: {"center_y": .5}

        MDIconButton:
            icon: "delete"
            theme_text_color: "Custom"
            text_color: 0.9, 0.2, 0.2, 1
            size_hint_x: None
            width: "40dp"
            on_release: root.cancel_download()
            pos_hint: {"center_y": .5}

MDScreen:
    md_bg_color: 0.98, 0.98, 0.98, 1

    MDBoxLayout:
        orientation: "vertical"

        # Header
        MDCard:
            size_hint_y: None
            height: "80dp"
            elevation: 4
            radius: [0, 0, 16, 16]
            md_bg_color: 0.1, 0.4, 0.8, 1

            MDBoxLayout:
                orientation: "horizontal"
                padding: "20dp"
                spacing: "16dp"

                MDIcon:
                    icon: "cloud-download"
                    theme_text_color: "Custom"
                    text_color: 1, 1, 1, 1
                    size_hint_x: None
                    width: "32dp"
                    pos_hint: {"center_y": .5}

                MDLabel:
                    text: "K Download Manager"
                    font_style: "H5"
                    bold: True
                    theme_text_color: "Custom"
                    text_color: 1, 1, 1, 1
                    pos_hint: {"center_y": .5}

        # Main Content
        MDBoxLayout:
            orientation: "vertical"
            padding: "20dp"
            spacing: "20dp"

            # Input Section
            MDCard:
                size_hint_y: None
                height: "100dp"
                elevation: 2
                radius: [16,]
                md_bg_color: 1, 1, 1, 1
                padding: "16dp"

                MDBoxLayout:
                    orientation: "vertical"
                    spacing: "12dp"

                    MDBoxLayout:
                        orientation: "horizontal"
                        size_hint_y: None
                        height: "56dp"
                        spacing: "12dp"

                        MDTextField:
                            id: url_field
                            hint_text: "Paste download URL here..."
                            mode: "rectangle"
                            size_hint_x: 0.6
                            size_hint_y: None
                            height: "56dp"
                            line_color_focus: 0.1, 0.4, 0.8, 1

                        MDTextField:
                            id: thread_field
                            hint_text: "Threads"
                            text: "64"
                            mode: "rectangle"
                            size_hint_x: 0.2
                            size_hint_y: None
                            height: "56dp"
                            input_filter: "int"
                            on_text_validate: app.set_threads(self.text)
                            line_color_focus: 0.1, 0.4, 0.8, 1

                        MDRaisedButton:
                            text: "START"
                            size_hint_x: 0.1
                            size_hint_y: 0.90
                            height: "56dp"
                            md_bg_color: 0.1, 0.4, 0.8, 1
                            elevation: 3
                            on_release: app.start_download(url_field.text)

            # Stats Section
            MDCard:
                size_hint_y: None
                height: "60dp"
                elevation: 1
                radius: [12,]
                md_bg_color: 0.95, 0.95, 0.95, 1
                padding: "16dp"

                MDLabel:
                    id: status_label
                    text: "Ready to download • Active: 0 | Completed: 0 | Failed: 0"
                    halign: "center"
                    font_style: "Body2"
                    theme_text_color: "Secondary"
                    pos_hint: {"center_y": .5}

            # Downloads List
            MDCard:
                elevation: 2
                radius: [16,]
                md_bg_color: 0.99, 0.99, 0.99, 1
                padding: "8dp"

                ScrollView:
                    MDBoxLayout:
                        id: download_list
                        orientation: "vertical"
                        spacing: "12dp"
                        padding: "8dp"
                        size_hint_y: None
                        height: self.minimum_height

                        MDLabel:
                            text: "No downloads yet"
                            halign: "center"
                            font_style: "Body1"
                            theme_text_color: "Hint"
                            size_hint_y: None
                            height: "100dp"
'''

class DownloadItemCard(MDCard):
    filename = StringProperty("")
    progress_text = StringProperty("Starting...")
    speed_text = StringProperty("")
    eta_text = StringProperty("")
    progress_value = NumericProperty(0)
    pause_icon = StringProperty("pause-circle")
    
    def __init__(self, task, **kwargs):
        super().__init__(**kwargs)
        self.task = task
        self.app = MDApp.get_running_app()
    
    def toggle_pause(self):
        if self.task.status == "completed": return
        
        if self.task.paused:
            # Resume
            self.task.paused = False
            self.pause_icon = "pause-circle"
            threading.Thread(
                target=self.app.downloader.start_download,
                args=(self.task, self.app.on_progress, self.app.on_complete, self.app.on_error),
                daemon=True
            ).start()
        else:
            # Pause
            self.task.paused = True
            self.task.status = "paused"
            self.pause_icon = "play-circle"
            self.progress_text = "Paused"

    def cancel_download(self):
        self.task.cancel = True
        self.task.paused = True
        if self.task.status != "completed":
            self.app.stats["active"] -= 1
            self.app.stats["failed"] += 1
        
        url_hash = self.app.downloader.get_url_hash(self.task.url)
        if url_hash in self.app.downloader.active_downloads:
            del self.app.downloader.active_downloads[url_hash]
        
        self.parent.remove_widget(self)
        self.app.update_stats()

class DownloadManagerApp(MDApp):
    def build(self):
        self.downloader = MultiThreadDownloader(num_threads=64)
        self.stats = {"total": 0, "completed": 0, "failed": 0, "active": 0}
        
        # Set android permissions if needed
        if platform == 'android':
            from android.permissions import request_permissions, Permission
            request_permissions([Permission.WRITE_EXTERNAL_STORAGE, Permission.READ_EXTERNAL_STORAGE, Permission.INTERNET])
        
        return Builder.load_string(KV)

    def set_threads(self, value):
        try:
            threads = int(value)
            threads = max(1, min(256, threads))
            self.downloader.num_threads = threads
            self.root.ids.thread_field.text = str(threads)
        except:
            self.root.ids.thread_field.text = str(self.downloader.num_threads)

    def update_stats(self):
        label = self.root.ids.status_label
        if self.stats['total'] == 0:
            label.text = f"Ready to download • Threads: {self.downloader.num_threads}"
        else:
            label.text = f"Active: {self.stats['active']} | Completed: {self.stats['completed']} | Failed: {self.stats['failed']} | Threads: {self.downloader.num_threads}"

    def format_size(self, size):
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024: return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"

    def start_download(self, url):
        url = url.strip()
        if not url:
            Snackbar(text="Please enter a URL").open()
            return
        
        # Update threads before starting
        try:
            threads = int(self.root.ids.thread_field.text)
            self.downloader.num_threads = max(1, min(256, threads))
        except:
            pass
            
        url_hash = self.downloader.get_url_hash(url)
        if url_hash in self.downloader.active_downloads:
            existing = self.downloader.active_downloads[url_hash]
            if existing.status in ["downloading", "pending"]:
                Snackbar(text="Already downloading!").open()
                return

        self.root.ids.url_field.text = ""
        self.update_stats()
        
        # Get info in thread to not block UI
        threading.Thread(target=self._init_download, args=(url, url_hash)).start()

    def _init_download(self, url, url_hash):
        filename, total_size, _ = self.downloader.get_file_info(url)
        if not filename:
            Clock.schedule_once(lambda dt: Snackbar(text="Failed to get file info").open())
            return
            
        # If android, adjust path to Download folder
        if platform == 'android':
            from android.storage import primary_external_storage_path
            dir_path = os.path.join(primary_external_storage_path(), 'Download')
            if not os.path.exists(dir_path):
                os.makedirs(dir_path)
            filename = os.path.join(dir_path, filename)

        task = DownloadTask(url, filename, total_size)
        self.downloader.active_downloads[url_hash] = task
        task.start_time = time.time()
        
        Clock.schedule_once(lambda dt: self._add_ui_item(task))

    def _add_ui_item(self, task):
        # Remove "No downloads yet" label if exists
        download_list = self.root.ids.download_list
        if len(download_list.children) == 1 and hasattr(download_list.children[0], 'text'):
            if "No downloads yet" in download_list.children[0].text:
                download_list.clear_widgets()
        
        item = DownloadItemCard(task)
        item.filename = os.path.basename(task.filename)
        task.ui_item = item
        download_list.add_widget(item, index=0) # Add to top
        
        self.stats["total"] += 1
        self.stats["active"] += 1
        self.update_stats()
        
        threading.Thread(
            target=self.downloader.start_download,
            args=(task, self.on_progress, self.on_complete, self.on_error),
            daemon=True
        ).start()

    def on_progress(self, task, speed):
        # Update UI on main thread
        Clock.schedule_once(lambda dt: self._update_ui_progress(task, speed))

    def _update_ui_progress(self, task, speed):
        if not task.ui_item: return
        
        progress = (task.downloaded / task.total_size * 100) if task.total_size > 0 else 0
        task.ui_item.progress_value = progress
        
        downloaded_str = self.format_size(task.downloaded)
        total_str = self.format_size(task.total_size)
        task.ui_item.progress_text = f"{int(progress)}% ({downloaded_str}/{total_str})"
        
        task.ui_item.speed_text = f"{self.format_size(speed)}/s"
        
        if task.status == "merging":
            task.ui_item.progress_text = "Merging chunks..."
            task.ui_item.eta_text = ""
        else:
            remaining = task.total_size - task.downloaded
            eta = remaining / speed if speed > 0 else 0
            task.ui_item.eta_text = f"ETA: {int(eta)}s"

    def on_complete(self, task):
        self.stats["completed"] += 1
        self.stats["active"] -= 1
        Clock.schedule_once(lambda dt: self._update_ui_complete(task))
        Clock.schedule_once(lambda dt: self.update_stats())

    def _update_ui_complete(self, task):
        if task.ui_item:
            task.ui_item.progress_value = 100
            task.ui_item.progress_text = "Completed"
            task.ui_item.speed_text = ""
            task.ui_item.eta_text = ""
            task.ui_item.pause_icon = "check-circle"

    def on_error(self, task, error):
        self.stats["failed"] += 1
        self.stats["active"] -= 1
        Clock.schedule_once(lambda dt: self._update_ui_error(task, error))
        Clock.schedule_once(lambda dt: self.update_stats())

    def _update_ui_error(self, task, error):
        if task.ui_item:
            task.ui_item.progress_text = f"Error: {error[:20]}"
            task.ui_item.md_bg_color = (1, 0.9, 0.9, 1)

if __name__ == "__main__":
    DownloadManagerApp().run()
