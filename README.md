# Acceleration Logger GUI

A desktop Tkinter GUI for logging Arduino accelerometer-style analog streams to CSV with run metadata, file rotation, and manifest tracking.

## Features

- Simple UI to capture platform, temperature, speed setting, and duration.
- Auto-detects common Arduino serial ports and logs at 115200 baud.
- Writes CSV parts with local timestamps and run metadata (`manifest.json`).
- Optional gzip compression of rotated CSV files.
- Live preview, elapsed/progress display, and basic logging stats.
- Optional temperature schedule editor for long runs.

## Repository Layout

- `src/AccelerationLoggerGUI.py`: main desktop application.
- `firmware/ACCL_logger_Arduino.ino`: Arduino sketch that outputs `x,y,z` CSV lines.
- `Build/AccelerationLoggerGUI.spec`: PyInstaller spec file.

## Data Format and Output

- Expected serial input line format: `x,y,z` (integer values).
- Output root folder on desktop: `~/Desktop/ARDUINO_AcclLogs/<run_id>/`.
- Each run includes:
  - One or more rotated CSV part files (hourly/date/temperature-change based).
  - `manifest.json` with run metadata, parts, checksums, and event history.

## Requirements

- Python 3.10+
- `pyserial>=3.5`
- Arduino-compatible board streaming CSV analog values

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

## Run Locally

```bash
python src/AccelerationLoggerGUI.py
```

## Arduino Firmware

1. Open `firmware/ACCL_logger_Arduino.ino` in Arduino IDE.
2. Upload to your board.
3. Confirm it streams comma-separated values at 115200 baud.

## Build a Windows Executable

PyInstaller builds should be produced on Windows for reliable `.exe` output:

```bash
python -m pip install pyinstaller
pyinstaller Build/AccelerationLoggerGUI.spec
```

The built executable will be in `dist/`.

## Hardware Notes

- Default port detection matches common Arduino/CH340 descriptors and VID/PID hints.
- Ensure your board is connected and readable before starting a long run.
- Hardware used: Arduino Micro + ADXL335 accelerometer.
- Reference publication: see [1].

### Arduino Micro + ADXL335 Rig (Quick Wiring)

- Pin connections used in this setup:
  - `ADXL335 VCC` -> `Arduino Micro +3V3`
  - `ADXL335 GND` -> `Arduino Micro GND`
  - `ADXL335 Xout` -> `Arduino Micro A5` (`ap1` in firmware)
  - `ADXL335 Yout` -> `Arduino Micro A4` (`ap2` in firmware)
  - `ADXL335 Zout` -> `Arduino Micro A3` (`ap3` in firmware)
- Keep wiring short/secure and mechanically anchor both boards so vibration does not introduce intermittent electrical contact.
- The ADXL335 was mounted using 3M `300LSE` double-sided tape for secure attachment to the test surface.
- Leave optional breakout pins (for example, self-test) disconnected during standard logging unless your board documentation says otherwise.
- Validate setup before long runs by checking that serial output remains stable at rest and changes predictably when each axis is tilted.
- The current firmware defines the analog input mapping in `ap1`, `ap2`, and `ap3` inside `firmware/ACCL_logger_Arduino.ino`; adjust those constants if you rewire.

Arduino Micro pin schema:

![Arduino Micro pin schema](docs/images/arduino-micro-pin-schema.png)

Reference setup photos:

![Arduino Micro wiring reference](docs/images/arduino-micro-wiring.png)
![ADXL335 mounted on plate](docs/images/adxl335-mounted-setup.png)

## References

1. Titos I, Juginovic A, Vaccaro A, Nambara K, Gorelik P, Mazor O, Rogulja D. A gut-secreted peptide suppresses arousability from sleep. Cell. 2023 Mar 30;186(7):1382-1397.e21. doi:10.1016/j.cell.2023.02.022. PMID:36958331. https://www.sciencedirect.com/science/article/pii/S0092867423001654?via%3Dihub

## Contributing

Contributions are welcome. Please:

1. Open an issue describing the bug or feature request.
2. Keep pull requests focused and small.
3. Include clear reproduction steps for bug fixes.

## Reporting Issues

When filing an issue, include:

- OS and Python version
- Board model and serial adapter type (if known)
- A sample of serial output
- Steps to reproduce and expected vs actual behavior

## License

This project is licensed under the MIT License. See `LICENSE`.
