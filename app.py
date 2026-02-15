import webview
import threading
from flask import Flask, request, render_template_string, jsonify
import subprocess
import os
import sys
import json
from easygui import diropenbox

app = Flask(__name__)

# Helper for PyInstaller bundled files
def resource_path(relative_path):
    """Get absolute path to resource, works for dev and for PyInstaller"""
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

CONFIG_FILE = 'save_path.txt'

def load_folder():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            path = f.read().strip()
            if os.path.isdir(path):
                return path
    return os.getcwd()

DOWNLOAD_FOLDER = load_folder()

HTML = """
<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Video Downloader</title>
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
            font-weight:500;
        }
        input, select {
            width:100%; padding:8px 80px 8px 12px;
            border-radius:6px;
            border:1px solid #334155; background:#0f172a; color:white;
            font-size:0.95rem;
        }
        input:focus, select:focus {
            outline:none; border-color:var(--accent); box-shadow:0 0 0 2px rgba(59,130,246,0.3);
        }
        input[type="text"] {
            overflow:hidden;
            text-overflow:ellipsis;
            white-space:nowrap;
        }
        .clear-url-row {
            display:flex; justify-content:flex-end; margin-top:6px;
        }
        .clear-btn {
            background:#334155;
            color:#cbd5e1;
            border:none;
            border-radius:4px;
            padding:4px 12px;
            font-size:0.8rem;
            font-weight:500;
            cursor:pointer;
            display:none;
        }
        .clear-btn.visible {
            display:inline-block;
        }
        .clear-btn:hover {
            background:#475569;
            color:white;
        }
        button {
            width:100%; padding:10px; margin:8px 0;
            border:none; border-radius:6px; cursor:pointer;
            font-weight:600; font-size:0.95rem; transition:0.2s;
        }
        #choose-folder { background:#334155; color:white; }
        #choose-folder:hover { background:#475569; }
        #download-btn {
            background:var(--accent); color:white; font-size:1.1rem; padding:12px;
        }
        #download-btn:hover:not(:disabled) { background:var(--accent-hover); }
        #download-btn:disabled { background:#475569; cursor:not-allowed; }
        #status {
            margin-top:10px; padding:8px; border-radius:6px;
            text-align:center; font-size:0.9rem; font-weight:500;
            min-height:40px; display:flex; align-items:center; justify-content:center;
        }
        .success { background:#14532d; color:#86efac; }
        .error   { background:#7f1d1d; color:#fca5a5; }
        .loading { background:#1e40af; color:#bfdbfe; }
        #folder-path {
            font-size:0.8rem; color:#94a3b8; margin-top:4px;
            word-break:break-all; line-height:1.3;
        }
        #preview {
            margin:8px 0; background:#0f172a; padding:8px;
            border-radius:6px; border:1px solid #334155; display:none;
            flex-direction:row; gap:10px; align-items:center;
        }
        #preview.show { display:flex; }
        #preview-thumb {
            width:100px; height:56px; object-fit:cover; border-radius:4px;
            background:#1e293b; flex-shrink:0;
        }
        #preview-info { flex:1; min-width:0; }
        #preview-title {
            font-weight:600; font-size:0.95rem; line-height:1.3;
            overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
        }
        #preview-duration { color:#94a3b8; font-size:0.85rem; }
        .spinner {
            width:16px; height:16px; border:3px solid #bfdbfe;
            border-top:3px solid transparent; border-radius:50%;
            animation:spin 1s linear infinite; display:inline-block;
        }
        @keyframes spin { to { transform:rotate(360deg); } }
    </style>
</head>
<body>
    <h1>Video Downloader</h1>

    <label>Paste Video Link</label>
    <input type="text" id="url" placeholder="YouTube / TikTok / Instagram..." oninput="toggleClearBtn()" onblur="fetchPreview()">

    <div class="clear-url-row">
        <button class="clear-btn" id="clearBtn" onclick="clearUrl()">Clear</button>
    </div>

    <div id="preview">
        <img id="preview-thumb" src="" alt="" />
        <div id="preview-info">
            <div id="preview-title"></div>
            <div id="preview-duration"></div>
        </div>
    </div>

    <label>Format</label>
    <select id="format">
        <option value="mp4">MP4 Video</option>
        <option value="webm">WebM Video</option>
        <option value="mp3">MP3 Audio</option>
    </select>

    <div id="quality-group">
        <label>Quality (video only)</label>
        <select id="quality">
            <option value="1080">1080p</option>
            <option value="720">720p</option>
            <option value="480">480p</option>
            <option value="best">Best</option>
        </select>
    </div>

    <label>Save Location</label>
    <button id="choose-folder" onclick="chooseFolder()">Choose Folder</button>
    <div id="folder-path">Current: {{ folder | safe }}</div>

    <button id="download-btn" onclick="startDownload()">Download</button>

    <div id="status">Ready – paste link & choose options</div>

    <script>
        let currentFolder = "{{ folder | safe }}";
        let isDownloading = false;

        function toggleClearBtn() {
            const urlInput = document.getElementById('url');
            const clearBtn = document.getElementById('clearBtn');
            if (urlInput.value.trim().length > 0) {
                clearBtn.classList.add('visible');
            } else {
                clearBtn.classList.remove('visible');
            }
        }

        function chooseFolder() {
            fetch('/choose_folder', { method: 'POST' })
                .then(r => r.json())
                .then(data => {
                    if (data.path) {
                        currentFolder = data.path;
                        document.getElementById('folder-path').textContent = 'Current: ' + data.path;
                        showStatus('Folder updated', 'success');
                    } else {
                        showStatus(data.message || 'No folder selected', 'error');
                    }
                })
                .catch(() => showStatus('Error contacting server', 'error'));
        }

        window.addEventListener('load', () => {
            const f = localStorage.getItem('yt_format') || 'mp4';
            const q = localStorage.getItem('yt_quality') || '1080';
            document.getElementById('format').value = f;
            document.getElementById('quality').value = q;
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
            document.getElementById('quality-group').style.display =
                document.getElementById('format').value === 'mp3' ? 'none' : 'block';
        }

        function clearUrl() {
            document.getElementById('url').value = '';
            document.getElementById('preview').classList.remove('show');
            document.getElementById('preview-thumb').src = '';
            toggleClearBtn();
        }

        function fetchPreview() {
            const url = document.getElementById('url').value.trim();
            if (!url) {
                document.getElementById('preview').classList.remove('show');
                toggleClearBtn();
                return;
            }

            showStatus('Fetching preview...', 'loading');

            fetch('/preview', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({url})
            })
            .then(r => r.json())
            .then(data => {
                if (data.error) {
                    showStatus('Preview failed: ' + data.error, 'error');
                    document.getElementById('preview').classList.remove('show');
                } else if (data.title) {
                    document.getElementById('preview-title').textContent = data.title;
                    document.getElementById('preview-duration').textContent = data.duration || '';
                    const thumb = document.getElementById('preview-thumb');
                    thumb.src = data.thumbnail || '';
                    thumb.style.display = data.thumbnail ? 'block' : 'none';
                    document.getElementById('preview').classList.add('show');
                    showStatus('Preview ready', 'success');
                } else {
                    showStatus('No preview data', 'error');
                    document.getElementById('preview').classList.remove('show');
                }
            })
            .catch(err => {
                showStatus('Preview connection error', 'error');
                document.getElementById('preview').classList.remove('show');
            });
        }

        function startDownload() {
            if (isDownloading) return;
            const url = document.getElementById('url').value.trim();
            if (!url) return showStatus('Enter link first', 'error');

            isDownloading = true;
            const btn = document.getElementById('download-btn');
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner"></span> Downloading...';

            showStatus('Downloading... Please wait', 'loading');

            fetch('/download', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    url,
                    format: document.getElementById('format').value,
                    quality: document.getElementById('quality').value
                })
            })
            .then(r => r.json())
            .then(data => {
                if (data.error) showStatus('Error: ' + data.error, 'error');
                else showStatus('Success! Saved to: ' + data.folder, 'success');
            })
            .catch(() => showStatus('Connection error', 'error'))
            .finally(() => {
                isDownloading = false;
                btn.disabled = false;
                btn.innerHTML = 'Download';
            });
        }

        function showStatus(msg, type) {
            const el = document.getElementById('status');
            el.innerHTML = msg;
            el.className = type;
        }
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

        result = subprocess.run(
            [resource_path('yt-dlp.exe'), '--dump-json', '--no-download', '--no-playlist',
             '--referer', 'https://www.tiktok.com/',
             '--referer', 'https://www.instagram.com/', url],
            capture_output=True,
            text=True,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW
        )

        if result.returncode != 0:
            return jsonify({'error': f'yt-dlp failed (code {result.returncode}): {result.stderr.strip() or "Check link"}'})

        info = json.loads(result.stdout.strip() or '{}')

        thumbnail = info.get('thumbnail') or (info.get('thumbnails', [{}])[0].get('url') if info.get('thumbnails') else None)

        return jsonify({
            'title': info.get('title', 'No title found'),
            'duration': f"Duration: {info.get('duration_string', '?')}" if info.get('duration_string') else 'No duration',
            'thumbnail': thumbnail
        })
    except Exception as e:
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
            return jsonify({'path': new_folder})
        return jsonify({'path': None, 'message': 'No folder selected'})
    except Exception as e:
        return jsonify({'path': None, 'message': str(e)})

@app.route('/download', methods=['POST'])
def download():
    global DOWNLOAD_FOLDER
    data = request.json
    url = data.get('url')
    fmt = data.get('format', 'mp4')
    qual = data.get('quality', 'best')

    if not url:
        return jsonify({'error': 'No URL provided'})

    cmd = [
        resource_path('yt-dlp.exe'),
        '--referer', 'https://www.tiktok.com/',
        '--referer', 'https://www.instagram.com/',
        '--no-playlist',
        '--no-warnings',
        '--hls-use-mpegts',
        '--no-part',
        '--concurrent-fragments', '10',  # Helps with m3u8 segment downloading
        '--merge-output-format', 'mp4'
    ]

    if fmt == 'mp3':
        cmd += ['--extract-audio', '--audio-format', 'mp3', '--audio-quality', '192K', url]
    elif fmt == 'mp4':
        fstr = 'bestvideo+bestaudio/best' if qual == 'best' else f'bestvideo[height<={qual}]+bestaudio/best'
        cmd += ['-f', fstr, url]
    else:
        fstr = 'bestvideo+bestaudio/best' if qual == 'best' else f'bestvideo[height<={qual}]+bestaudio/best'
        cmd += ['-f', fstr, '--merge-output-format', 'webm', url]

    try:
        subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            cwd=DOWNLOAD_FOLDER,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        return jsonify({'status': 'ok', 'folder': DOWNLOAD_FOLDER})
    except subprocess.CalledProcessError as e:
        return jsonify({'error': e.stderr.decode('utf-8', errors='ignore').strip() or 'Download failed'})
    except Exception as e:
        return jsonify({'error': str(e)})

def start_flask():
    app.run(port=5000, debug=False, use_reloader=False, threaded=True)

if __name__ == '__main__':
    threading.Thread(target=start_flask, daemon=True).start()
    webview.create_window(
        "Video Downloader",
        "http://127.0.0.1:5000",
        width=480,
        height=620,
        resizable=True,
        fullscreen=False,
        text_select=True,
        easy_drag=True
    )
    webview.start()