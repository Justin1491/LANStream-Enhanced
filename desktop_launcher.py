"""Windows tray entry point; run launcher.py directly for the console version."""
import io
import os
from pathlib import Path
import queue
import sys
import threading
import traceback


LOG_LIMIT = 1024 * 1024


class RecentOutput:
    def __init__(self):
        self._text = b''
        self._revision = 0
        self._lock = threading.Lock()

    def write(self, text):
        with self._lock:
            self._text = (self._text + text.encode('utf-8', errors='replace'))[-LOG_LIMIT:]
            self._revision += 1

    def snapshot(self, since=None):
        with self._lock:
            return self._revision, (None if since == self._revision else
                                    self._text.decode('utf-8', errors='ignore'))


class CapturedStream(io.TextIOBase):
    def __init__(self, output, original=None):
        self.output, self.original = output, original

    @property
    def encoding(self):
        return 'utf-8'

    def write(self, text):
        self.output.write(text)
        if self.original is not None:
            try:
                self.original.write(text)
                self.original.flush()
            except (OSError, ValueError):
                self.original = None
        return len(text)

    def flush(self):
        if self.original is not None:
            try:
                self.original.flush()
            except (OSError, ValueError):
                self.original = None


def inherited_stream(name):
    stream = getattr(sys, name)
    if stream is not None or os.name != 'nt':
        return stream
    # A windowless bootloader sets sys.std* to None even with installer pipes.
    import ctypes
    from ctypes import wintypes
    import msvcrt

    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.GetStdHandle.argtypes = [wintypes.DWORD]
    api.GetStdHandle.restype = wintypes.HANDLE
    api.GetCurrentProcess.restype = wintypes.HANDLE
    api.DuplicateHandle.argtypes = [wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
                                   ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD,
                                   wintypes.BOOL, wintypes.DWORD]
    api.DuplicateHandle.restype = wintypes.BOOL
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    handle = api.GetStdHandle((-11 if name == 'stdout' else -12) & 0xffffffff)
    if not handle or handle == ctypes.c_void_p(-1).value:
        return None
    duplicate = wintypes.HANDLE()
    process = api.GetCurrentProcess()
    if not api.DuplicateHandle(process, handle, process, ctypes.byref(duplicate), 0, False, 2):
        return None
    try:
        fd = msvcrt.open_osfhandle(duplicate.value, os.O_WRONLY | os.O_BINARY)
    except OSError:
        api.CloseHandle(duplicate)
        return None
    return os.fdopen(fd, 'w', buffering=1, encoding='utf-8', errors='backslashreplace')


class StatusWindow:
    def __init__(self, output):
        import tkinter as tk
        from tkinter import scrolledtext, ttk

        self.output = output
        self.stop = threading.Event()
        self.events = queue.SimpleQueue()
        self.tray = None
        self.tray_thread = None
        self.tray_available = False
        self.tray_failed = False
        self.finished = False
        self.result = 0
        self.revision = -1
        self.closed = False
        self.poll_id = None
        self.tray_check_id = None
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title('LANStream — Starting')
        self.root.geometry('800x480')
        self.root.minsize(480, 260)
        self.text = scrolledtext.ScrolledText(self.root, wrap='word', font='TkFixedFont', state='disabled')
        self.text.pack(fill='both', expand=True, padx=8, pady=(8, 4))
        self.hint = ttk.Label(self.root, text='Starting LANStream…')
        self.hint.pack(fill='x', padx=8, pady=(0, 8))
        self.root.protocol('WM_DELETE_WINDOW', self.close_window)
        self.root.bind('<Unmap>', self.minimized)

    def show(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def close_window(self):
        if self.tray_available and not self.stop.is_set():
            self.root.withdraw()
        else:
            self.request_exit()

    def minimized(self, event):
        if event.widget == self.root and self.root.state() == 'iconic' and self.tray_available:
            self.root.withdraw()

    def request_exit(self):
        if not self.stop.is_set():
            self.stop.set()
            self.root.title('LANStream — Stopping')
            self.hint.configure(text='Stopping LANStream; waiting for background cleanup…')
        if self.finished:
            self.finish()

    def finish(self):
        if self.closed:
            return
        self.closed = True
        for callback in (self.poll_id, self.tray_check_id):
            if callback is not None:
                self.root.after_cancel(callback)
        if self.tray is not None:
            self.tray.stop()
        if self.tray_thread is not None:
            self.tray_thread.join(timeout=1)
        self.root.destroy()

    def run_server(self):
        try:
            import launcher
            result = launcher.main(stop_event=self.stop,
                                   on_ready=lambda: self.events.put(('ready', None)))
        except BaseException:
            detail = traceback.format_exc()
            if getattr(sys.stderr, 'output', None) is self.output:
                sys.stderr.write(detail)
            else:
                self.output.write(detail)
            result = 1
        self.events.put(('finished', result))

    def start_tray(self):
        import pystray
        from PIL import Image, ImageTk

        icon_path = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent)) / 'static' / 'lanstream.ico'
        with Image.open(icon_path) as source:
            image = source.convert('RGBA')
        self.icon_image = ImageTk.PhotoImage(image)
        self.root.iconphoto(True, self.icon_image)
        self.tray = pystray.Icon('LANStream', image, 'LANStream', menu=pystray.Menu(
            pystray.MenuItem('Show status', lambda: self.events.put(('show', None)), default=True),
            pystray.MenuItem('Exit LANStream', lambda: self.events.put(('exit', None))),
        ))
        def setup(icon):
            try:
                if self.stop.is_set() or self.tray_failed:
                    icon.stop()
                    return
                icon.visible = True
                self.events.put(('tray_ready', None))
            except Exception:
                self.events.put(('tray_failed', traceback.format_exc()))
        def run_icon():
            try:
                self.tray.run(setup=setup)
            except Exception:
                self.events.put(('tray_failed', traceback.format_exc()))
        # pystray's setup thread inherits daemon status, including on failure
        # before its native message loop can signal readiness.
        self.tray_thread = threading.Thread(target=run_icon, name='lanstream-tray', daemon=True)
        self.tray_thread.start()
        self.tray_check_id = self.root.after(5000, self.check_tray)

    def check_tray(self):
        if not self.tray_available and not self.tray_failed:
            self.events.put(('tray_failed', 'The system tray did not become available.'))

    def poll(self):
        while True:
            try:
                event, value = self.events.get_nowait()
            except queue.Empty:
                break
            if event == 'show':
                self.show()
            elif event == 'exit':
                self.request_exit()
                if self.finished:
                    return
            elif event == 'tray_ready' and not self.tray_failed and not self.stop.is_set():
                self.tray_available = True
                self.hint.configure(text='Minimize or close to hide. To stop, choose Exit LANStream from the tray menu.')
            elif event == 'tray_failed':
                self.tray_failed = True
                self.tray_available = False
                self.output.write('System tray unavailable: ' + value + '\n')
                self.hint.configure(text='System tray unavailable. Close this window to stop LANStream.')
                self.show()
            elif event == 'ready' and not self.stop.is_set():
                self.root.title('LANStream — Running')
            elif event == 'finished':
                self.finished, self.result = True, value
                if self.stop.is_set() or value == 0:
                    self.finish()
                    return
                self.root.title('LANStream — Failed to start')
                self.hint.configure(text='LANStream stopped. Review the error above, then use Exit LANStream.')
                self.show()
        revision, text = self.output.snapshot(self.revision)
        if revision != self.revision:
            scroll = self.text.yview()
            self.text.configure(state='normal')
            self.text.delete('1.0', 'end')
            self.text.insert('end', text)
            self.text.configure(state='disabled')
            if scroll[1] >= 0.99:
                self.text.see('end')
            else:
                self.text.yview_moveto(scroll[0])
            self.revision = revision
        self.poll_id = self.root.after(100, self.poll)

    def run(self):
        try:
            self.start_tray()
        except Exception:
            self.events.put(('tray_failed', traceback.format_exc()))
        worker = threading.Thread(target=self.run_server, name='lanstream-server')
        worker.start()
        try:
            self.poll()
            self.root.mainloop()
        finally:
            self.stop.set()
            worker.join()
            if self.tray is not None:
                self.tray.stop()
        return self.result


def main():
    output = RecentOutput()
    sys.stdout = CapturedStream(output, inherited_stream('stdout'))
    sys.stderr = CapturedStream(output, inherited_stream('stderr'))
    if sys.stdin is None:
        sys.stdin = io.StringIO('')
    try:
        return StatusWindow(output).run()
    except Exception:
        traceback.print_exc()
        if os.name == 'nt':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, output.snapshot()[1], 'LANStream could not start', 0x10)
        return 1


if __name__ == '__main__':
    sys.exit(main())
