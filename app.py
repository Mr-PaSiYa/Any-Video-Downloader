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
    "filename": "",
    "error": "",
    "mode": "unknown"
}
download_lock = threading.Lock()

# Helper for PyInstaller bundled files
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
    """Write log message to file"""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(f"[{timestamp}] {message}\n")

def sanitize_filename(filename):
    """Clean filename for safe saving"""
    filename = re.sub(r'[<>:"/\\|?*]', '', filename)
    filename = filename.strip()
    return filename[:200] if len(filename) > 200 else filename

def download_m3u8_advanced(url, output_path, quality='best'):
    """
    Advanced m3u8 downloader that:
    1. Gets the m3u8 playlist URL with fresh tokens
    2. Downloads all segments
    3. Combines them with ffmpeg
    """
    log_message("=== ADVANCED M3U8 DOWNLOAD MODE ===")
    
    try:
        # Step 1: Get the m3u8 playlist URL using yt-dlp
        log_message("Step 1: Getting m3u8 playlist URL...")
        
        format_arg = 'best' if quality == 'best' or not quality.isdigit() else f'bestvideo[height<={quality}]+bestaudio/best'
        
        # Enhanced command with cookies and headers
        cmd = [
            resource_path('yt-dlp.exe'),
            '--get-url',
            '-f', format_arg,
            '--no-playlist',
            '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            '--add-header', 'Accept:*/*',
            '--add-header', 'Accept-Language:en-US,en;q=0.9',
            '--cookies-from-browser', 'chrome',  # Try to use browser cookies
            '--no-check-certificate',
            url
        ]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        
        if result.returncode != 0:
            log_message(f"ERROR: Could not get m3u8 URL: {result.stderr}")
            # Try without browser cookies if that failed
            log_message("Retrying without browser cookies...")
            cmd_no_cookies = [
                resource_path('yt-dlp.exe'),
                '--get-url',
                '-f', format_arg,
                '--no-playlist',
                '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                '--no-check-certificate',
                url
            ]
            result = subprocess.run(cmd_no_cookies, capture_output=True, text=True, check=False, creationflags=subprocess.CREATE_NO_WINDOW)
            
            if result.returncode != 0:
                return False, "Failed to get video URL. Site may require login or use Advanced mode."
        
        m3u8_url = result.stdout.strip()
        log_message(f"m3u8 URL obtained: {m3u8_url[:100]}...")
        
        # Step 2: Use FFmpeg with special options for HLS
        log_message("Step 2: Downloading with FFmpeg in advanced mode...")
        
        with download_lock:
            download_progress["status"] = "downloading"
            download_progress["percent"] = 0
        
        # Detect site for custom headers
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
        
        log_message(f"FFmpeg command: {' '.join(ffmpeg_cmd[:6])}...")
        
        process = subprocess.Popen(
            ffmpeg_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            bufsize=1,
            universal_newlines=True
        )
        
        total_duration = None
        error_404_count = 0
        
        for line in iter(process.stdout.readline, ''):
            line = line.strip()
            if line:
                log_message(f"FFmpeg: {line}")
            
            # Check for 404 errors
            if '404' in line or 'Not Found' in line:
                error_404_count += 1
                if error_404_count >= 3:
                    log_message("CRITICAL: Multiple 404 errors - aborting")
                    process.kill()
                    return False, "Segments expired (404 errors)"
            
            # Parse duration
            if 'Duration:' in line and total_duration is None:
                dur_match = re.search(r'Duration: (\d{2}):(\d{2}):(\d{2})\.\d+', line)
                if dur_match:
                    h, m, s = map(int, dur_match.groups())
                    total_duration = h * 3600 + m * 60 + s
                    log_message(f"Total duration: {total_duration}s")
            
            # Parse progress
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
            log_message("SUCCESS: Video downloaded successfully!")
            return True, "Success"
        else:
            log_message(f"ERROR: FFmpeg failed with code {process.returncode}")
            return False, f"FFmpeg failed (code {process.returncode})"
            
    except Exception as e:
        log_message(f"EXCEPTION in advanced download: {str(e)}")
        return False, str(e)

HTML = """
<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Any Video Downloader</title>
    <style>
        :root {
            --bg: #0f172a;
            --card: #1e293b;
            --text: #e2e8f0;
            --accent: #3b82f6;
            --accent-hover: #2563eb;
        }
        * { margin:0; padding:0; box-sizing:border-box; }
        body {
            background:var(--bg); color:var(--text);
            font-family:system-ui,sans-serif;
            height:100vh; overflow:hidden;
            display:flex; flex-direction:column;
            padding:12px;
        }
        h1 {
            color:var(--accent); font-size:1.4rem; text-align:center;
            margin-bottom:12px; font-weight:600;
        }
        label {
            font-size:0.85rem; color:#94a3b8; margin:10px 0 4px;
            font-weight:500; display:block;
        }
        .input-wrapper {
            position: relative;
            width: 100%;
        }
        input, select {
            width:100%; padding:8px 12px;
            border-radius:6px;
            border:1px solid #334155; background:#0f172a; color:white;
            font-size:0.95rem;
        }
        input[type="text"] {
            padding-right: 40px;
        }
        input:focus, select:focus {
            outline:none; border-color:var(--accent); box-shadow:0 0 0 2px rgba(59,130,246,0.3);
        }
        .clear-btn {
            position: absolute;
            right: 8px;
            top: 50%;
            transform: translateY(-50%);
            background: transparent;
            border: none;
            color: #94a3b8;
            cursor: pointer;
            padding: 4px 8px;
            font-size: 1.1rem;
            line-height: 1;
            opacity: 0;
            pointer-events: none;
            transition: opacity 0.2s;
        }
        .clear-btn.visible {
            opacity: 1;
            pointer-events: auto;
        }
        .clear-btn:hover {
            color: #e2e8f0;
        }
        button {
            width:100%; padding:10px; margin:8px 0;
            border:none; border-radius:6px; cursor:pointer;
            font-weight:600; font-size:0.95rem; transition:0.2s;
        }
        #choose-folder { background:#334155; color:white; margin-top: 4px; }
        #choose-folder:hover { background:#475569; }
        #download-btn {
            background:var(--accent); color:white; font-size:1.1rem; padding:12px;
            margin-top: 12px;
        }
        #download-btn:hover:not(:disabled) { background:var(--accent-hover); }
        #download-btn:disabled { background:#475569; cursor:not-allowed; opacity: 0.6; }
        #status {
            margin-top:10px; padding:8px; border-radius:6px;
            text-align:center; font-size:0.9rem; font-weight:500;
            min-height:40px; display:flex; align-items:center; justify-content:center;
        }
        .success { background:#14532d; color:#86efac; }
        .error   { background:#7f1d1d; color:#fca5a5; }
        .loading { background:#1e40af; color:#bfdbfe; }
        #folder-path {
            font-size:0.75rem; color:#64748b; 
            word-break:break-all; line-height:1.3;
            padding: 4px 0;
        }
        #preview {
            margin:8px 0; background:#0f172a; padding:10px;
            border-radius:6px; border:1px solid #334155; display:none;
            flex-direction:row; gap:12px; align-items:center;
        }
        #preview.show { display:flex; }
        #preview-thumb {
            width:80px; height:45px; object-fit:cover; border-radius:4px;
            background:#1e293b; flex-shrink:0;
        }
        #preview-info { flex:1; min-width:0; }
        #preview-title {
            font-weight:600; font-size:0.9rem; line-height:1.3;
            overflow:hidden; text-overflow:ellipsis; 
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            margin-bottom: 4px;
        }
        #preview-duration { 
            color:#94a3b8; font-size:0.8rem; 
        }
        .spinner {
            width:16px; height:16px; border:3px solid #bfdbfe;
            border-top:3px solid transparent; border-radius:50%;
            animation:spin 1s linear infinite; display:inline-block;
            margin-right: 8px;
        }
        @keyframes spin { to { transform:rotate(360deg); } }
        .options-row {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 12px;
            margin-top: 4px;
        }
        .option-group {
            display: flex;
            flex-direction: column;
        }
        #quality-group {
            transition: opacity 0.2s;
        }
        #quality-group.hidden {
            opacity: 0.4;
            pointer-events: none;
        }
        
        /* Downloader toggle styles */
        .toggle-wrapper {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 8px 12px;
            background: #1e293b;
            border-radius: 6px;
            margin: 8px 0;
        }
        .toggle-label {
            font-size: 0.85rem;
            color: #94a3b8;
            font-weight: 500;
        }
        .toggle-switch {
            position: relative;
            width: 52px;
            height: 28px;
            background: #334155;
            border-radius: 14px;
            cursor: pointer;
            transition: background 0.3s;
        }
        .toggle-switch.active {
            background: var(--accent);
        }
        .toggle-slider {
            position: absolute;
            top: 3px;
            left: 3px;
            width: 22px;
            height: 22px;
            background: white;
            border-radius: 50%;
            transition: transform 0.3s;
        }
        .toggle-switch.active .toggle-slider {
            transform: translateX(24px);
        }
        .downloader-mode {
            font-size: 0.75rem;
            color: #64748b;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        
        /* Progress bar */
        #progress-container {
            margin-top: 12px;
            display: none;
            flex-direction: column;
            gap: 8px;
        }
        #progress-bar {
            height: 12px;
            background: #334155;
            border-radius: 6px;
            overflow: hidden;
        }
        #progress-fill {
            height: 100%;
            background: var(--accent);
            width: 0%;
            transition: width 0.3s;
        }
        #progress-text {
            font-size: 0.8rem;
            text-align: center;
            color: #94a3b8;
        }
        
        /* Log button and modal */
        .log-btn-wrapper {
            position: fixed;
            bottom: 12px;
            right: 12px;
            z-index: 100;
        }
        .log-btn {
            width: 44px;
            height: 44px;
            border-radius: 50%;
            background: #334155;
            color: white;
            border: none;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 1.2rem;
            box-shadow: 0 4px 6px rgba(0,0,0,0.3);
            transition: all 0.2s;
        }
        .log-btn:hover {
            background: #475569;
            transform: translateY(-2px);
            box-shadow: 0 6px 8px rgba(0,0,0,0.4);
        }
        
        .log-modal {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0,0,0,0.7);
            z-index: 200;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }
        .log-modal.show {
            display: flex;
        }
        .log-content {
            background: var(--card);
            border-radius: 8px;
            width: 100%;
            max-width: 600px;
            max-height: 80vh;
            display: flex;
            flex-direction: column;
            overflow: hidden;
        }
        .log-header {
            padding: 16px;
            border-bottom: 1px solid #334155;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .log-header h2 {
            font-size: 1.1rem;
            color: var(--accent);
            margin: 0;
        }
        .log-close {
            background: transparent;
            border: none;
            color: #94a3b8;
            font-size: 1.5rem;
            cursor: pointer;
            padding: 0;
            width: 32px;
            height: 32px;
            display: flex;
            align-items: center;
            justify-content: center;
            border-radius: 4px;
            transition: all 0.2s;
        }
        .log-close:hover {
            background: #334155;
            color: white;
        }
        .log-body {
            flex: 1;
            overflow-y: auto;
            padding: 16px;
            font-family: 'Courier New', monospace;
            font-size: 0.8rem;
            line-height: 1.5;
            color: #cbd5e1;
            background: #0f172a;
        }
        .log-body pre {
            margin: 0;
            white-space: pre-wrap;
            word-wrap: break-word;
        }
        .log-footer {
            padding: 12px 16px;
            border-top: 1px solid #334155;
            display: flex;
            gap: 8px;
        }
        .log-footer button {
            flex: 1;
            margin: 0;
            padding: 8px;
            font-size: 0.85rem;
        }
        .log-clear {
            background: #7f1d1d;
            color: #fca5a5;
        }
        .log-clear:hover {
            background: #991b1b;
        }
        .log-refresh {
            background: var(--accent);
        }
        .log-refresh:hover {
            background: var(--accent-hover);
        }
        
        .info-hint {
            font-size: 0.75rem;
            color: #64748b;
            margin-top: 2px;
            font-style: italic;
        }
    </style>
</head>
<body>
    <h1>Any Video Downloader</h1>

    <label>Video Link</label>
    <div class="input-wrapper">
        <input type="text" id="url" placeholder="Paste YouTube, TikTok, Instagram,..." autocomplete="off">
        <button class="clear-btn" id="clearBtn" onclick="clearUrl()">×</button>
    </div>

    <div id="preview">
        <img id="preview-thumb" src="" alt="" />
        <div id="preview-info">
            <div id="preview-title"></div>
            <div id="preview-duration"></div>
        </div>
    </div>

    <div class="options-row">
        <div class="option-group">
            <label>Format</label>
            <select id="format">
                <option value="mp4">MP4 Video</option>
                <option value="webm">WebM Video</option>
                <option value="mp3">MP3 Audio</option>
            </select>
        </div>
        <div class="option-group" id="quality-group">
            <label>Quality</label>
            <select id="quality">
                <option value="best">Best Available</option>
            </select>
        </div>
    </div>

    <label>Download Mode <span class="downloader-mode" id="downloader-mode">Standard</span></label>
    <div class="toggle-wrapper">
        <span class="toggle-label">Advanced M3U8 mode (for streaming sites)</span>
        <div class="toggle-switch" id="downloaderToggle" onclick="toggleDownloader()">
            <div class="toggle-slider"></div>
        </div>
    </div>
    <div class="info-hint">💡 Use Advanced mode for sites with expiring tokens</div>

    <label>Save Location</label>
    <div id="folder-path">{{ folder | safe }}</div>
    <button id="choose-folder" onclick="chooseFolder()">Change Folder</button>

    <button id="download-btn" onclick="startDownload()">Download</button>

    <div id="progress-container">
        <div id="progress-bar"><div id="progress-fill"></div></div>
        <div id="progress-text">Waiting...</div>
    </div>

    <div id="status">Ready</div>

    <!-- Log Button -->
    <div class="log-btn-wrapper">
        <button class="log-btn" onclick="openLogModal()" title="View Logs">📋</button>
    </div>

    <!-- Log Modal -->
    <div class="log-modal" id="logModal">
        <div class="log-content">
            <div class="log-header">
                <h2>Download Log</h2>
                <button class="log-close" onclick="closeLogModal()">×</button>
            </div>
            <div class="log-body" id="logBody">
                <pre>Loading...</pre>
            </div>
            <div class="log-footer">
                <button class="log-clear" onclick="clearLog()">Clear Log</button>
                <button class="log-refresh" onclick="refreshLog()">Refresh</button>
            </div>
        </div>
    </div>

    <script>
        let currentFolder = "{{ folder | safe }}";
        let isDownloading = false;
        let useAdvanced = false;
        let progressInterval = null;
        let availableFormats = [];

        const urlInput = document.getElementById('url');
        const clearBtn = document.getElementById('clearBtn');

        urlInput.addEventListener('input', toggleClearBtn);
        urlInput.addEventListener('paste', () => {
            setTimeout(() => {
                toggleClearBtn();
                fetchPreview();
            }, 100);
        });

        function toggleClearBtn() {
            if (urlInput.value.trim().length > 0) {
                clearBtn.classList.add('visible');
            } else {
                clearBtn.classList.remove('visible');
            }
        }

        function clearUrl() {
            urlInput.value = '';
            document.getElementById('preview').classList.remove('show');
            document.getElementById('preview-thumb').src = '';
            toggleClearBtn();
            urlInput.focus();
        }

        function toggleDownloader() {
            useAdvanced = !useAdvanced;
            const toggle = document.getElementById('downloaderToggle');
            const modeText = document.getElementById('downloader-mode');
            
            if (useAdvanced) {
                toggle.classList.add('active');
                modeText.textContent = 'Advanced';
                localStorage.setItem('use_advanced', 'true');
            } else {
                toggle.classList.remove('active');
                modeText.textContent = 'Standard';
                localStorage.setItem('use_advanced', 'false');
            }
        }

        function chooseFolder() {
            fetch('/choose_folder', { method: 'POST' })
                .then(r => r.json())
                .then(data => {
                    if (data.path) {
                        currentFolder = data.path;
                        document.getElementById('folder-path').textContent = data.path;
                        showStatus('Folder updated', 'success');
                    }
                })
                .catch(() => showStatus('Error selecting folder', 'error'));
        }

        window.addEventListener('load', () => {
            const f = localStorage.getItem('yt_format') || 'mp4';
            const advanced = localStorage.getItem('use_advanced') === 'true';
            
            document.getElementById('format').value = f;
            
            if (advanced) {
                useAdvanced = true;
                document.getElementById('downloaderToggle').classList.add('active');
                document.getElementById('downloader-mode').textContent = 'Advanced';
            }
            
            updateQualityVisibility();
            toggleClearBtn();
        });

        document.getElementById('format').addEventListener('change', () => {
            localStorage.setItem('yt_format', document.getElementById('format').value);
            updateQualityVisibility();
        });

        document.getElementById('quality').addEventListener('change', () => {
            localStorage.setItem('yt_quality', document.getElementById('quality').value);
        });

        function updateQualityVisibility() {
            const qualityGroup = document.getElementById('quality-group');
            if (document.getElementById('format').value === 'mp3') {
                qualityGroup.classList.add('hidden');
            } else {
                qualityGroup.classList.remove('hidden');
            }
        }

        function fetchPreview() {
            const url = urlInput.value.trim();
            if (!url) {
                document.getElementById('preview').classList.remove('show');
                return;
            }

            const previewDiv = document.getElementById('preview');
            previewDiv.classList.remove('show');
            showStatus('Fetching preview...', 'loading');

            fetch('/preview', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({url})
            })
            .then(r => r.json())
            .then(data => {
                if (data.title) {
                    document.getElementById('preview-title').textContent = data.title;
                    document.getElementById('preview-duration').textContent = data.duration || '';
                    const thumb = document.getElementById('preview-thumb');
                    if (data.thumbnail) {
                        thumb.src = data.thumbnail;
                        thumb.style.display = 'block';
                    } else {
                        thumb.style.display = 'none';
                    }
                    previewDiv.classList.add('show');

                    // Populate quality dropdown with fetched formats
                    availableFormats = data.formats || [];
                    const qualitySelect = document.getElementById('quality');
                    qualitySelect.innerHTML = '';
                    
                    if (availableFormats.length > 0) {
                        availableFormats.forEach(f => {
                            const opt = document.createElement('option');
                            opt.value = f.format_id;
                            opt.textContent = f.resolution || f.note || f.format_id;
                            qualitySelect.appendChild(opt);
                        });
                        const savedQuality = localStorage.getItem('yt_quality');
                        if (savedQuality && Array.from(qualitySelect.options).some(o => o.value === savedQuality)) {
                            qualitySelect.value = savedQuality;
                        }
                    } else {
                        qualitySelect.innerHTML = `
                            <option value="1080">1080p</option>
                            <option value="720">720p</option>
                            <option value="480">480p</option>
                            <option value="best">Best</option>
                        `;
                    }
                    
                    showStatus('Ready to download', 'success');
                }
            })
            .catch(() => {});
        }

        function startDownload() {
            if (isDownloading) return;
            const url = urlInput.value.trim();
            if (!url) {
                showStatus('Please paste a video link', 'error');
                return;
            }

            isDownloading = true;
            const btn = document.getElementById('download-btn');
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner"></span>Downloading...';

            document.getElementById('progress-container').style.display = 'flex';
            document.getElementById('progress-fill').style.width = '0%';
            document.getElementById('progress-text').textContent = 'Initializing...';

            showStatus('Downloading...', 'loading');

            const mode = useAdvanced ? 'advanced' : 'standard';

            fetch('/download', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    url,
                    format: document.getElementById('format').value,
                    quality: document.getElementById('quality').value,
                    mode: mode
                })
            })
            .then(r => r.json())
            .then(data => {
                if (data.error) {
                    showStatus('Error: ' + data.error, 'error');
                } else {
                    showStatus('Downloaded successfully!', 'success');
                    setTimeout(() => clearUrl(), 2000);
                }
            })
            .catch(() => showStatus('Connection error', 'error'))
            .finally(() => {
                isDownloading = false;
                btn.disabled = false;
                btn.innerHTML = 'Download';
                clearInterval(progressInterval);
                setTimeout(() => {
                    document.getElementById('progress-container').style.display = 'none';
                }, 4000);
            });

            // Start progress polling
            progressInterval = setInterval(() => {
                fetch('/progress')
                    .then(r => r.json())
                    .then(data => {
                        document.getElementById('progress-fill').style.width = data.percent + '%';
                        document.getElementById('progress-text').textContent =
                            `${data.downloaded} / ${data.total} • ${data.speed} • ETA: ${data.eta}`;
                        if (data.status === 'completed' || data.status === 'failed' || data.status === 'error') {
                            clearInterval(progressInterval);
                        }
                    })
                    .catch(() => {});
            }, 800);
        }

        function showStatus(msg, type) {
            const el = document.getElementById('status');
            el.innerHTML = msg;
            el.className = type;
        }

        // Log Modal Functions
        function openLogModal() {
            document.getElementById('logModal').classList.add('show');
            refreshLog();
        }

        function closeLogModal() {
            document.getElementById('logModal').classList.remove('show');
        }

        function refreshLog() {
            fetch('/get_log')
                .then(r => r.json())
                .then(data => {
                    const logBody = document.getElementById('logBody');
                    if (data.log) {
                        logBody.innerHTML = '<pre>' + data.log + '</pre>';
                        logBody.scrollTop = logBody.scrollHeight;
                    } else {
                        logBody.innerHTML = '<pre>No logs yet</pre>';
                    }
                })
                .catch(() => {
                    document.getElementById('logBody').innerHTML = '<pre>Error loading log</pre>';
                });
        }

        function clearLog() {
            if (confirm('Clear all logs?')) {
                fetch('/clear_log', { method: 'POST' })
                    .then(() => refreshLog())
                    .catch(() => alert('Error clearing log'));
            }
        }

        // Close modal on outside click
        document.getElementById('logModal').addEventListener('click', (e) => {
            if (e.target.id === 'logModal') {
                closeLogModal();
            }
        });
    </script>
</body>
</html>
"""

@app.route('/')
def home():
    return render_template_string(HTML, folder=DOWNLOAD_FOLDER.replace('\\', '\\\\'))

@app.route('/preview', methods=['POST'])
def preview():
    try:
        url = request.json.get('url')
        if not url:
            return jsonify({'error': 'No URL provided'})

        log_message(f"Preview request for: {url}")

        # Enhanced command with better site support
        cmd = [
            resource_path('yt-dlp.exe'),
            '--list-formats',
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
            cmd,
            capture_output=True,
            text=True,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW
        )

        if result.returncode != 0:
            log_message(f"Preview failed: {result.stderr}")
            return jsonify({'error': f'Failed to fetch formats: {result.stderr.strip()}'})

        lines = result.stdout.splitlines()
        info_line = next((line for line in lines if line.strip().startswith('{')), None)

        if not info_line:
            log_message("No metadata found in preview")
            return jsonify({'error': 'No metadata found'})

        info = json.loads(info_line)

        # Parse available formats from --list-formats output
        formats = []
        for line in lines:
            if re.match(r'^\d+\s', line):
                parts = line.split()
                if len(parts) >= 3:
                    format_id = parts[0]
                    resolution = parts[2] if len(parts) > 2 else 'audio'
                    note = ' '.join(parts[3:]) if len(parts) > 3 else ''
                    formats.append({'format_id': format_id, 'resolution': resolution, 'note': note})

        thumbnail = info.get('thumbnail') or (info.get('thumbnails', [{}])[0].get('url') if info.get('thumbnails') else None)

        log_message(f"Preview success: {info.get('title', 'Unknown')} with {len(formats)} formats")

        return jsonify({
            'title': info.get('title', 'No title found'),
            'duration': f"Duration: {info.get('duration_string', '?')}" if info.get('duration_string') else '',
            'thumbnail': thumbnail,
            'formats': formats
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
    url = data.get('url')
    fmt = data.get('format', 'mp4')
    quality = data.get('quality', 'best')
    mode = data.get('mode', 'standard')  # 'standard' or 'advanced'

    if not url:
        return jsonify({'error': 'No URL provided', 'folder': DOWNLOAD_FOLDER})

    with download_lock:
        download_progress["mode"] = mode
        download_progress["status"] = "starting"
        download_progress["percent"] = 0
        download_progress["downloaded"] = "0B"
        download_progress["total"] = "Unknown"
        download_progress["speed"] = "0B/s"
        download_progress["eta"] = "Unknown"
        download_progress["error"] = ""

    log_message(f"Download started: {url}")
    log_message(f"Format: {fmt}, Quality: {quality}, Mode: {mode}")

    try:
        # Get video info for proper filename
        info_result = subprocess.run(
            [resource_path('yt-dlp.exe'),
             '--dump-json',
             '--no-download',
             '--no-playlist',
             '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
             '--no-check-certificate',
             url],
            capture_output=True,
            text=True,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        
        if info_result.returncode == 0:
            info = json.loads(info_result.stdout.strip() or '{}')
            title = sanitize_filename(info.get('title', 'video'))
            log_message(f"Video title: {title}")
        else:
            title = 'video'
            log_message("Could not fetch video title, using default")

        output_path = os.path.join(DOWNLOAD_FOLDER, f'{title}.mp4')

        # Use advanced mode for m3u8 streams
        if mode == 'advanced':
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
        
        # Standard mode - use yt-dlp
        else:
            output_template = os.path.join(DOWNLOAD_FOLDER, f'{title}.%(ext)s')

            cmd = [
                resource_path('yt-dlp.exe'),
                '--no-playlist',
                '--no-warnings',
                '--newline',
                '-o', output_template,
                '--ffmpeg-location', resource_path('ffmpeg.exe'),
                '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                '--add-header', 'Accept:*/*',
                '--add-header', 'Accept-Language:en-US,en;q=0.9',
                '--no-check-certificate',
            ]

            if fmt == 'mp3':
                cmd += [
                    '--extract-audio', 
                    '--audio-format', 'mp3', 
                    '--audio-quality', '192K',
                    url
                ]
            elif fmt == 'mp4':
                if quality == 'best' or not quality.isdigit():
                    fstr = quality if quality in ['best', 'bestvideo+bestaudio'] else 'bestvideo+bestaudio/best'
                else:
                    fstr = f'bestvideo[height<={quality}]+bestaudio/best'
                cmd += [
                    '-f', fstr,
                    '--merge-output-format', 'mp4',
                    url
                ]
            else:  # webm
                if quality == 'best' or not quality.isdigit():
                    fstr = 'bestvideo+bestaudio/best'
                else:
                    fstr = f'bestvideo[height<={quality}]+bestaudio/best'
                cmd += [
                    '-f', fstr,
                    '--merge-output-format', 'webm',
                    url
                ]

            log_message(f"Command: {' '.join(cmd[:8])}...")

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
                bufsize=1,
                universal_newlines=True
            )

            with download_lock:
                download_progress["status"] = "downloading"

            for line in iter(process.stdout.readline, ''):
                line = line.strip()
                if line:
                    log_message(f"Output: {line}")
                
                # Parse progress from yt-dlp output
                if '[download]' in line and '%' in line:
                    percent_match = re.search(r'(\d+\.?\d*)%', line)
                    size_match = re.search(r'(\d+\.?\d*[KMG]iB)\s+of\s+[~]?\s*(\d+\.?\d*[KMG]iB)', line)
                    speed_match = re.search(r'at\s+(\d+\.?\d*[KMG]iB/s)', line)
                    eta_match = re.search(r'ETA\s+(\d{2}:\d{2})', line)
                    
                    with download_lock:
                        if percent_match:
                            download_progress["percent"] = int(float(percent_match.group(1)))
                        if size_match:
                            download_progress["downloaded"] = size_match.group(1)
                            download_progress["total"] = size_match.group(2)
                        if speed_match:
                            download_progress["speed"] = speed_match.group(1)
                        if eta_match:
                            download_progress["eta"] = eta_match.group(1)

            process.wait()

            if process.returncode == 0:
                log_message("Download completed successfully using yt-dlp")
                with download_lock:
                    download_progress["status"] = "completed"
                    download_progress["percent"] = 100
                return jsonify({'status': 'ok', 'folder': DOWNLOAD_FOLDER})
            else:
                log_message(f"yt-dlp failed with code {process.returncode}. Try Advanced mode for m3u8 streams.")
                with download_lock:
                    download_progress["status"] = "error"
                return jsonify({'error': 'Download failed. Try turning ON Advanced mode', 'folder': DOWNLOAD_FOLDER})

    except Exception as e:
        error_msg = str(e)
        log_message(f"Download exception: {error_msg}")
        with download_lock:
            download_progress["status"] = "error"
            download_progress["error"] = error_msg
        return jsonify({'error': error_msg, 'folder': DOWNLOAD_FOLDER})

@app.route('/progress', methods=['GET'])
def progress():
    with download_lock:
        return jsonify(download_progress)

@app.route('/get_log', methods=['GET'])
def get_log():
    try:
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, 'r', encoding='utf-8') as f:
                log_content = f.read()
            return jsonify({'log': log_content})
        return jsonify({'log': ''})
    except Exception as e:
        return jsonify({'log': f'Error reading log: {str(e)}'})

@app.route('/clear_log', methods=['POST'])
def clear_log():
    try:
        with open(LOG_FILE, 'w', encoding='utf-8') as f:
            f.write('')
        log_message("Log cleared by user")
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
        width=480,
        height=780,
        resizable=True,
        fullscreen=False,
        text_select=True,
        easy_drag=True
    )
    webview.start()
