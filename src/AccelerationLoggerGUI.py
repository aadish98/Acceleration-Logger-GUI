import tkinter as tk
from tkinter import messagebox, ttk
import threading, time, csv, os, json, hashlib, gzip, shutil, subprocess, platform, re, ctypes
from datetime import datetime
from collections import deque

import serial
import serial.tools.list_ports

from version import APP_DISPLAY_NAME, APP_VERSION

def find_arduino_port():
    """
    Returns the first serial port whose description / VID-PID
    looks like an Arduino-style board. Returns None if nothing found.
    """
    for port in serial.tools.list_ports.comports():
        # Check common clues
        if ("Arduino" in port.description
            or "CH340"  in port.description     # cheap clone USB⇄Serial
            or "USB-Serial" in port.description
            or port.vid in (0x2341, 0x1A86)):   # 0x2341 = Arduino SA, 0x1A86 = CH340
            return port.device
    return None

class AccelLoggerGUI:
    def __init__(self, master):
        self.master = master
        master.title(APP_DISPLAY_NAME)

        menubar = tk.Menu(master)
        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="About", command=self._show_about_dialog)
        menubar.add_cascade(label="Help", menu=help_menu)
        master.config(menu=menubar)

        # Input fields for metadata
        tk.Label(master, text="Platform Name (i.e. Zantiks):").grid(row=0, column=0, padx=5, pady=5, sticky='e')
        self.platform_entry = tk.Entry(master)
        self.platform_entry.grid(row=0, column=1, padx=5, pady=5)

        tk.Label(master, text="Temperature (C, i.e. 21):").grid(row=1, column=0, padx=5, pady=5, sticky='e')
        self.temp_entry = tk.Entry(master)
        self.temp_entry.grid(row=1, column=1, padx=5, pady=5)

        # Optional temperature schedule controls
        self.temperature_schedule = []  # list of (start_hour_float, temp_float)
        self.temp_schedule_button = tk.Button(master, text="Schedule...", command=self.open_schedule_dialog)
        self.temp_schedule_button.grid(row=1, column=2, padx=5, pady=5)
        self.temp_schedule_summary = tk.Label(master, text="Schedule: none")
        self.temp_schedule_summary.grid(row=1, column=3, padx=5, pady=5, sticky='w')

        tk.Label(master, text="Speed Setting (i.e U0 D1000 M1 M-1 x5):").grid(row=2, column=0, padx=5, pady=5, sticky='e')
        self.speed_entry = tk.Entry(master)
        self.speed_entry.grid(row=2, column=1, padx=5, pady=5)

        tk.Label(master, text="Logging Duration (seconds):").grid(row=3, column=0, padx=5, pady=5, sticky='e')
        self.duration_entry = tk.Entry(master)
        self.duration_entry.grid(row=3, column=1, padx=5, pady=5)

        # Options
        self.compress_var = tk.BooleanVar(value=False)
        self.compress_cb = tk.Checkbutton(master, text="Compress rotated files (.gz)", variable=self.compress_var)
        self.compress_cb.grid(row=4, column=0, columnspan=2, sticky='w', padx=5)

        # Buttons for starting and stopping logging
        self.start_button = tk.Button(master, text="Start Logging", command=self.start_logging)
        self.start_button.grid(row=5, column=0, padx=5, pady=10)
        self.stop_button = tk.Button(master, text="Stop Logging", command=self.stop_logging, state=tk.DISABLED)
        self.stop_button.grid(row=5, column=1, padx=5, pady=10)

        # Progress and metrics
        tk.Label(master, text="Progress (elapsed / target):").grid(row=6, column=0, padx=5, pady=2, sticky='e')
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(master, orient='horizontal', mode='determinate', variable=self.progress_var)
        self.progress_bar.grid(row=6, column=1, padx=5, pady=2, sticky='we')
        master.grid_columnconfigure(1, weight=1)

        self.elapsed_label = tk.Label(master, text="Elapsed: 0s of 0s")
        self.elapsed_label.grid(row=7, column=0, columnspan=2, sticky='w', padx=5)

        self.rate_label = tk.Label(master, text="Rate: 0.0 Hz | Samples: 0 | Dropped: 0 | Reconnects: 0")
        self.rate_label.grid(row=8, column=0, columnspan=2, sticky='w', padx=5)

        # Live preview (last N samples)
        tk.Label(master, text="Live Preview (latest 50):").grid(row=9, column=0, columnspan=2, sticky='w', padx=5)
        self.preview_list = tk.Listbox(master, height=8)
        self.preview_list.grid(row=10, column=0, columnspan=2, sticky='nsew', padx=5, pady=5)
        master.grid_rowconfigure(10, weight=1)

        # Logging thread and control flag
        self.logging_thread = None
        self.stop_logging_flag = False
        self.ui_update_job = None

        # Runtime state for metrics/preview
        self._start_time = None
        self._target_duration = 0
        self._samples_total = 0
        self._dropped_total = 0
        self._reconnects_total = 0
        self._last_sample_times = deque(maxlen=200)
        self._preview_buffer = deque(maxlen=50)
        self._buf_lock = threading.Lock()
        self._manifest_path = None
        self._manifest = None
        self._sleep_inhibit_handle = None
        self._sleep_keepalive_thread = None
        self._sleep_keepalive_stop = None
        self._current_file_path = None
        self._current_file = None
        self._current_writer = None
        self._current_date_str = None
        self._part_index = 0
        self._row_since_flush = 0
        self._run_dir = None
        self._part_rows_written = 0
        self._current_part_temperature = None
        self._current_part_start = None

        # Close handler
        self.master.protocol("WM_DELETE_WINDOW", self._on_close)

    def _fmt_temp_for_name(self, t):
        try:
            ft = float(t)
            return str(int(ft)) if abs(ft - int(ft)) < 1e-9 else str(ft)
        except Exception:
            return str(t)
    # -------------------------- UI helpers --------------------------
    def _update_ui_periodic(self):
        try:
            # Progress/elapsed
            if self._start_time:
                elapsed = int(time.time() - self._start_time)
            else:
                elapsed = 0
            target = int(self._target_duration) if self._target_duration else 0
            self.elapsed_label.config(text=f"Elapsed: {elapsed}s of {target}s")
            if self._target_duration and self._target_duration > 0:
                try:
                    self.progress_var.set(min(elapsed, self._target_duration))
                except Exception:
                    pass

            # Actual average rate since start (Hz)
            if self._start_time:
                elapsed_real = max(time.time() - self._start_time, 1e-9)
                rate_hz = self._samples_total / elapsed_real
            else:
                rate_hz = 0.0

            self.rate_label.config(
                text=f"Rate: {rate_hz:.1f} Hz | Samples: {self._samples_total} | Dropped: {self._dropped_total} | Reconnects: {self._reconnects_total}"
            )

            # Live preview
            with self._buf_lock:
                items = list(self._preview_buffer)[-50:]
            self.preview_list.delete(0, tk.END)
            for ts_str, x, y, z in items:
                self.preview_list.insert(tk.END, f"{ts_str}  X={x} Y={y} Z={z}")
        finally:
            if not self.stop_logging_flag:  # guard
                self.ui_update_job = self.master.after(500, self._update_ui_periodic)

    def _cancel_ui_update_job(self):
        try:
            if self.ui_update_job is not None:
                self.master.after_cancel(self.ui_update_job)
                self.ui_update_job = None
        except Exception:
            pass

    def _on_close(self):
        self.stop_logging()
        try:
            if self.ui_update_job is not None:
                self.master.after_cancel(self.ui_update_job)
                self.ui_update_job = None
        except Exception:
            pass
        self.master.destroy()

    def _temp_changed(self, a, b, tol=1e-6):
        try:
            return abs(float(a) - float(b)) > tol
        except Exception:
            return str(a) != str(b)

    def _show_about_dialog(self):
        messagebox.showinfo(
            "About",
            f"{APP_DISPLAY_NAME}\nVersion: {APP_VERSION}",
        )

    # -------------------------- Manifest helpers --------------------------
    def _write_manifest_atomic(self):
        if not self._manifest_path or self._manifest is None:
            return
        tmp_path = self._manifest_path + ".tmp"
        try:
            with open(tmp_path, 'w') as f:
                json.dump(self._manifest, f, indent=2)
            os.replace(tmp_path, self._manifest_path)
        except Exception:
            # Non-fatal
            pass

    def _log_event(self, kind, payload):
        event = {"ts": datetime.now().astimezone().isoformat(), "event": kind, "data": payload}
        try:
            self._manifest["events"].append(event)
            # Keep manifest from growing unbounded in memory
            if len(self._manifest["events"]) > 10000:
                self._manifest["events"] = self._manifest["events"][-5000:]
        except Exception:
            pass

    # -------------------------- Sleep inhibit --------------------------
    def _sleep_inhibit_start(self):
        try:
            system = platform.system()
            if system == 'Darwin':  # macOS
                # caffeinate keeps system awake while process is alive
                self._sleep_inhibit_handle = subprocess.Popen(['caffeinate', '-dims'])
            elif system == 'Windows':
                # Use SetThreadExecutionState to prevent system sleep
                ES_CONTINUOUS = 0x80000000
                ES_SYSTEM_REQUIRED = 0x00000001
                try:
                    ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
                except Exception:
                    pass
                # Start keepalive thread to refresh periodically
                if self._sleep_keepalive_stop is None:
                    self._sleep_keepalive_stop = threading.Event()

                def _keepalive():
                    ES_SYSTEM_REQUIRED_L = 0x00000001
                    while not self._sleep_keepalive_stop.is_set():
                        try:
                            ctypes.windll.kernel32.SetThreadExecutionState(ES_SYSTEM_REQUIRED_L)
                        except Exception:
                            pass
                        self._sleep_keepalive_stop.wait(30.0)

                self._sleep_keepalive_thread = threading.Thread(target=_keepalive, name="SleepKeepalive", daemon=True)
                self._sleep_keepalive_thread.start()
                self._sleep_inhibit_handle = None
            else:
                # Linux: systemd-inhibit if available
                try:
                    self._sleep_inhibit_handle = subprocess.Popen(['systemd-inhibit', '--what=sleep', '--who=AccelerationLogger', '--why=Long run logging', '--mode=block', 'sleep', 'infinity'])
                except Exception:
                    self._sleep_inhibit_handle = None
        except Exception:
            self._sleep_inhibit_handle = None

    def _sleep_inhibit_stop(self):
        try:
            if self._sleep_inhibit_handle:
                self._sleep_inhibit_handle.terminate()
                self._sleep_inhibit_handle = None
            # Stop Windows keepalive and clear ES_CONTINUOUS
            if platform.system() == 'Windows':
                try:
                    if self._sleep_keepalive_stop:
                        self._sleep_keepalive_stop.set()
                    if self._sleep_keepalive_thread and self._sleep_keepalive_thread.is_alive():
                        try:
                            self._sleep_keepalive_thread.join(timeout=2.0)
                        except Exception:
                            pass
                finally:
                    self._sleep_keepalive_thread = None
                    self._sleep_keepalive_stop = None
                    try:
                        ES_CONTINUOUS = 0x80000000
                        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
                    except Exception:
                        pass
        except Exception:
            pass

    # -------------------------- File rotation --------------------------
    def _open_new_part(self, run_dir, platform_name, temperature, speed):
        # Close existing part first
        self._close_current_part()

        # Make date subfolder
        date_str = datetime.now().strftime("%m%d%Y")
        self._current_date_str = date_str
        date_dir = os.path.join(run_dir, date_str)
        os.makedirs(date_dir, exist_ok=True)

        # Part filename
        self._part_index += 1
        # in _open_new_part
        temp_tag = self._fmt_temp_for_name(temperature)
        part_name = f"{platform_name}_{temp_tag}C_{speed}_{datetime.now().strftime('%y%m%d%H%M%S')}_part{self._part_index:03d}.csv"
        self._current_file_path = os.path.join(date_dir, part_name)
        self._current_file = open(self._current_file_path, mode='w', newline='')
        self._current_writer = csv.writer(self._current_file)
        # Write header
        self._current_writer.writerow(["ts_local", "sample", "X", "Y", "Z"])
        self._row_since_flush = 0
        self._part_rows_written = 0
        self._current_part_temperature = temperature
        self._current_part_start = time.time()

        # Update manifest with new part
        self._manifest["parts"].append({
            "path": os.path.relpath(self._current_file_path, start=run_dir),
            "created_iso": datetime.now().astimezone().isoformat(),
            "rows": 0,
            "sha256": None,
            "compressed": False,
            "temperature": str(self._fmt_temp_for_name(temperature)),
        })
        self._write_manifest_atomic()

    def _flush_current_file(self):
        if self._current_file:
            try:
                self._current_file.flush()
                os.fsync(self._current_file.fileno())
            except Exception:
                pass
            self._row_since_flush = 0

    def _close_current_part(self):
        if not self._current_file_path or not self._current_file:
            return
        try:
            # finalize rows count
            try:
                if self._manifest and self._manifest.get("parts"):
                    self._manifest["parts"][-1]["rows"] = self._part_rows_written
            except Exception:
                pass

            self._current_file.flush()
            os.fsync(self._current_file.fileno())
            self._current_file.close()

            # Compute checksum
            sha = hashlib.sha256()
            with open(self._current_file_path, 'rb') as rf:
                for chunk in iter(lambda: rf.read(1024 * 1024), b''):
                    sha.update(chunk)
            checksum = sha.hexdigest()
            if self._manifest and self._manifest.get("parts"):
                self._manifest["parts"][-1]["sha256"] = checksum

            # Optional compression
            if self.compress_var.get():
                gz_path = self._current_file_path + '.gz'
                with open(self._current_file_path, 'rb') as f_in, gzip.open(gz_path, 'wb') as f_out:
                    shutil.copyfileobj(f_in, f_out)
                try:
                    os.remove(self._current_file_path)
                except Exception:
                    pass
                self._current_file_path = gz_path
                if self._manifest and self._manifest.get("parts"):
                    self._manifest["parts"][-1]["path"] = os.path.relpath(self._current_file_path, start=self._run_dir)
                    self._manifest["parts"][-1]["compressed"] = True
                    # recompute checksum on gz
                    sha = hashlib.sha256()
                    with open(self._current_file_path, 'rb') as rf:
                        for chunk in iter(lambda: rf.read(1024*1024), b''):
                            sha.update(chunk)
                    self._manifest["parts"][-1]["sha256"] = sha.hexdigest()

        finally:
            self._current_file = None
            self._current_writer = None
            self._write_manifest_atomic()
    # -------------------------- Temperature schedule --------------------------
    def _parse_temperature_schedule_text(self, text):
        """
        Parse the schedule text into a sorted list of (start_hour_float, temp_float).
        Accepts comma or newline separated entries in the form "H:Temp" or "H=Temp".
        Example: "0:21, 2:26, 5.5:30".
        """
        entries = []
        if not text:
            return entries
        tokens = re.split(r"[\n,]+", text)
        pattern = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*[:=]\s*(-?[0-9]+(?:\.[0-9]+)?)\s*$")
        for tok in tokens:
            tok = tok.strip()
            if not tok:
                continue
            m = pattern.match(tok)
            if not m:
                raise ValueError(f"Invalid token: '{tok}'. Use 'hour:temp', e.g., 0:21")
            hour = float(m.group(1))
            temp = float(m.group(2))
            entries.append((hour, temp))
        entries.sort(key=lambda x: x[0])
        merged = []
        for hour, temp in entries:
            if merged and abs(merged[-1][0] - hour) < 1e-9:
                merged[-1] = (hour, temp)
            else:
                merged.append((hour, temp))
        return merged

    def _hours_to_hhmm(self, hours_value):
        try:
            total_minutes = int(round(float(hours_value) * 60))
            if total_minutes < 0:
                total_minutes = 0
        except Exception:
            total_minutes = 0
        hh = total_minutes // 60
        mm = total_minutes % 60
        return f"{hh:02d}:{mm:02d}"

    def _hhmm_to_hours(self, hhmm_text):
        s = hhmm_text.strip()
        m = re.match(r"^(\d+):(\d{2})$", s)
        if not m:
            raise ValueError("Use HH:MM format, e.g., 02:30")
        hh = int(m.group(1))
        mm = int(m.group(2))
        if mm < 0 or mm > 59:
            raise ValueError("Minutes must be 00-59")
        return float(hh) + (mm / 60.0)

    def _format_schedule_summary(self):
        if not self.temperature_schedule:
            return "Schedule: none"
        parts = [f"{self._hours_to_hhmm(h)}→{int(t) if abs(t - int(t)) < 1e-9 else t}C" for h, t in self.temperature_schedule]
        return "Schedule: " + ", ".join(parts)

    def open_schedule_dialog(self):
        dlg = tk.Toplevel(self.master)
        dlg.title("Temperature Schedule")
        dlg.transient(self.master)
        dlg.grab_set()

        header = tk.Label(dlg, text="Define when temperature changes after logging starts")
        header.pack(padx=10, pady=(10, 4), anchor='w')

        # Table
        columns = ("start", "temp")
        tree = ttk.Treeview(dlg, columns=columns, show="headings", height=8)
        tree.heading("start", text="Start (HH:MM)")
        tree.heading("temp", text="Temp (°C)")
        tree.column("start", width=140, anchor='center')
        tree.column("temp", width=120, anchor='center')
        tree.pack(padx=10, pady=5, fill='both', expand=True)

        # Working copy of rows
        rows = list(self.temperature_schedule) if self.temperature_schedule else []
        rows.sort(key=lambda x: x[0])

        def refresh_tree():
            for item in tree.get_children():
                tree.delete(item)
            for h, t in rows:
                tree.insert('', 'end', values=(self._hours_to_hhmm(h), f"{t}"))

        refresh_tree()

        # Editor row
        editor = tk.Frame(dlg)
        editor.pack(fill='x', padx=10, pady=5)
        tk.Label(editor, text="Start (HH:MM):").grid(row=0, column=0, padx=4, pady=2, sticky='e')
        start_entry = tk.Entry(editor, width=10)
        start_entry.grid(row=0, column=1, padx=4, pady=2, sticky='w')
        tk.Label(editor, text="Temp (°C):").grid(row=0, column=2, padx=8, pady=2, sticky='e')
        temp_entry = tk.Entry(editor, width=8)
        temp_entry.grid(row=0, column=3, padx=4, pady=2, sticky='w')

        # Actions
        btns = tk.Frame(dlg)
        btns.pack(fill='x', padx=10, pady=(0, 8))

        def add_entry():
            try:
                hours_v = self._hhmm_to_hours(start_entry.get())
                temp_v = float(temp_entry.get().strip())
                if hours_v < 0.0 or hours_v > 168.0:
                    raise ValueError("Start must be between 00:00 and 168:00 (7 days)")
                rows.append((hours_v, temp_v))
                rows.sort(key=lambda x: x[0])
                refresh_tree()
                start_entry.delete(0, tk.END)
                temp_entry.delete(0, tk.END)
            except Exception as e:
                messagebox.showerror("Invalid Entry", str(e))

        def selected_index():
            sel = tree.selection()
            if not sel:
                return None
            # Map selection to index
            sel_values = tree.item(sel[0], 'values')
            hhmm, temp_s = sel_values
            try:
                hours_v = self._hhmm_to_hours(hhmm)
            except Exception:
                return None
            temp_v = float(temp_s)
            for i, (h, t) in enumerate(rows):
                if abs(h - hours_v) < 1e-6 and abs(float(t) - temp_v) < 1e-6:
                    return i
            return None

        def edit_selected():
            idx = selected_index()
            if idx is None:
                messagebox.showinfo("Select Row", "Select a row to edit.")
                return
            try:
                hours_v = self._hhmm_to_hours(start_entry.get())
                temp_v = float(temp_entry.get().strip())
                if hours_v < 0.0 or hours_v > 168.0:
                    raise ValueError("Start must be between 00:00 and 168:00 (7 days)")
                rows[idx] = (hours_v, temp_v)
                rows.sort(key=lambda x: x[0])
                refresh_tree()
            except Exception as e:
                messagebox.showerror("Invalid Entry", str(e))

        def delete_selected():
            idx = selected_index()
            if idx is None:
                messagebox.showinfo("Select Row", "Select a row to delete.")
                return
            rows.pop(idx)
            refresh_tree()

        def move_selected(delta):
            idx = selected_index()
            if idx is None:
                messagebox.showinfo("Select Row", "Select a row to move.")
                return
            new_idx = idx + delta
            if new_idx < 0 or new_idx >= len(rows):
                return
            rows[idx], rows[new_idx] = rows[new_idx], rows[idx]
            refresh_tree()

        def on_tree_select(event):
            sel = tree.selection()
            if not sel:
                return
            vals = tree.item(sel[0], 'values')
            if not vals:
                return
            start_entry.delete(0, tk.END)
            temp_entry.delete(0, tk.END)
            start_entry.insert(0, vals[0])
            temp_entry.insert(0, vals[1])

        tree.bind('<<TreeviewSelect>>', on_tree_select)

        tk.Button(btns, text="Add", command=add_entry).pack(side=tk.LEFT, padx=4)
        tk.Button(btns, text="Update", command=edit_selected).pack(side=tk.LEFT, padx=4)
        tk.Button(btns, text="Delete", command=delete_selected).pack(side=tk.LEFT, padx=4)
        tk.Button(btns, text="Move Up", command=lambda: move_selected(-1)).pack(side=tk.LEFT, padx=4)
        tk.Button(btns, text="Move Down", command=lambda: move_selected(1)).pack(side=tk.LEFT, padx=4)

        # Save / Cancel
        actions = tk.Frame(dlg)
        actions.pack(fill='x', padx=10, pady=(0, 12))

        def on_save():
            try:
                # Normalize and store
                normalized = []
                for h, t in rows:
                    h = max(0.0, float(h))
                    t = float(t)
                    normalized.append((h, t))
                normalized.sort(key=lambda x: x[0])
                # Guardrails: first at 00:00, strictly increasing, within 0-168h
                if not normalized:
                    raise ValueError("Add at least one schedule entry.")
                if abs(normalized[0][0] - 0.0) > 1e-9:
                    raise ValueError("First entry must start at 00:00.")
                prev_h = -1.0
                for h, _ in normalized:
                    if h < 0.0 or h > 168.0:
                        raise ValueError("All start times must be within 00:00–168:00 (7 days).")
                    if h <= prev_h + 1e-9:
                        raise ValueError("Start times must be strictly increasing.")
                    prev_h = h
                self.temperature_schedule = normalized
                self.temp_schedule_summary.config(text=self._format_schedule_summary())
                dlg.destroy()
            except Exception as e:
                messagebox.showerror("Error", str(e))

        tk.Button(actions, text="Save", command=on_save).pack(side=tk.LEFT, padx=4)
        tk.Button(actions, text="Cancel", command=dlg.destroy).pack(side=tk.LEFT, padx=4)

    def _resolve_temperature(self, elapsed_seconds, default_temperature):
        """
        Given elapsed_seconds since run start, return temperature based on schedule.
        Falls back to default_temperature if no schedule. After last schedule entry, holds last value.
        """
        try:
            if not self.temperature_schedule:
                return float(default_temperature)
            elapsed_hours = float(elapsed_seconds) / 3600.0
            current_temp = None
            for start_hour, temp in self.temperature_schedule:
                if elapsed_hours + 1e-9 >= start_hour:
                    current_temp = temp
                else:
                    break
            if current_temp is None:
                return float(default_temperature)
            return current_temp
        except Exception:
            try:
                return float(default_temperature)
            except Exception:
                return default_temperature

    # -------------------------- Serial reconnection --------------------------
    def _attempt_reconnect(self, ser):
        try:
            try:
                ser.close()
            except Exception:
                pass
            self._reconnects_total += 1
            self._manifest["stats"]["reconnects"] = self._reconnects_total
            start = time.time()
            while not self.stop_logging_flag and (time.time() - start) < 60:
                port = find_arduino_port()
                if port:
                    try:
                        new_ser = serial.Serial(port, 115200, timeout=1)
                        time.sleep(2)
                        self._log_event("reconnected", {"port": port})
                        return new_ser
                    except Exception as e:
                        self._log_event("reconnect_failed", {"error": str(e)[:120]})
                time.sleep(1.0)
            # Could not reconnect within 60s; record and keep trying lazily
            self._log_event("reconnect_timeout", {})
            return None
        except Exception as e:
            self._log_event("reconnect_unhandled", {"error": str(e)[:120]})
            return None

    # -------------------------- Disk space --------------------------
    def _check_disk_space(self, path, min_free_gb=2.0):
        try:
            stat = shutil.disk_usage(path)
            free_gb = stat.free / (1024 ** 3)
            return free_gb >= min_free_gb
        except Exception:
            return True

    def start_logging(self):
        platform = self.platform_entry.get().strip()
        speed = self.speed_entry.get().strip()
        temperature = self.temp_entry.get().strip()
        # Validate numeric temperature
        try:
            float(temperature)
        except ValueError:
            messagebox.showerror("Invalid Input", "Temperature must be a number (°C).")
            return
        try:
            duration = int(self.duration_entry.get().strip())
        except ValueError:
            messagebox.showerror("Invalid Input", "Duration must be an integer (seconds).")
            return

        if not platform or not speed or not temperature:
            messagebox.showerror("Missing Information", "Please fill in all fields.")
            return

        self.start_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL)

        # Reset stop flag and start logging in a background thread
        self.stop_logging_flag = False
        self.logging_thread = threading.Thread(target=self.log_data, args=(platform, speed, duration, temperature, self.compress_var.get()), daemon=True)
        self.logging_thread.start()

        # Kick off UI updater
        if self.ui_update_job is None:
            self.ui_update_job = self.master.after(500, self._update_ui_periodic)

        # Configure progress bar
        self._target_duration = duration
        if duration <= 0:
            self.progress_bar.config(mode='indeterminate')
            self.progress_bar.start(200)
        else:
            self.progress_bar.config(mode='determinate', maximum=duration)
            self.progress_bar.stop()

    def stop_logging(self):
        self.stop_logging_flag = True
        self.stop_button.config(state=tk.DISABLED)
        self.progress_bar.stop()
        # Cancel UI updater
        self._cancel_ui_update_job()

    def log_data(self, platform, speed, duration, temperature, compress):
        # Open the serial port (auto-detected) and set up run folders/manifest
        try:
            auto_port = find_arduino_port()
            if auto_port is None:
                self.master.after(0, lambda: messagebox.showerror("Port not found",
                                     "Couldn’t locate an Arduino. Plug it in and try again."))
                self.master.after(0, lambda: self.start_button.config(state=tk.NORMAL))
                return

            ser = serial.Serial(auto_port, 115200, timeout=1)
        except serial.SerialException as e:
            self.master.after(0, lambda: messagebox.showerror("Serial Error", f"Error opening serial port: {e}"))
            self.master.after(0, lambda: self.start_button.config(state=tk.NORMAL))
            return

        time.sleep(2)  # Allow time for the Arduino to reset

        # Prepare run directories and manifest
        start_dt = datetime.now().astimezone()
        start_timestamp_compact = start_dt.strftime("%y%m%d%H%M%S")  # yymmddHHMMSS for folder id
        base_dir = os.path.join(os.path.expanduser("~"), "Desktop", "ARDUINO_AcclLogs")
        run_dir = os.path.join(base_dir, start_timestamp_compact)
        os.makedirs(run_dir, exist_ok=True)
        self._run_dir = run_dir

        self._manifest_path = os.path.join(run_dir, "manifest.json")
        # Snapshot temperature schedule at start
        schedule_snapshot = list(self.temperature_schedule) if self.temperature_schedule else []
        used_port = auto_port
        self._manifest = {
            "platform": platform,
            "speed": speed,
            "temperature": temperature,
            "default_temperature": temperature,
            "temperature_schedule": [[h, t] for (h, t) in schedule_snapshot],
            "start_iso": start_dt.isoformat(),
            "target_duration_s": int(duration),
            "compress_gzip": bool(compress),
            "serial_port": used_port,
            "events": [],
            "parts": [],
            "stats": {"samples": 0, "malformed": 0, "reconnects": 0, "dropped_estimate": 0},
        }
        self._write_manifest_atomic()

        # Inhibit system sleep
        self._sleep_inhibit_start()

        # Initialize runtime state
        self._start_time = time.time()
        self._target_duration = duration
        self._samples_total = 0
        self._dropped_total = 0
        self._reconnects_total = 0
        last_data_time = time.time()
        last_flush_time = time.time()
        last_manifest_time = time.time()
        rotation_minutes = 60  # rotate files hourly (and on date change)
        self._current_part_start = time.time()
        self._current_date_str = datetime.now().strftime("%m%d%Y")
        self._part_index = 0

        # Open first part file using schedule-based starting temperature
        starting_temp = self._resolve_temperature(0.0, temperature)
        self._open_new_part(run_dir, platform, starting_temp, speed)

        try:
            sample_index = 0
            while not self.stop_logging_flag:
                try:
                    if ser is None:
                        # try to reconnect periodically
                        ser = self._attempt_reconnect(None)
                        if ser is None:
                            time.sleep(5.0)
                            continue
                    line = ser.readline().decode('utf-8', errors='ignore').strip()
                    now = time.time()
                    # Watchdog: if no data for >5s, attempt reconnect
                    if not line:
                        if now - last_data_time > 5.0:
                            self._log_event("watchdog_timeout", {"since_s": round(now - last_data_time, 3)})
                            ser = self._attempt_reconnect(ser)
                            last_data_time = now
                        # Also check duration even when empty
                        if self._target_duration > 0 and (now - self._start_time) >= self._target_duration:
                            break
                        # Rotation handled before write; nothing here
                        # Periodic flush/manifest even if no data
                        if now - last_flush_time > 5.0:
                            self._flush_current_file()
                            last_flush_time = now
                        if now - last_manifest_time > 60.0:
                            self._write_manifest_atomic()
                            last_manifest_time = now
                        # Disk space check
                        if not self._check_disk_space(base_dir):
                            self._log_event("low_disk_space_stop", {})
                            break
                        continue

                    parts = line.split(',')
                    if len(parts) != 3:
                        self._manifest["stats"]["malformed"] += 1
                        self._log_event("malformed_line", {"line": line[:120]})
                        continue

                    try:
                        x = int(parts[0])
                        y = int(parts[1])
                        z = int(parts[2])
                    except ValueError:
                        self._manifest["stats"]["malformed"] += 1
                        self._log_event("parse_error", {"line": line[:120]})
                        continue

                    last_data_time = now
                    sample_index += 1
                    self._samples_total += 1
                    self._manifest["stats"]["samples"] = self._samples_total
                    self._last_sample_times.append(now)

                    # Single rotation check BEFORE write
                    current_temp = self._resolve_temperature(now - self._start_time, temperature)
                    need_new_part = False
                    if (now - self._current_part_start) >= rotation_minutes * 60:
                        need_new_part = True
                    elif datetime.now().strftime("%m%d%Y") != self._current_date_str:
                        need_new_part = True
                    elif self._temp_changed(current_temp, self._current_part_temperature):
                        need_new_part = True

                    if need_new_part:
                        self._open_new_part(run_dir, platform, current_temp, speed)

                    ts_local = datetime.now().astimezone().isoformat()
                    # Write row with local timestamp
                    self._current_writer.writerow([ts_local, sample_index, x, y, z])
                    self._row_since_flush += 1
                    self._part_rows_written += 1

                    # Update preview buffer
                    with self._buf_lock:
                        self._preview_buffer.append((ts_local, x, y, z))

                    # Periodic flush
                    if self._row_since_flush >= 200 or (time.time() - last_flush_time) > 5.0:
                        self._flush_current_file()
                        last_flush_time = time.time()

                    # Periodically persist manifest
                    if time.time() - last_manifest_time > 60.0:
                        self._write_manifest_atomic()
                        last_manifest_time = time.time()

                    # Disk space monitoring
                    if not self._check_disk_space(base_dir):
                        self._log_event("low_disk_space_stop", {})
                        break

                    # Stop logging if the set duration has passed
                    if self._target_duration > 0 and (time.time() - self._start_time) >= self._target_duration:
                        break
                except serial.SerialException as e:
                    self._log_event("serial_exception", {"error": str(e)[:200]})
                    ser = self._attempt_reconnect(ser)
                    continue
                except Exception as e:
                    # Log and continue; do not crash long runs on transient issues
                    self._log_event("unexpected_exception", {"error": str(e)[:200]})
                    time.sleep(0.1)
                    continue
        finally:
            try:
                ser.close()
            except Exception:
                pass
            # Close file and finalize manifest
            self._close_current_part()
            self._sleep_inhibit_stop()
            self._manifest["end_iso"] = datetime.now().astimezone().isoformat()
            self._write_manifest_atomic()
            self.master.after(0, self.progress_bar.stop)

        # Notify UI
        final_msg = f"Logging stopped. Run folder: {run_dir}"
        print(final_msg)
        self.master.after(0, lambda: messagebox.showinfo("Logging Stopped", final_msg))
        self.master.after(0, lambda: self.start_button.config(state=tk.NORMAL))
        self.master.after(0, lambda: self.stop_button.config(state=tk.DISABLED))


if __name__ == "__main__":
    root = tk.Tk()
    gui = AccelLoggerGUI(root)
    root.mainloop()
