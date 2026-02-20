import webview
import threading
from flask import Flask, request, render_template_string, jsonify
import subprocess
import os
import sys
import json
import re
import time
from datetime import datetime
from easygui import diropenbox
import requests
import tempfile

app = Flask(__name__)

# Global progress tracking
download_progress = {
    "percent": 0,
    "downloaded": "0B",
    "total": "Unknown",
    "speed": "0B/s",
    "eta": "Unknown",
    "status": "idle",
    "stage": "idle",
    "filename": "",
    "error": "",
    "mode": "unknown"
}
download_lock = threading.Lock()

def resource_path(relative_path):
    """Get absolute path to resource, works for dev and for PyInstaller"""
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

CONFIG_FILE = 'save_path.txt'
LOG_FILE = 'download_log.txt'

def load_folder():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            path = f.read().strip()
            if os.path.isdir(path):
                return path
    return os.getcwd()

DOWNLOAD_FOLDER = load_folder()

def log_message(message):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(f"[{timestamp}] {message}\n")

def sanitize_filename(filename):
    filename = re.sub(r'[<>:"/\\|?*]', '', filename)
    filename = filename.strip()
    return filename[:200] if len(filename) > 200 else filename

def format_filesize(bytes_val):
    if not bytes_val:
        return None
    if bytes_val < 1024 * 1024:
        return f"{bytes_val/1024:.0f}KB"
    elif bytes_val < 1024 * 1024 * 1024:
        return f"{bytes_val/(1024*1024):.1f}MB"
    else:
        return f"{bytes_val/(1024*1024*1024):.2f}GB"

def parse_formats_from_info(info):
    """
    Parse yt-dlp JSON info dict and return organised format lists:
    - video_formats: list of video streams (with audio or muxed)
    - audio_formats: list of audio-only streams
    Each entry: {format_id, label, height, ext, vcodec, acodec, filesize}
    """
    raw_formats = info.get('formats', [])
    
    video_formats = []
    audio_formats = []
    seen_resolutions = {}  # height -> best format entry (prefer larger filesize)

    for f in raw_formats:
        fmt_id = f.get('format_id', '')
        vcodec = f.get('vcodec', 'none')
        acodec = f.get('acodec', 'none')
        height = f.get('height')
        width = f.get('width')
        ext = f.get('ext', '')
        fps = f.get('fps')
        filesize = f.get('filesize') or f.get('filesize_approx')
        tbr = f.get('tbr')

        is_video = vcodec and vcodec != 'none'
        is_audio = acodec and acodec != 'none'

        if is_video and height:
            size_str = format_filesize(filesize) if filesize else (f"{tbr:.0f}kbps" if tbr else '')
            fps_str = f" {fps:.0f}fps" if fps and fps > 30 else ''
            label = f"{height}p{fps_str}"
            if size_str:
                label += f" ({size_str})"

            entry = {
                'format_id': fmt_id,
                'height': height,
                'label': label,
                'ext': ext,
                'vcodec': vcodec,
                'acodec': acodec,
                'filesize': filesize or 0,
                'tbr': tbr or 0,
            }

            # Keep the best (highest bitrate / filesize) version for each height
            key = height
            if key not in seen_resolutions or entry['tbr'] > seen_resolutions[key]['tbr']:
                seen_resolutions[key] = entry

        elif is_audio and not is_video:
            abr = f.get('abr')
            size_str = format_filesize(filesize) if filesize else (f"{abr:.0f}kbps" if abr else '')
            label = f"{ext.upper()} audio"
            if abr:
                label += f" {abr:.0f}kbps"
            if size_str and filesize:
                label += f" ({size_str})"
            audio_formats.append({
                'format_id': fmt_id,
                'label': label,
                'ext': ext,
                'abr': abr or 0,
                'filesize': filesize or 0,
            })

    # Sort video by height descending
    video_formats = sorted(seen_resolutions.values(), key=lambda x: x['height'], reverse=True)
    # Sort audio by bitrate descending
    audio_formats = sorted(audio_formats, key=lambda x: x['abr'], reverse=True)

    return video_formats, audio_formats


def download_m3u8_advanced(url, output_path, quality='best'):
    """Advanced m3u8 downloader using yt-dlp to get stream URL then ffmpeg."""
    log_message("=== ADVANCED M3U8 DOWNLOAD MODE ===")
    
    try:
        log_message("Step 1: Getting m3u8 playlist URL...")
        
        if quality == 'best' or not str(quality).isdigit():
            format_arg = 'best'
        else:
            format_arg = f'bestvideo[height<={quality}]+bestaudio/best[height<={quality}]/best'
        
        cmd = [
            resource_path('yt-dlp.exe'),
            '--get-url',
            '-f', format_arg,
            '--no-playlist',
            '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            '--add-header', 'Accept:*/*',
            '--add-header', 'Accept-Language:en-US,en;q=0.9',
            '--no-check-certificate',
            url
        ]
        
        result = subprocess.run(
            cmd, capture_output=True, text=True, check=False,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        
        if result.returncode != 0:
            log_message(f"ERROR: Could not get m3u8 URL: {result.stderr}")
            return False, "Failed to get video URL. Site may require login."
        
        m3u8_url = result.stdout.strip().splitlines()[0]
        log_message(f"m3u8 URL obtained: {m3u8_url[:100]}...")
        
        log_message("Step 2: Downloading with FFmpeg in advanced mode...")
        
        with download_lock:
            download_progress["status"] = "downloading"
            download_progress["percent"] = 0
        
        headers_str = 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        if 'xhamster' in url.lower():
            headers_str += '\r\nReferer: https://xhamster.com/'
        elif 'pornhub' in url.lower():
            headers_str += '\r\nReferer: https://www.pornhub.com/'
        elif 'xvideos' in url.lower():
            headers_str += '\r\nReferer: https://www.xvideos.com/'
        
        ffmpeg_cmd = [
            resource_path('ffmpeg.exe'),
            '-headers', headers_str,
            '-reconnect', '1',
            '-reconnect_streamed', '1',
            '-reconnect_delay_max', '5',
            '-i', m3u8_url,
            '-c', 'copy',
            '-bsf:a', 'aac_adtstoasc',
            '-y',
            output_path
        ]
        
        process = subprocess.Popen(
            ffmpeg_cmd,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            bufsize=1, universal_newlines=True
        )
        
        total_duration = None
        error_404_count = 0
        
        for line in iter(process.stdout.readline, ''):
            line = line.strip()
            if line:
                log_message(f"FFmpeg: {line}")
            
            if '404' in line or 'Not Found' in line:
                error_404_count += 1
                if error_404_count >= 3:
                    process.kill()
                    return False, "Segments expired (404 errors). Try downloading immediately after getting the URL."
            
            if 'Duration:' in line and total_duration is None:
                dur_match = re.search(r'Duration: (\d{2}):(\d{2}):(\d{2})\.\d+', line)
                if dur_match:
                    h, m, s = map(int, dur_match.groups())
                    total_duration = h * 3600 + m * 60 + s
            
            if 'time=' in line:
                time_match = re.search(r'time=(\d{2}):(\d{2}):(\d{2})\.\d+', line)
                if time_match:
                    h, m, s = map(int, time_match.groups())
                    current_time = h * 3600 + m * 60 + s
                    if total_duration:
                        percent = min(100, int((current_time / total_duration) * 100))
                        with download_lock:
                            download_progress["percent"] = percent
                            download_progress["downloaded"] = f"{current_time}s"
                            download_progress["total"] = f"{total_duration}s"
        
        process.wait()
        
        if process.returncode == 0 and os.path.exists(output_path):
            return True, "Success"
        else:
            return False, f"FFmpeg failed (exit code {process.returncode})"
            
    except Exception as e:
        log_message(f"EXCEPTION in advanced download: {str(e)}")
        return False, str(e)


HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Any Video Downloader</title>
    <style>
        :root {
            --bg:         #0d1117;
            --surface:    #161b22;
            --card:       #1e2533;
            --border:     #30363d;
            --text:       #e6edf3;
            --muted:      #8b949e;
            --accent:     #3b82f6;
            --accent2:    #60a5fa;
            --accent-h:   #2563eb;
            --green:      #56d364;
            --green-bg:   #0d2818;
            --green-bd:   #238636;
            --red:        #f85149;
            --red-bg:     #2d1117;
            --red-bd:     #da3633;
            --yellow:     #e3b341;
            --yellow-bg:  #271d0a;
            --yellow-bd:  #9e6a03;
        }
        *, *::before, *::after { margin:0; padding:0; box-sizing:border-box; }

        body {
            background: var(--bg);
            color: var(--text);
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
            height: 100vh;
            overflow: hidden;
            display: flex;
            flex-direction: column;
            padding: 14px 16px 12px;
        }

        /* ─── Header ──────────────────────────────── */
        .app-header {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 9px;
            margin-bottom: 12px;
            flex-shrink: 0;
        }
        h1 {
            font-size: 1.2rem;
            font-weight: 700;
            letter-spacing: -0.3px;
            background: linear-gradient(135deg, var(--accent2), var(--accent));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }

        /* ─── Labels ──────────────────────────────── */
        label {
            font-size: 0.72rem;
            color: var(--muted);
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.6px;
            display: block;
            margin-bottom: 4px;
        }
        .field { margin-bottom: 9px; flex-shrink: 0; }

        /* ─── Inputs ──────────────────────────────── */
        .input-wrapper { position: relative; }
        input[type="text"], select {
            width: 100%;
            padding: 8px 12px;
            border-radius: 8px;
            border: 1px solid var(--border);
            background: var(--surface);
            color: var(--text);
            font-size: 0.875rem;
            transition: border-color .15s, box-shadow .15s;
            appearance: none;
        }
        input[type="text"] { padding-right: 34px; }
        input[type="text"]:focus, select:focus {
            outline: none;
            border-color: var(--accent);
            box-shadow: 0 0 0 3px rgba(59,130,246,.18);
        }
        select {
            background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6'%3E%3Cpath d='M0 0l5 6 5-6z' fill='%238b949e'/%3E%3C/svg%3E");
            background-repeat: no-repeat;
            background-position: right 10px center;
            padding-right: 28px;
            cursor: pointer;
        }
        select option { background: #1e2533; }

        .clear-btn {
            position: absolute; right: 8px; top: 50%;
            transform: translateY(-50%);
            background: transparent; border: none;
            color: var(--muted); cursor: pointer;
            padding: 3px 6px; font-size: 1rem; line-height: 1;
            opacity: 0; pointer-events: none;
            transition: opacity .15s, color .15s, background .15s;
            border-radius: 4px;
        }
        .clear-btn.visible { opacity: 1; pointer-events: auto; }
        .clear-btn:hover { color: var(--text); background: var(--border); }

        /* ─── Skeleton shimmer ────────────────────── */
        @keyframes shimmer {
            0%   { background-position: -400px 0; }
            100% { background-position:  400px 0; }
        }
        .skeleton {
            background: linear-gradient(90deg, var(--surface) 25%, #222c3a 50%, var(--surface) 75%);
            background-size: 800px 100%;
            animation: shimmer 1.4s infinite linear;
            border-radius: 6px;
        }

        /* ─── Preview card ────────────────────────── */
        #preview-skeleton {
            display: none;
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 10px;
            flex-direction: row;
            gap: 10px;
            align-items: center;
            margin-bottom: 9px;
            flex-shrink: 0;
        }
        #preview-skeleton.show { display: flex; }
        .sk-thumb { width: 80px; height: 45px; border-radius: 6px; flex-shrink: 0; }
        .sk-lines { flex: 1; display: flex; flex-direction: column; gap: 7px; }
        .sk-line-a { height: 11px; width: 85%; }
        .sk-line-b { height: 9px;  width: 55%; }

        #preview {
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 10px;
            display: none;
            flex-direction: row;
            gap: 10px;
            align-items: center;
            margin-bottom: 9px;
            flex-shrink: 0;
            animation: fadeIn .25s ease;
        }
        #preview.show { display: flex; }
        @keyframes fadeIn { from { opacity:0; transform:translateY(-4px); } to { opacity:1; transform:none; } }

        #preview-thumb {
            width: 80px; height: 45px;
            object-fit: cover;
            border-radius: 6px;
            background: var(--card);
            flex-shrink: 0;
        }
        #preview-info { flex: 1; min-width: 0; }
        #preview-title {
            font-weight: 600; font-size: 0.82rem; line-height: 1.35;
            overflow: hidden; text-overflow: ellipsis;
            display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
            margin-bottom: 3px;
        }
        #preview-meta { color: var(--muted); font-size: 0.72rem; }

        /* ─── Format + Quality row ────────────────── */
        .options-row {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 9px;
            margin-bottom: 9px;
            flex-shrink: 0;
        }
        .option-group { display: flex; flex-direction: column; }
        #quality-group { transition: opacity .2s; }
        #quality-group.hidden { opacity: .3; pointer-events: none; }

        /* ─── Quality skeleton ────────────────────── */
        #quality-skeleton {
            display: none;
            height: 34px;
            border-radius: 8px;
        }
        #quality-skeleton.show { display: block; }

        /* ─── Advanced toggle ─────────────────────── */
        .toggle-row {
            display: flex; align-items: center; justify-content: space-between;
            padding: 8px 12px;
            background: var(--surface); border: 1px solid var(--border);
            border-radius: 8px; margin-bottom: 9px; flex-shrink: 0;
        }
        .toggle-info { display: flex; flex-direction: column; gap: 1px; }
        .toggle-title { font-size: 0.82rem; font-weight: 600; color: var(--text); }
        .toggle-hint  { font-size: 0.7rem;  color: var(--muted); }
        .toggle-badge {
            display: inline-block;
            font-size: 0.62rem;
            font-weight: 700;
            padding: 1px 6px;
            border-radius: 4px;
            margin-left: 6px;
            vertical-align: middle;
            letter-spacing: 0.4px;
            text-transform: uppercase;
        }
        .toggle-badge.auto-on  { background: rgba(34,197,94,.15); color: #4ade80; border: 1px solid rgba(34,197,94,.3); }
        .toggle-badge.auto-off { background: rgba(239,68,68,.12); color: #f87171; border: 1px solid rgba(239,68,68,.25); }
        .toggle-badge.manual   { background: rgba(148,163,184,.1); color: var(--muted); border: 1px solid var(--border); }
        /* Lock styling when toggle is forced */
        #downloaderToggle.locked { pointer-events: none; opacity: 0.6; }
        .toggle-switch {
            position: relative; width: 42px; height: 22px;
            background: var(--border); border-radius: 11px;
            cursor: pointer; transition: background .25s; flex-shrink: 0;
        }
        .toggle-switch.active { background: var(--accent); }
        .toggle-slider {
            position: absolute; top: 2px; left: 2px;
            width: 18px; height: 18px;
            background: white; border-radius: 50%;
            transition: transform .25s; box-shadow: 0 1px 3px rgba(0,0,0,.4);
        }
        .toggle-switch.active .toggle-slider { transform: translateX(20px); }

        /* ─── Folder row ──────────────────────────── */
        .folder-row {
            display: flex; align-items: stretch; gap: 7px;
            margin-bottom: 9px; flex-shrink: 0;
        }
        #folder-path {
            flex: 1; min-width: 0;
            font-size: 0.72rem; color: var(--muted);
            word-break: break-all; line-height: 1.3;
            padding: 6px 10px;
            background: var(--surface); border: 1px solid var(--border);
            border-radius: 8px;
        }
        #choose-folder {
            flex-shrink: 0; padding: 6px 12px;
            background: var(--card); border: 1px solid var(--border);
            color: var(--text); border-radius: 8px; cursor: pointer;
            font-size: 0.78rem; font-weight: 600; white-space: nowrap;
            transition: background .15s, border-color .15s; width: auto;
        }
        #choose-folder:hover { background: var(--border); }

        /* ─── Download button ─────────────────────── */
        #download-btn {
            width: 100%; padding: 10px; border: none; border-radius: 8px;
            cursor: pointer; font-weight: 700; font-size: 0.95rem;
            background: linear-gradient(135deg, var(--accent), #1d6ef5);
            color: white; letter-spacing: .3px;
            transition: opacity .15s, transform .1s, box-shadow .15s;
            margin-bottom: 9px; flex-shrink: 0;
            box-shadow: 0 2px 8px rgba(59,130,246,.3);
        }
        #download-btn:hover:not(:disabled) {
            opacity: .92; transform: translateY(-1px);
            box-shadow: 0 4px 14px rgba(59,130,246,.4);
        }
        #download-btn:active:not(:disabled) { transform: translateY(0); box-shadow: none; }
        #download-btn:disabled { background: var(--border); box-shadow: none; cursor: not-allowed; opacity: .65; }

        /* ─── Progress panel ──────────────────────── */
        #progress-container {
            display: none;
            flex-direction: column;
            gap: 0;
            margin-bottom: 8px;
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 10px;
            overflow: hidden;
            flex-shrink: 0;
            animation: fadeIn .2s ease;
        }
        #progress-container.show { display: flex; }

        /* Striped animated top bar */
        #progress-bar {
            height: 4px;
            background: var(--card);
            position: relative;
            overflow: hidden;
        }
        #progress-fill {
            height: 100%;
            width: 0%;
            position: relative;
            transition: width .5s cubic-bezier(.4,0,.2,1);
            background: linear-gradient(90deg, var(--accent), var(--accent2), var(--accent));
            background-size: 200% 100%;
            animation: gradientShift 2s linear infinite;
        }
        @keyframes gradientShift {
            0%   { background-position: 0% 0%; }
            100% { background-position: 200% 0%; }
        }
        #progress-fill::after {
            content: '';
            position: absolute; inset: 0;
            background: linear-gradient(90deg,
                transparent 0%, rgba(255,255,255,.15) 50%, transparent 100%);
            background-size: 200% 100%;
            animation: sheen 1.6s ease-in-out infinite;
        }
        @keyframes sheen {
            0%   { background-position: -200% 0; }
            100% { background-position:  200% 0; }
        }

        /* Stats row inside the panel */
        .progress-body {
            padding: 9px 12px;
            display: flex;
            flex-direction: column;
            gap: 5px;
        }
        .progress-top {
            display: flex;
            align-items: center;
            justify-content: space-between;
        }
        #progress-label {
            font-size: 0.78rem;
            font-weight: 600;
            color: var(--text);
            display: flex;
            align-items: center;
            gap: 6px;
        }
        #progress-pct {
            font-size: 0.78rem;
            font-weight: 700;
            color: var(--accent2);
            font-variant-numeric: tabular-nums;
        }
        .progress-stats {
            display: flex;
            gap: 14px;
        }
        .stat {
            display: flex;
            flex-direction: column;
            gap: 1px;
        }
        .stat-key {
            font-size: 0.62rem;
            color: var(--muted);
            text-transform: uppercase;
            letter-spacing: .5px;
            font-weight: 600;
        }
        .stat-val {
            font-size: 0.75rem;
            color: var(--text);
            font-weight: 600;
            font-variant-numeric: tabular-nums;
        }

        /* Indeterminate pulse when pct == 0 */
        #progress-fill.indeterminate {
            width: 35% !important;
            animation: indeterminate 1.4s ease-in-out infinite, gradientShift 2s linear infinite;
        }
        @keyframes indeterminate {
            0%   { margin-left: -35%; }
            100% { margin-left: 110%; }
        }

        /* ─── Status pill ─────────────────────────── */
        #status {
            padding: 8px 12px;
            border-radius: 8px;
            text-align: center;
            font-size: 0.82rem;
            font-weight: 500;
            min-height: 34px;
            display: flex; align-items: center; justify-content: center;
            background: var(--surface); border: 1px solid var(--border);
            color: var(--muted); flex-shrink: 0;
            transition: background .2s, border-color .2s, color .2s;
        }
        #status.success { background: var(--green-bg);  border-color: var(--green-bd);  color: var(--green);  }
        #status.error   { background: var(--red-bg);    border-color: var(--red-bd);    color: var(--red);    }
        #status.loading { background: #0c1e3d;          border-color: #1d3a6e;          color: var(--accent2);}
        #status.warn    { background: var(--yellow-bg); border-color: var(--yellow-bd); color: var(--yellow); }

        /* ─── Spinner ─────────────────────────────── */
        .spinner {
            display: inline-block; vertical-align: middle;
            width: 13px; height: 13px; margin-right: 6px;
            border: 2px solid rgba(147,197,253,.25);
            border-top-color: var(--accent2);
            border-radius: 50%;
            animation: spin .75s linear infinite;
        }
        @keyframes spin { to { transform: rotate(360deg); } }

        /* ─── Log FAB & modal ─────────────────────── */
        .log-fab {
            position: fixed; bottom: 14px; right: 14px; z-index: 100;
            width: 38px; height: 38px; border-radius: 50%;
            background: var(--card); border: 1px solid var(--border);
            color: var(--muted); cursor: pointer;
            display: flex; align-items: center; justify-content: center;
            font-size: 1rem;
            box-shadow: 0 4px 12px rgba(0,0,0,.4);
            transition: background .2s, transform .2s, box-shadow .2s;
        }
        .log-fab:hover {
            background: var(--border); color: var(--text);
            transform: translateY(-2px); box-shadow: 0 6px 16px rgba(0,0,0,.5);
        }
        .log-modal {
            display: none; position: fixed; inset: 0;
            background: rgba(0,0,0,.75); z-index: 200;
            align-items: center; justify-content: center;
            padding: 16px; backdrop-filter: blur(6px);
        }
        .log-modal.show { display: flex; }
        .log-content {
            background: var(--card); border: 1px solid var(--border);
            border-radius: 10px; width: 100%; max-width: 600px;
            max-height: 80vh; display: flex; flex-direction: column; overflow: hidden;
        }
        .log-header {
            padding: 12px 16px; border-bottom: 1px solid var(--border);
            display: flex; justify-content: space-between; align-items: center;
        }
        .log-header h2 { font-size: 0.95rem; color: var(--accent2); }
        .log-close {
            background: transparent; border: none; color: var(--muted);
            font-size: 1.3rem; cursor: pointer; width: 28px; height: 28px;
            display: flex; align-items: center; justify-content: center;
            border-radius: 6px; transition: all .15s;
        }
        .log-close:hover { background: var(--border); color: var(--text); }
        .log-body {
            flex: 1; overflow-y: auto; padding: 12px 14px;
            font-family: 'Cascadia Code', 'Fira Code', 'Courier New', monospace;
            font-size: 0.73rem; line-height: 1.6; color: #9aa5b1; background: var(--bg);
        }
        .log-body pre { margin: 0; white-space: pre-wrap; word-wrap: break-word; }
        .log-footer {
            padding: 9px 14px; border-top: 1px solid var(--border);
            display: flex; gap: 8px;
        }
        .log-footer button {
            flex: 1; margin: 0; padding: 7px; font-size: 0.8rem; border-radius: 6px;
        }
        .log-clear   { background: var(--red-bg);  color: var(--red);    border: 1px solid var(--red-bd); }
        .log-clear:hover { background: #3d1117; }
        .log-refresh { background: var(--accent);  color: white; border: none; }
        .log-refresh:hover { background: var(--accent-h); }
    </style>
</head>
<body>

    <div class="app-header">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="url(#g)" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
            <defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stop-color="#60a5fa"/><stop offset="100%" stop-color="#3b82f6"/></linearGradient></defs>
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
            <polyline points="7 10 12 15 17 10"/>
            <line x1="12" y1="15" x2="12" y2="3"/>
        </svg>
        <h1>Any Video Downloader</h1>
    </div>

    <!-- URL field -->
    <div class="field">
        <label>Video URL</label>
        <div class="input-wrapper">
            <input type="text" id="url"
                placeholder="Paste YouTube, TikTok, Instagram, Twitter…"
                autocomplete="off" spellcheck="false">
            <button class="clear-btn" id="clearBtn" onclick="clearUrl()">×</button>
        </div>
    </div>

    <!-- Preview skeleton -->
    <div id="preview-skeleton">
        <div class="skeleton sk-thumb"></div>
        <div class="sk-lines">
            <div class="skeleton sk-line-a"></div>
            <div class="skeleton sk-line-b"></div>
        </div>
    </div>

    <!-- Preview card -->
    <div id="preview">
        <img id="preview-thumb" src="" alt="" />
        <div id="preview-info">
            <div id="preview-title"></div>
            <div id="preview-meta"></div>
        </div>
    </div>

    <!-- Format + Quality -->
    <div class="options-row">
        <div class="option-group field">
            <label>Format</label>
            <select id="format" onchange="onFormatChange()">
                <option value="mp4">MP4 (Video)</option>
                <option value="webm">WebM (Video)</option>
                <option value="mp3">MP3 (Audio only)</option>
            </select>
        </div>
        <div class="option-group field" id="quality-group">
            <label>Quality</label>
            <div id="quality-skeleton" class="skeleton"></div>
            <select id="quality">
                <option value="bestvideo+bestaudio/best">⭐ Best Available</option>
                <option value="1080">1080p</option>
                <option value="720">720p</option>
                <option value="480">480p</option>
                <option value="360">360p</option>
            </select>
        </div>
    </div>

    <!-- Advanced toggle -->
    <div class="toggle-row" id="toggle-row">
        <div class="toggle-info">
            <span class="toggle-title">Advanced M3U8 Mode
                <span id="toggle-badge" class="toggle-badge"></span>
            </span>
            <span class="toggle-hint" id="toggle-hint">For streaming sites with expiring tokens</span>
        </div>
        <div class="toggle-switch" id="downloaderToggle" onclick="toggleDownloader()">
            <div class="toggle-slider"></div>
        </div>
    </div>

    <!-- Save location -->
    <div class="field">
        <label>Save Location</label>
        <div class="folder-row">
            <div id="folder-path">{{ folder | safe }}</div>
            <button id="choose-folder" onclick="chooseFolder()">Browse…</button>
        </div>
    </div>

    <!-- Download button (label set by JS via BTN_LABEL constant) -->
    <button id="download-btn" onclick="startDownload()">Download</button>

    <!-- Progress panel -->
    <div id="progress-container">
        <div id="progress-bar">
            <div id="progress-fill" class="indeterminate"></div>
        </div>
        <div class="progress-body">
            <div class="progress-top">
                <div id="progress-label">
                    <span class="spinner" id="prog-spinner"></span>
                    <span id="prog-stage">Initializing…</span>
                </div>
                <span id="progress-pct">0%</span>
            </div>
            <div class="progress-stats" id="progress-stats" style="display:none">
                <div class="stat">
                    <span class="stat-key">Downloaded</span>
                    <span class="stat-val" id="stat-down">—</span>
                </div>
                <div class="stat">
                    <span class="stat-key">Total</span>
                    <span class="stat-val" id="stat-total">—</span>
                </div>
                <div class="stat">
                    <span class="stat-key">Speed</span>
                    <span class="stat-val" id="stat-speed">—</span>
                </div>
                <div class="stat">
                    <span class="stat-key">ETA</span>
                    <span class="stat-val" id="stat-eta">—</span>
                </div>
            </div>
        </div>
    </div>

    <!-- Status -->
    <div id="status">Ready</div>

    <!-- Log FAB -->
    <button class="log-fab" onclick="openLogModal()" title="View Download Logs">📋</button>

    <!-- Log modal -->
    <div class="log-modal" id="logModal">
        <div class="log-content">
            <div class="log-header">
                <h2>📋 Download Log</h2>
                <button class="log-close" onclick="closeLogModal()">×</button>
            </div>
            <div class="log-body" id="logBody"><pre>Loading…</pre></div>
            <div class="log-footer">
                <button class="log-clear"   onclick="clearLog()">🗑 Clear</button>
                <button class="log-refresh" onclick="refreshLog()">↻ Refresh</button>
            </div>
        </div>
    </div>

    <script>
    // ── State ─────────────────────────────────────────────────────────────────
    let currentFolder  = {{ folder_json | safe }};
    let isDownloading  = false;
    let useAdvanced    = false;
    let progressInterval = null;
    let cachedFormats  = { video: [], audio: [] };

    // ── DOM refs ──────────────────────────────────────────────────────────────
    const urlInput      = document.getElementById('url');
    const clearBtn      = document.getElementById('clearBtn');
    const formatSel     = document.getElementById('format');
    const qualitySel    = document.getElementById('quality');
    const qualityGroup  = document.getElementById('quality-group');
    const qualitySkel   = document.getElementById('quality-skeleton');
    const previewSkel   = document.getElementById('preview-skeleton');
    const previewCard   = document.getElementById('preview');
    const progContainer = document.getElementById('progress-container');
    const progFill      = document.getElementById('progress-fill');
    const progStage     = document.getElementById('prog-stage');
    const progPct       = document.getElementById('progress-pct');
    const progStats     = document.getElementById('progress-stats');
    const statDown      = document.getElementById('stat-down');
    const statTotal     = document.getElementById('stat-total');
    const statSpeed     = document.getElementById('stat-speed');
    const statEta       = document.getElementById('stat-eta');

    // Button label constant — SVG icon + text, no spinner ever in the button
    const BTN_LABEL = `<svg style="vertical-align:middle;margin-right:6px" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>Download`;

    // ── URL input ─────────────────────────────────────────────────────────────
    urlInput.addEventListener('input', onUrlInput);
    urlInput.addEventListener('paste', () => setTimeout(() => { onUrlInput(); triggerPreview(); }, 100));
    urlInput.addEventListener('keydown', e => { if (e.key === 'Enter') triggerPreview(); });

    function onUrlInput() {
        const url = urlInput.value.trim();
        clearBtn.classList.toggle('visible', url.length > 0);
        if (url) applyToggleForUrl(url);
        else     resetToggle();
    }

    function clearUrl() {
        urlInput.value = '';
        previewCard.classList.remove('show');
        previewSkel.classList.remove('show');
        document.getElementById('preview-thumb').src = '';
        cachedFormats = { video: [], audio: [] };
        resetQualityToDefaults();
        resetToggle();
        onUrlInput();
        urlInput.focus();
    }

    // ── Site classification ───────────────────────────────────────────────────
    // Sites that ALWAYS use standard yt-dlp (no M3U8 needed)
    const STANDARD_SITES = [
        'youtube.com', 'youtu.be',
        'twitter.com', 'x.com',
        'instagram.com',
        'tiktok.com',
        'facebook.com', 'fb.watch',
        'vimeo.com',
        'twitch.tv',
        'dailymotion.com',
        'reddit.com',
        'soundcloud.com',
        'bilibili.com',
    ];
    // Sites that ALWAYS need Advanced M3U8 mode
    const M3U8_SITES = [
        'pornhub.com',
        'xvideos.com',
        'xhamster.com',
        'xnxx.com',
        'redtube.com',
        'tube8.com',
        'youporn.com',
        'spankbang.com',
        'eporner.com',
        'hqporner.com',
        'txxx.com',
        'tnaflix.com',
        'empflix.com',
        'porntrex.com',
        'bravotube.net',
        'streamtape.com',
        'doodstream.com',
        'upstream.to',
        'mixdrop.co',
    ];

    // 'auto' = set by URL detection, 'manual' = user overrode it
    let toggleMode = 'auto';

    function getHostname(url) {
        try { return new URL(url.startsWith('http') ? url : 'https://' + url).hostname.replace('www.', ''); }
        catch { return ''; }
    }

    function classifyUrl(url) {
        const host = getHostname(url);
        if (!host) return 'unknown';
        if (STANDARD_SITES.some(s => host === s || host.endsWith('.' + s))) return 'standard';
        if (M3U8_SITES.some(s => host === s || host.endsWith('.' + s)))     return 'streaming';
        return 'unknown';
    }

    function applyToggleForUrl(url) {
        const kind = classifyUrl(url);
        const toggle     = document.getElementById('downloaderToggle');
        const badge      = document.getElementById('toggle-badge');
        const hint       = document.getElementById('toggle-hint');

        if (kind === 'standard') {
            // Force OFF — standard sites don't need M3U8
            useAdvanced = false;
            toggleMode  = 'auto';
            toggle.classList.remove('active', 'locked');
            toggle.classList.add('locked');
            badge.className = 'toggle-badge auto-off';
            badge.textContent = 'Auto: Off';
            hint.textContent = 'Standard yt-dlp mode (best for this site)';
        } else if (kind === 'streaming') {
            // Force ON — streaming sites need M3U8
            useAdvanced = true;
            toggleMode  = 'auto';
            toggle.classList.add('active', 'locked');
            badge.className = 'toggle-badge auto-on';
            badge.textContent = 'Auto: On';
            hint.textContent = 'Streaming site detected — M3U8 mode enabled automatically';
        } else {
            // Unknown site — unlock and let user decide, restore their preference
            toggleMode = 'manual';
            toggle.classList.remove('locked');
            badge.className = 'toggle-badge manual';
            badge.textContent = 'Manual';
            hint.textContent = 'Unknown site — enable if standard download fails';
            // Restore last manual preference
            useAdvanced = localStorage.getItem('use_advanced_manual') === 'true';
            toggle.classList.toggle('active', useAdvanced);
        }
    }

    function resetToggle() {
        const toggle = document.getElementById('downloaderToggle');
        const badge  = document.getElementById('toggle-badge');
        const hint   = document.getElementById('toggle-hint');
        toggle.classList.remove('active', 'locked');
        badge.className = 'toggle-badge';
        badge.textContent = '';
        hint.textContent = 'For streaming sites with expiring tokens';
        useAdvanced = false;
        toggleMode  = 'auto';
    }

    // ── Advanced toggle (manual override — only works when not locked) ────────
    function toggleDownloader() {
        const toggle = document.getElementById('downloaderToggle');
        if (toggle.classList.contains('locked')) return;   // blocked for known sites
        useAdvanced = !useAdvanced;
        toggle.classList.toggle('active', useAdvanced);
        // Save manual preference separately so auto-detect doesn't stomp it
        localStorage.setItem('use_advanced_manual', useAdvanced ? 'true' : 'false');
    }

    // ── Folder ────────────────────────────────────────────────────────────────
    function chooseFolder() {
        fetch('/choose_folder', { method: 'POST' })
            .then(r => r.json())
            .then(data => {
                if (data.path) {
                    currentFolder = data.path;
                    document.getElementById('folder-path').textContent = data.path;
                    showStatus('Save folder updated.', 'success');
                }
            })
            .catch(() => showStatus('Error selecting folder.', 'error'));
    }

    // ── On load ───────────────────────────────────────────────────────────────
    window.addEventListener('load', () => {
        document.getElementById('download-btn').innerHTML = BTN_LABEL;
        formatSel.value = localStorage.getItem('yt_format') || 'mp4';
        onFormatChange(false);
        onUrlInput();  // will call applyToggleForUrl if URL already in box
    });

    // ── Format change ─────────────────────────────────────────────────────────
    function onFormatChange(repopulate = true) {
        const fmt = formatSel.value;
        localStorage.setItem('yt_format', fmt);
        if (fmt === 'mp3') {
            qualityGroup.classList.add('hidden');
        } else {
            qualityGroup.classList.remove('hidden');
            if (repopulate) populateQualityDropdown(fmt);
        }
    }

    function populateQualityDropdown(fmt) {
        const saved = localStorage.getItem('yt_quality');
        let filtered = [];
        if (fmt === 'mp4') {
            filtered = cachedFormats.video.filter(f =>
                f.ext === 'mp4' || f.vcodec.toLowerCase().includes('avc') || f.vcodec.toLowerCase().includes('h264')
            );
            if (!filtered.length) filtered = cachedFormats.video;
        } else {
            filtered = cachedFormats.video.filter(f =>
                f.ext === 'webm' || f.vcodec.toLowerCase().includes('vp') || f.vcodec.toLowerCase().includes('av01')
            );
            if (!filtered.length) filtered = cachedFormats.video;
        }
        if (!filtered.length) { resetQualityToDefaults(); return; }

        qualitySel.innerHTML = '<option value="best">⭐ Best Available</option>';
        filtered.forEach(f => {
            const o = document.createElement('option');
            // Encode as "id:{format_id}:{height}" so backend never confuses
            // a numeric format_id (e.g. "136") with a pixel-height filter
            o.value = `id:${f.format_id}:${f.height}`;
            o.textContent = f.label;
            qualitySel.appendChild(o);
        });
        // Restore saved selection by label-match since values now include prefix
        const savedLabel = localStorage.getItem('yt_quality_label');
        if (savedLabel) {
            const match = Array.from(qualitySel.options).find(o => o.textContent === savedLabel);
            if (match) qualitySel.value = match.value;
        }
    }

    function resetQualityToDefaults() {
        qualitySel.innerHTML = `
            <option value="best">⭐ Best Available</option>
            <option value="h:1080">1080p</option>
            <option value="h:720">720p</option>
            <option value="h:480">480p</option>
            <option value="h:360">360p</option>`;
    }

    qualitySel.addEventListener('change', () => {
        // Save by label so it survives across different videos
        const sel = qualitySel.options[qualitySel.selectedIndex];
        if (sel) localStorage.setItem('yt_quality_label', sel.textContent);
    });

    // ── Preview fetch ─────────────────────────────────────────────────────────
    let previewTimer = null;
    function triggerPreview() {
        clearTimeout(previewTimer);
        previewTimer = setTimeout(fetchPreview, 300);
    }

    function showPreviewSkeleton() {
        previewCard.classList.remove('show');
        previewSkel.classList.add('show');
        // Quality skeleton
        qualitySkel.classList.add('show');
        qualitySel.style.display = 'none';
    }

    function hidePreviewSkeleton() {
        previewSkel.classList.remove('show');
        qualitySkel.classList.remove('show');
        qualitySel.style.display = '';
    }

    function fetchPreview() {
        const url = urlInput.value.trim();
        if (!url) { previewCard.classList.remove('show'); return; }

        showPreviewSkeleton();
        showStatus('<span class="spinner"></span>Fetching video info…', 'loading');

        fetch('/preview', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url })
        })
        .then(r => r.json())
        .then(data => {
            hidePreviewSkeleton();
            if (data.error) { showStatus('⚠ ' + data.error, 'warn'); return; }

            document.getElementById('preview-title').textContent = data.title || '';
            document.getElementById('preview-meta').textContent =
                [data.duration, data.uploader].filter(Boolean).join(' · ');

            const thumb = document.getElementById('preview-thumb');
            if (data.thumbnail) { thumb.src = data.thumbnail; thumb.style.display = 'block'; }
            else                { thumb.style.display = 'none'; }
            previewCard.classList.add('show');

            cachedFormats = { video: data.video_formats || [], audio: data.audio_formats || [] };
            if (formatSel.value !== 'mp3') populateQualityDropdown(formatSel.value);

            showStatus('✓ Ready — select quality and hit Download', 'success');
        })
        .catch(() => {
            hidePreviewSkeleton();
            showStatus('⚠ Could not fetch video info. You can still try downloading.', 'warn');
        });
    }

    // ── Download ──────────────────────────────────────────────────────────────
    function startDownload() {
        if (isDownloading) return;
        const url = urlInput.value.trim();
        if (!url) { showStatus('Please paste a video URL first.', 'error'); return; }

        isDownloading = true;
        const btn = document.getElementById('download-btn');
        btn.disabled = true;

        // Reset and show the progress panel — it's the only download indicator
        progFill.style.background = '';
        progFill.style.backgroundSize = '';
        progFill.style.animation = '';
        progFill.style.width = '';
        progFill.classList.add('indeterminate');
        progPct.textContent = '0%';
        progStage.textContent = 'Preparing…';
        progStats.style.display = 'none';
        document.getElementById('prog-spinner').style.display = '';
        progContainer.classList.add('show');
        // Status bar: quiet message only — the progress panel is the visual indicator
        showStatus('Download in progress…', 'loading');

        fetch('/download', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                url,
                format:  formatSel.value,
                quality: qualitySel.value,
                mode:    useAdvanced ? 'advanced' : 'standard'
            })
        })
        .then(r => r.json())
        .then(data => {
            clearInterval(progressInterval);
            if (data.error) {
                setProgressDone(false);
                showStatus('✕ ' + data.error, 'error');
            } else {
                setProgressDone(true);
                showStatus('✓ Download complete!', 'success');
            }
        })
        .catch(() => {
            clearInterval(progressInterval);
            setProgressDone(false);
            showStatus('✕ Connection error.', 'error');
        })
        .finally(() => {
            isDownloading = false;
            btn.disabled = false;
            btn.innerHTML = BTN_LABEL;
            setTimeout(() => progContainer.classList.remove('show'), 6000);
        });

        // Poll progress
        progressInterval = setInterval(() => {
            fetch('/progress').then(r => r.json()).then(p => {
                const pct = Math.min(100, p.percent || 0);

                // Switch from indeterminate to real fill once we have data
                if (pct > 0 || p.stage === 'video' || p.stage === 'audio' || p.stage === 'merging') {
                    progFill.classList.remove('indeterminate');
                    progFill.style.width = pct + '%';
                    progPct.textContent = pct + '%';
                }

                // Stage label + bar color
                const stageLabels = {
                    'starting': 'Preparing…',
                    'video':    '⬇ Downloading video',
                    'audio':    '🎵 Downloading audio',
                    'merging':  '⚙ Merging streams',
                    'idle':     'Starting…',
                };
                progStage.textContent = stageLabels[p.stage] || 'Downloading…';
                updateBarColor(p.stage);

                // Show stats only during actual download phases (not merging)
                if ((p.stage === 'video' || p.stage === 'audio') && p.total && p.total !== '—' && p.total !== 'Unknown') {
                    progStats.style.display = 'flex';
                    statDown.textContent  = p.downloaded || '—';
                    statTotal.textContent = p.total      || '—';
                    statSpeed.textContent = p.speed      || '—';
                    statEta.textContent   = p.eta        || '—';
                } else if (p.stage === 'merging') {
                    progStats.style.display = 'flex';
                    statDown.textContent  = '—';
                    statTotal.textContent = '—';
                    statSpeed.textContent = '—';
                    statEta.textContent   = '—';
                }

                if (['completed','failed','error'].includes(p.status))
                    clearInterval(progressInterval);
            }).catch(() => {});
        }, 700);
    }

    function setProgressDone(success) {
        progFill.classList.remove('indeterminate');
        progFill.style.animation = 'none';
        progFill.style.width = '100%';
        progPct.textContent = '100%';
        progStage.textContent = success ? '✓ Complete!' : '✕ Failed';
        document.getElementById('prog-spinner').style.display = 'none';
        if (success) progFill.style.background = 'linear-gradient(90deg, #16a34a, #22c55e, #4ade80)';
        else         progFill.style.background = 'linear-gradient(90deg, #dc2626, #ef4444, #f87171)';
        progStats.style.display = 'none';
    }

    // Update bar tint per phase
    function updateBarColor(stage) {
        if (stage === 'merging') {
            progFill.style.background = 'linear-gradient(90deg, #7c3aed, #a78bfa, #7c3aed)';
            progFill.style.backgroundSize = '200% 100%';
        } else if (stage === 'audio') {
            progFill.style.background = 'linear-gradient(90deg, #0891b2, #22d3ee, #0891b2)';
            progFill.style.backgroundSize = '200% 100%';
        } else {
            progFill.style.background = '';
            progFill.style.backgroundSize = '';
        }
    }

    // ── Status ────────────────────────────────────────────────────────────────
    function showStatus(msg, type) {
        const el = document.getElementById('status');
        el.innerHTML = msg;
        el.className = type || '';
    }

    // ── Log modal ─────────────────────────────────────────────────────────────
    function openLogModal()  { document.getElementById('logModal').classList.add('show'); refreshLog(); }
    function closeLogModal() { document.getElementById('logModal').classList.remove('show'); }
    function refreshLog() {
        fetch('/get_log').then(r => r.json()).then(data => {
            const b = document.getElementById('logBody');
            b.innerHTML = '<pre>' + (data.log || 'No logs yet.') + '</pre>';
            b.scrollTop = b.scrollHeight;
        }).catch(() => {
            document.getElementById('logBody').innerHTML = '<pre>Error loading log.</pre>';
        });
    }
    function clearLog() {
        if (confirm('Clear all logs?'))
            fetch('/clear_log', { method: 'POST' }).then(() => refreshLog());
    }
    document.getElementById('logModal').addEventListener('click', e => {
        if (e.target.id === 'logModal') closeLogModal();
    });
    </script>
</body>
</html>
"""

@app.route('/')
def home():
    folder_escaped = DOWNLOAD_FOLDER.replace('\\', '\\\\')
    return render_template_string(
        HTML,
        folder=folder_escaped,
        folder_json=json.dumps(DOWNLOAD_FOLDER)
    )

@app.route('/preview', methods=['POST'])
def preview():
    try:
        url = request.json.get('url')
        if not url:
            return jsonify({'error': 'No URL provided'})

        log_message(f"Preview request for: {url}")

        cmd = [
            resource_path('yt-dlp.exe'),
            '--dump-json',
            '--no-download',
            '--no-playlist',
            '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            '--add-header', 'Accept:*/*',
            '--add-header', 'Accept-Language:en-US,en;q=0.9',
            '--no-check-certificate',
            url
        ]

        result = subprocess.run(
            cmd, capture_output=True, text=True, check=False,
            creationflags=subprocess.CREATE_NO_WINDOW
        )

        if result.returncode != 0:
            log_message(f"Preview failed: {result.stderr[:500]}")
            return jsonify({'error': 'Could not fetch video info. Check the URL or try a different link.'})

        # yt-dlp may output multiple JSON lines for playlists; take the first
        stdout = result.stdout.strip()
        first_line = next((l for l in stdout.splitlines() if l.strip().startswith('{')), None)
        if not first_line:
            return jsonify({'error': 'No video metadata returned.'})

        info = json.loads(first_line)

        video_formats, audio_formats = parse_formats_from_info(info)

        thumbnail = (
            info.get('thumbnail') or
            (info.get('thumbnails', [{}])[-1].get('url') if info.get('thumbnails') else None)
        )

        duration_str = info.get('duration_string', '')
        uploader = info.get('uploader') or info.get('channel') or ''

        log_message(
            f"Preview OK: '{info.get('title','?')}' | "
            f"{len(video_formats)} video formats, {len(audio_formats)} audio formats"
        )

        return jsonify({
            'title':         info.get('title', ''),
            'duration':      duration_str,
            'uploader':      uploader,
            'thumbnail':     thumbnail,
            'video_formats': [
                {'format_id': f['format_id'], 'label': f['label'], 'height': f['height'],
                 'ext': f['ext'], 'vcodec': f['vcodec'], 'acodec': f['acodec']}
                for f in video_formats
            ],
            'audio_formats': [
                {'format_id': f['format_id'], 'label': f['label'], 'ext': f['ext']}
                for f in audio_formats
            ],
        })

    except Exception as e:
        log_message(f"Preview exception: {str(e)}")
        return jsonify({'error': f'Preview error: {str(e)}'})


@app.route('/choose_folder', methods=['POST'])
def choose_folder():
    global DOWNLOAD_FOLDER
    try:
        new_folder = diropenbox(
            msg="Select folder to save videos",
            title="Choose Save Location",
            default=DOWNLOAD_FOLDER
        )
        if new_folder:
            DOWNLOAD_FOLDER = new_folder
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                f.write(new_folder)
            log_message(f"Save folder changed to: {new_folder}")
            return jsonify({'path': new_folder})
        return jsonify({'path': None})
    except Exception as e:
        log_message(f"Folder selection error: {str(e)}")
        return jsonify({'path': None, 'message': str(e)})


@app.route('/download', methods=['POST'])
def download():
    global DOWNLOAD_FOLDER
    data = request.json
    url     = data.get('url', '').strip()
    fmt     = data.get('format', 'mp4')
    quality = data.get('quality', 'bestvideo+bestaudio/best')
    mode    = data.get('mode', 'standard')

    if not url:
        return jsonify({'error': 'No URL provided'})

    with download_lock:
        download_progress.update({
            "mode": mode, "status": "starting", "stage": "starting",
            "percent": 0, "downloaded": "—", "total": "—",
            "speed": "—", "eta": "—", "error": ""
        })

    log_message(f"Download: url={url}")
    log_message(f"  format={fmt}, quality={quality}, mode={mode}")

    try:
        # Resolve video title for the output filename
        info_result = subprocess.run(
            [resource_path('yt-dlp.exe'),
             '--dump-json', '--no-download', '--no-playlist',
             '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
             '--no-check-certificate', url],
            capture_output=True, text=True, check=False,
            creationflags=subprocess.CREATE_NO_WINDOW
        )

        if info_result.returncode == 0:
            try:
                first = next(
                    (l for l in info_result.stdout.splitlines() if l.strip().startswith('{')),
                    '{}'
                )
                info = json.loads(first)
                title = sanitize_filename(info.get('title', 'video'))
            except Exception:
                title = 'video'
        else:
            title = 'video'

        log_message(f"Title: {title}")

        # ── Advanced (m3u8) mode ──────────────────────────────────────────────
        if mode == 'advanced':
            output_path = os.path.join(DOWNLOAD_FOLDER, f'{title}.mp4')
            success, error_msg = download_m3u8_advanced(url, output_path, quality)
            if success:
                with download_lock:
                    download_progress["status"] = "completed"
                    download_progress["percent"] = 100
                return jsonify({'status': 'ok', 'folder': DOWNLOAD_FOLDER})
            else:
                with download_lock:
                    download_progress["status"] = "error"
                return jsonify({'error': error_msg, 'folder': DOWNLOAD_FOLDER})

        # ── Standard yt-dlp mode ──────────────────────────────────────────────
        output_template = os.path.join(DOWNLOAD_FOLDER, '%(title)s.%(ext)s')

        cmd = [
            resource_path('yt-dlp.exe'),
            '--no-playlist',
            '--newline',
            '-o', output_template,
            '--ffmpeg-location', resource_path('ffmpeg.exe'),
            '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            '--add-header', 'Accept:*/*',
            '--add-header', 'Accept-Language:en-US,en;q=0.9',
            '--no-check-certificate',
        ]

        if fmt == 'mp3':
            cmd += ['--extract-audio', '--audio-format', 'mp3', '--audio-quality', '0', url]
            log_message("Format string: mp3 audio extraction")

        elif fmt == 'mp4':
            fstr = _build_format_string(quality, 'mp4')
            cmd += ['-f', fstr, '--merge-output-format', 'mp4', url]
            log_message(f"Format string: {fstr}")

        else:  # webm
            fstr = _build_format_string(quality, 'webm')
            cmd += ['-f', fstr, '--merge-output-format', 'webm', url]
            log_message(f"Format string: {fstr}")

        log_message(f"ffmpeg path: {resource_path('ffmpeg.exe')}")
        log_message(f"yt-dlp cmd: {' '.join(cmd[:12])}…")

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            bufsize=1, universal_newlines=True
        )

        with download_lock:
            download_progress["status"] = "downloading"
            download_progress["stage"] = "video"

        # Track which stream we're on so we can map to a unified 0-100% bar.
        # Phases: video dl (0–75%), audio dl (75–92%), merging (92–100%)
        # If only one stream (muxed / audio-only), it maps 0–100% directly.
        stream_index   = 0   # 0 = video/only, 1 = audio
        last_dest_line = ''

        for line in iter(process.stdout.readline, ''):
            line = line.strip()
            if line:
                log_message(f"  {line}")

            # Detect when yt-dlp switches to downloading the second stream
            if '[download] Destination:' in line:
                if last_dest_line:          # second Destination = audio stream
                    stream_index = 1
                    with download_lock:
                        download_progress["stage"]     = "audio"
                        download_progress["speed"]     = "—"
                        download_progress["eta"]       = "—"
                        download_progress["downloaded"] = "—"
                        download_progress["total"]     = "—"
                last_dest_line = line

            # Merging / ffmpeg encode phase
            if '[Merger]' in line or 'Merging formats' in line or 'ffmpeg' in line.lower() and 'merging' in line.lower():
                with download_lock:
                    download_progress["stage"]   = "merging"
                    download_progress["percent"] = 93
                    download_progress["speed"]   = "—"
                    download_progress["eta"]     = "—"

            if '[download]' in line and '%' in line:
                pct  = re.search(r'(\d+\.?\d*)%', line)
                size = re.search(r'(\d+\.?\d*\s*[KMGTiB]+)\s+of\s+~?\s*(\d+\.?\d*\s*[KMGTiB]+)', line)
                spd  = re.search(r'at\s+(\S+/s)', line)
                eta  = re.search(r'ETA\s+(\d{2}:\d{2})', line)

                if pct:
                    raw = float(pct.group(1))
                    # Map raw 0-100 into the unified bar range for each phase
                    if stream_index == 0:
                        unified = int(raw * 0.75)          # video:  0 → 75
                    else:
                        unified = int(75 + raw * 0.17)     # audio: 75 → 92

                    with download_lock:
                        download_progress["percent"] = unified
                        if size:
                            download_progress["downloaded"] = size.group(1).strip()
                            download_progress["total"]      = size.group(2).strip()
                        if spd:  download_progress["speed"] = spd.group(1)
                        if eta:  download_progress["eta"]   = eta.group(1)

        process.wait()

        if process.returncode == 0:
            log_message("Download completed successfully.")
            with download_lock:
                download_progress["status"] = "completed"
                download_progress["percent"] = 100
            return jsonify({'status': 'ok', 'folder': DOWNLOAD_FOLDER})
        else:
            log_message(f"yt-dlp exited with code {process.returncode}")
            with download_lock:
                download_progress["status"] = "error"
            return jsonify({
                'error': 'Download failed. Check the log for details. Try enabling Advanced mode for streaming sites.',
                'folder': DOWNLOAD_FOLDER
            })

    except Exception as e:
        log_message(f"Download exception: {str(e)}")
        with download_lock:
            download_progress["status"] = "error"
            download_progress["error"] = str(e)
        return jsonify({'error': str(e), 'folder': DOWNLOAD_FOLDER})


def _build_format_string(quality: str, container: str) -> str:
    """
    Build a yt-dlp -f format string from the quality value sent by the UI.

    quality values from the frontend:
      - "best"            — Best Available (sentinel)
      - "id:{fid}:{h}"   — specific yt-dlp format_id with its height (from fetched list)
      - "h:{pixels}"     — height-based filter (from fallback defaults, e.g. "h:720")
    """
    if not quality or quality in ('best', 'bestvideo+bestaudio/best'):
        return 'bestvideo+bestaudio/best'

    # Specific format_id selected from fetched formats: "id:136:720"
    if quality.startswith('id:'):
        parts = quality.split(':')
        fmt_id = parts[1] if len(parts) > 1 else 'best'
        height = parts[2] if len(parts) > 2 else None
        # Request the exact video format + best audio; fall back to height filter
        if height:
            return (
                f'{fmt_id}+bestaudio'
                f'/bestvideo[height<={height}]+bestaudio'
                f'/best[height<={height}]'
            )
        return f'{fmt_id}+bestaudio/bestvideo+bestaudio/best'

    # Height-based filter from fallback defaults: "h:720"
    if quality.startswith('h:'):
        h = quality[2:]
        if container == 'mp4':
            return (
                f'bestvideo[height<={h}][vcodec^=avc]+bestaudio[ext=m4a]'
                f'/bestvideo[height<={h}]+bestaudio'
                f'/best[height<={h}]'
            )
        else:
            return (
                f'bestvideo[height<={h}]+bestaudio'
                f'/best[height<={h}]'
            )

    # Legacy fallback: plain digits passed directly (shouldn't happen anymore)
    if quality.isdigit():
        h = quality
        return f'bestvideo[height<={h}]+bestaudio/best[height<={h}]'

    # Anything else: treat as a raw format string and pass through
    return quality


@app.route('/progress', methods=['GET'])
def progress():
    with download_lock:
        return jsonify(download_progress)

@app.route('/get_log', methods=['GET'])
def get_log():
    try:
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, 'r', encoding='utf-8') as f:
                content = f.read()
            return jsonify({'log': content})
        return jsonify({'log': ''})
    except Exception as e:
        return jsonify({'log': f'Error reading log: {str(e)}'})

@app.route('/clear_log', methods=['POST'])
def clear_log():
    try:
        with open(LOG_FILE, 'w', encoding='utf-8') as f:
            f.write('')
        log_message("Log cleared.")
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'error': str(e)})

def start_flask():
    app.run(port=5000, debug=False, use_reloader=False, threaded=True)

if __name__ == '__main__':
    log_message("=== Application Started ===")
    threading.Thread(target=start_flask, daemon=True).start()
    webview.create_window(
        "Any Video Downloader",
        "http://127.0.0.1:5000",
        width=500,
        height=800,
        resizable=True,
        fullscreen=False,
        text_select=True,
        easy_drag=True
    )
    webview.start()
