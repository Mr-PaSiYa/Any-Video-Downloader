# Any Video Downloader

A simple, modern desktop application to download videos from **YouTube**, **TikTok**, **Instagram** and **Other Supported Sites**(public posts/reels).

Built with Python + Flask + pywebview + yt-dlp.

## Features

- Clean dark-mode UI
- Video preview (title + thumbnail + duration)
- Choose save folder (native Windows picker)
- Multiple formats: MP4 video, WebM video, MP3 audio
- Quality selection (up to 1080p or best available)
- No console window flashes
- Portable .exe version (single file)

## Downloads

| File                              | Description                                      | Who it's for                  |
|-----------------------------------|--------------------------------------------------|-------------------------------|
| `VideoDownloader.exe`             | Ready-to-run Windows executable (recommended)    | Normal users                  |
| Source code (zip / clone repo)    | Full Python source code                          | Developers / modifiers        |

→ **Latest release**: [Releases page](https://github.com/YOUR-USERNAME/VideoDownloader/releases)

## How to use the .exe (recommended for most people)

1. Download `VideoDownloader.exe` from the Releases page
2. **Important**: For full functionality, place these files in the **same folder** as `VideoDownloader.exe`:
   - `yt-dlp.exe`  
   - `ffmpeg.exe`  
   - `ffprobe.exe`
   
   (You can download them from official sources — links below in "Requirements")

3. Double-click `VideoDownloader.exe`
4. Paste any supported video link
5. Choose format/quality/save folder → Download

## How to run from source code (for developers)

### Requirements

- Python 3.8+
- Install dependencies:
  ```bash
  pip install flask pywebview easygui pyinstaller
