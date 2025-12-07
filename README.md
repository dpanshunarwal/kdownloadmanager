# K Download Manager

A powerful multi-threaded download manager built with Python and KivyMD.

## Features

- **Multi-threaded Downloads**: Up to 256 parallel connections for maximum speed
- **Pause/Resume**: Full control over your downloads
- **Modern UI**: Clean, card-based interface
- **Real-time Progress**: Live speed, ETA, and progress tracking
- **Cross-platform**: Works on Android, Windows, Linux, macOS

## Installation

### Desktop (Windows/Linux/macOS)
```bash
pip install kivy kivymd requests
python main_kivy.py
```

### Android APK Build
```bash
# Install buildozer
pip install buildozer

# Build APK
buildozer android debug
```

## Usage

1. Paste download URL
2. Set thread count (1-256)
3. Click START
4. Monitor progress with pause/resume/cancel options

## Requirements

- Python 3.7+
- Kivy
- KivyMD
- Requests

## License

MIT License - Feel free to use and modify!