import tkinter as tk
from tkinter import messagebox, ttk
import threading, time, csv, os, json, hashlib, gzip, shutil, subprocess, platform, ctypes
import math
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
        master.minsize(860, 540)
        self._forbidden_input_chars = {":", "*", "?", "<", ">", "|", "\\", "/"}
        self._input_max_lengths = {
            "platform": 64,
            "speed": 128,
            "duration": 16,
        }

        menubar = tk.Menu(master)
        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="About", command=self._show_about_dialog)
        menubar.add_cascade(label="Help", menu=help_menu)
        master.config(menu=menubar)

        style = ttk.Style(master)
        try:
            style.theme_use(style.theme_use())
        except Exception:
            pass
        style.configure("Input.TEntry", foreground="#111111")
        style.configure("Placeholder.TEntry", foreground="#888888")

        master.grid_rowconfigure(0, weight=1)
        master.grid_columnconfigure(0, weight=1)

        main_frame = ttk.Frame(master, padding=(14, 12))
        main_frame.grid(row=0, column=0, sticky="nsew")
        main_frame.grid_columnconfigure(0, weight=1)
        main_frame.grid_rowconfigure(2, weight=1)

        setup_frame = ttk.LabelFrame(main_frame, text="Run Setup", padding=(12, 10))
        setup_frame.grid(row=0, column=0, sticky="ew")
        setup_frame.grid_columnconfigure(1, weight=1)

        ttk.Label(setup_frame, text="Platform name").grid(row=0, column=0, padx=(0, 10), pady=4, sticky="w")
        self.platform_entry = ttk.Entry(setup_frame, width=44)
        self.platform_entry.grid(row=0, column=1, padx=0, pady=4, sticky="ew")
        self.platform_entry.config(
            validate="key",
            validatecommand=(master.register(self._validate_entry_input), "%P", "platform"),
        )

        ttk.Label(setup_frame, text="Experiment setting").grid(row=1, column=0, padx=(0, 10), pady=4, sticky="w")
        self.speed_entry = ttk.Entry(setup_frame, width=44)
        self.speed_entry.grid(row=1, column=1, padx=0, pady=4, sticky="ew")
        self.speed_entry.config(
            validate="key",
            validatecommand=(master.register(self._validate_entry_input), "%P", "speed"),
        )

        ttk.Label(setup_frame, text="Logging duration (hours)").grid(row=2, column=0, padx=(0, 10), pady=4, sticky="w")
        self.duration_entry = ttk.Entry(setup_frame, width=20)
        self.duration_entry.grid(row=2, column=1, padx=0, pady=4, sticky="w")
        self.duration_entry.config(
            validate="key",
            validatecommand=(master.register(self._validate_entry_input), "%P", "duration"),
        )

        controls_frame = ttk.Frame(setup_frame)
        controls_frame.grid(row=3, column=0, columnspan=2, pady=(8, 2), sticky="ew")
        controls_frame.grid_columnconfigure(0, weight=1)

        self.start_button = ttk.Button(controls_frame, text="Start Logging", command=self.start_logging)
        self.start_button.grid(row=0, column=0, padx=(0, 6), sticky="w")
        self.stop_button = ttk.Button(controls_frame, text="Stop Logging", command=self.stop_logging, state=tk.DISABLED)
        self.stop_button.grid(row=0, column=1, padx=(6, 0), sticky="w")

        status_frame = ttk.LabelFrame(main_frame, text="Status", padding=(12, 10))
        status_frame.grid(row=1, column=0, pady=(10, 0), sticky="ew")
        status_frame.grid_columnconfigure(1, weight=1)

        ttk.Label(status_frame, text="Progress (elapsed / target)").grid(row=0, column=0, padx=(0, 10), pady=2, sticky="w")
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(status_frame, orient='horizontal', mode='determinate', variable=self.progress_var)
        self.progress_bar.grid(row=0, column=1, pady=2, sticky='ew')

        self.elapsed_label = ttk.Label(status_frame, text="Elapsed: 0s of 0s")
        self.elapsed_label.grid(row=1, column=0, columnspan=2, sticky='w', pady=(4, 1))

        self.rate_label = ttk.Label(status_frame, text="Rate: 0.0 Hz | Samples: 0 | Dropped: 0 | Reconnects: 0")
        self.rate_label.grid(row=2, column=0, columnspan=2, sticky='w')

        preview_frame = ttk.LabelFrame(main_frame, text="Live Preview (latest 50)", padding=(12, 10))
        preview_frame.grid(row=2, column=0, pady=(10, 0), sticky="nsew")
        preview_frame.grid_columnconfigure(0, weight=1)
        preview_frame.grid_rowconfigure(0, weight=1)

        self.preview_list = tk.Listbox(preview_frame, height=10)
        self.preview_list.grid(row=0, column=0, sticky='nsew')
        preview_scroll = ttk.Scrollbar(preview_frame, orient="vertical", command=self.preview_list.yview)
        preview_scroll.grid(row=0, column=1, sticky="ns", padx=(8, 0))
        self.preview_list.configure(yscrollcommand=preview_scroll.set)

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
        self._current_part_start = None
        self._placeholder_map = {}
        self._placeholder_active = set()

        self._setup_placeholders()

        # Close handler
        self.master.protocol("WM_DELETE_WINDOW", self._on_close)

    # -------------------------- UI helpers --------------------------
    def _validate_entry_input(self, proposed_value, field_name):
        max_len = self._input_max_lengths.get(field_name, 128)
        if len(proposed_value) > max_len:
            return False
        for ch in proposed_value:
            if ch in self._forbidden_input_chars:
                return False
        return True

    def _validate_field_constraints(self, value, field_name, label):
        max_len = self._input_max_lengths.get(field_name, 128)
        if any(ch in self._forbidden_input_chars for ch in value):
            chars = ": * ? < > | \\ /"
            return f"{label} cannot contain any of these characters: {chars}"
        if len(value) > max_len:
            return f"{label} must be {max_len} characters or fewer."
        return None

    def _setup_placeholders(self):
        self._placeholder_map = {
            self.platform_entry: ("platform", "Zantiks"),
            self.speed_entry: ("speed", "R85C10AD x R64H06DBD G4 motor P150,400,2000"),
            self.duration_entry: ("duration", "72"),
        }
        for entry, (_field, placeholder) in self._placeholder_map.items():
            entry.configure(style="Input.TEntry")
            self._set_entry_placeholder(entry, placeholder)
            entry.bind("<FocusIn>", self._on_entry_focus_in, add="+")
            entry.bind("<FocusOut>", self._on_entry_focus_out, add="+")

    def _set_entry_placeholder(self, entry, placeholder):
        if entry not in self._placeholder_active and not entry.get().strip():
            entry.delete(0, tk.END)
            entry.insert(0, placeholder)
            entry.configure(style="Placeholder.TEntry")
            self._placeholder_active.add(entry)

    def _clear_entry_placeholder(self, entry):
        if entry in self._placeholder_active:
            entry.delete(0, tk.END)
            entry.configure(style="Input.TEntry")
            self._placeholder_active.discard(entry)

    def _on_entry_focus_in(self, event):
        self._clear_entry_placeholder(event.widget)

    def _on_entry_focus_out(self, event):
        info = self._placeholder_map.get(event.widget)
        if not info:
            return
        _field, placeholder = info
        self._set_entry_placeholder(event.widget, placeholder)

    def _get_clean_entry_value(self, entry):
        if entry in self._placeholder_active:
            return ""
        return entry.get().strip()

    def _update_ui_periodic(self):
        try:
            # Progress/elapsed
            if self._start_time:
                elapsed = int(time.time() - self._start_time)
            else:
                elapsed = 0
            if self._target_duration and self._target_duration > 0:
                target_hours = self._target_duration / 3600.0
                self.elapsed_label.config(text=f"Elapsed: {elapsed}s of {target_hours:g}h")
            else:
                self.elapsed_label.config(text=f"Elapsed: {elapsed}s of continuous")
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
    def _open_new_part(self, run_dir, platform_name, speed):
        # Close existing part first
        self._close_current_part()

        # Make date subfolder
        date_str = datetime.now().strftime("%m%d%Y")
        self._current_date_str = date_str
        date_dir = os.path.join(run_dir, date_str)
        os.makedirs(date_dir, exist_ok=True)

        # Part filename
        self._part_index += 1
        part_name = f"{platform_name}_{speed}_{datetime.now().strftime('%y%m%d%H%M%S')}_part{self._part_index:03d}.csv"
        self._current_file_path = os.path.join(date_dir, part_name)
        self._current_file = open(self._current_file_path, mode='w', newline='')
        self._current_writer = csv.writer(self._current_file)
        # Write header
        self._current_writer.writerow(["ts_local", "sample", "X", "Y", "Z"])
        self._row_since_flush = 0
        self._part_rows_written = 0
        self._current_part_start = time.time()

        # Update manifest with new part
        self._manifest["parts"].append({
            "path": os.path.relpath(self._current_file_path, start=run_dir),
            "created_iso": datetime.now().astimezone().isoformat(),
            "rows": 0,
            "sha256": None,
            "compressed": False,
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
        platform = self._get_clean_entry_value(self.platform_entry)
        speed = self._get_clean_entry_value(self.speed_entry)
        duration_text = self._get_clean_entry_value(self.duration_entry)
        for field_name, label, value in (
            ("platform", "Platform Name", platform),
            ("speed", "Experiment setting", speed),
            ("duration", "Logging Duration", duration_text),
        ):
            validation_error = self._validate_field_constraints(value, field_name, label)
            if validation_error:
                messagebox.showerror("Invalid Input", validation_error)
                return
        try:
            duration_hours = float(duration_text)
            if not math.isfinite(duration_hours) or duration_hours < 0:
                raise ValueError
            duration = duration_hours * 3600.0
        except ValueError:
            messagebox.showerror("Invalid Input", "Duration must be a number in hours (0 for continuous).")
            return

        if not platform or not speed:
            messagebox.showerror("Missing Information", "Please fill in all fields.")
            return

        self.start_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL)

        # Reset stop flag and start logging in a background thread
        self.stop_logging_flag = False
        self.logging_thread = threading.Thread(target=self.log_data, args=(platform, speed, duration), daemon=True)
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

    def log_data(self, platform, speed, duration):
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
        used_port = auto_port
        self._manifest = {
            "platform": platform,
            "speed": speed,
            "start_iso": start_dt.isoformat(),
            "target_duration_s": int(round(duration)),
            "compress_gzip": True,
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

        self._open_new_part(run_dir, platform, speed)

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
                    need_new_part = False
                    if (now - self._current_part_start) >= rotation_minutes * 60:
                        need_new_part = True
                    elif datetime.now().strftime("%m%d%Y") != self._current_date_str:
                        need_new_part = True

                    if need_new_part:
                        self._open_new_part(run_dir, platform, speed)

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
