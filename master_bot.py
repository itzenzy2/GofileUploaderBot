import logging
import os
import asyncio
import time
import shutil
import requests
import math
import fnmatch
import hashlib
import urllib.parse
import json
from urllib.parse import urlparse
from asyncio import CancelledError
from telethon import TelegramClient, events
from telethon.tl.types import DocumentAttributeVideo, DocumentAttributeFilename
from pathvalidate import sanitize_filename

# --- Your Credentials ---
# Load credentials from environment variables for security
API_ID = os.environ.get('API_ID')
API_HASH = os.environ.get('API_HASH')
GOFILE_TOKEN = os.environ.get('GOFILE_TOKEN')

# --- Configuration ---
DOWNLOAD_DIR = "downloads"
USER_TASKS = {}

# --- Setup Logging ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)


# =====================================================================================
# HELPER CLASSES (Faithful implementation from gofile-dl)
# =====================================================================================

class GoFileFile:
    def __init__(self, link: str, dest: str, size: int, name: str):
        self.link, self.dest, self.size, self.name = link, dest, size, name

class GoFileDownloader:
    def __init__(self, token): 
        self.token = token
    def download(self, file: GoFileFile, progress_callback=None):
        link, dest, total_size = file.link, file.dest, file.size
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        downloaded_bytes = os.path.getsize(dest) if os.path.exists(dest) else 0
        if downloaded_bytes >= total_size:
            if progress_callback: progress_callback(total_size, total_size)
            return True
        
        try:
            headers = {"Cookie": f"accountToken={self.token}", "Range": f"bytes={downloaded_bytes}-"}
            with requests.get(link, headers=headers, stream=True, timeout=30) as r:
                r.raise_for_status()
                with open(dest, "ab") as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                            downloaded_bytes += len(chunk)
                            if progress_callback: progress_callback(downloaded_bytes, total_size)
            return True
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to download {file.name}: {e}")
            if os.path.exists(dest):
                os.remove(dest)
            return False

class GoFile:
    def __init__(self):
        self.token = ""
        self.wt = ""

    def _update_session(self):
        if not self.token:
            try:
                data = requests.post("https://api.gofile.io/accounts").json()
                if data["status"] == "ok": self.token = data["data"]["token"]
                else: raise Exception("Could not get guest token")
            except Exception as e: logger.error(f"Failed to update token: {e}"); raise
        if not self.wt:
            try:
                alljs = requests.get("https://gofile.io/dist/js/global.js").text
                if 'appdata.wt = "' in alljs: self.wt = alljs.split('appdata.wt = "')[1].split('"')[0]
                else: raise Exception("Could not find wt in global.js")
            except Exception as e: logger.error(f"Failed to update wt: {e}"); raise

    def get_folder_contents(self, content_id: str, output_dir: str, password: str = None) -> list[GoFileFile]:
        self._update_session()
        hash_password = hashlib.sha256(password.encode()).hexdigest() if password else ""
        api_url = f"https://api.gofile.io/contents/{content_id}?wt={self.wt}&cache=true&password={hash_password}"
        headers = {"Authorization": "Bearer " + self.token}
        response = requests.get(api_url, headers=headers)
        response.raise_for_status()
        data = response.json()
        if data["status"] != "ok": raise Exception(f"GoFile API Error: {data.get('status')}")
        if data["data"].get("passwordStatus", "passwordOk") != "passwordOk": raise Exception("Invalid password")
        files_list = []
        def recurse_children(children, current_path):
            for child_id, child_info in children.items():
                if child_info["type"] == "folder":
                    new_path = os.path.join(current_path, sanitize_filename(child_info["name"]))
                    files_list.extend(self.get_folder_contents(child_id, new_path, password))
                elif child_info["type"] == "file":
                    filename = sanitize_filename(child_info["name"])
                    files_list.append(GoFileFile(link=urllib.parse.unquote(child_info["link"]), dest=os.path.join(current_path, filename), size=child_info["size"], name=filename))
        root_data = data["data"]
        if root_data["type"] == "folder":
            folder_name = sanitize_filename(root_data["name"])
            new_output_dir = os.path.join(output_dir, folder_name)
            recurse_children(root_data.get("children", {}), new_output_dir)
        else:
             filename = sanitize_filename(root_data["name"])
             files_list.append(GoFileFile(link=urllib.parse.unquote(root_data["link"]), dest=os.path.join(output_dir, filename), size=root_data["size"], name=filename))
        return files_list
    
    def refresh_folder_links(self, content_id: str, files_list: list[GoFileFile], password: str = None):
        self._update_session()
        hash_password = hashlib.sha256(password.encode()).hexdigest() if password else ""
        api_url = f"https://api.gofile.io/contents/{content_id}?wt={self.wt}&cache=true&password={hash_password}"
        headers = {"Authorization": "Bearer " + self.token}
        response = requests.get(api_url, headers=headers)
        response.raise_for_status()
        data = response.json()
        if data["status"] != "ok": raise Exception(f"GoFile API Error: {data.get('status')}")
        if data["data"].get("passwordStatus", "passwordOk") != "passwordOk": raise Exception("Invalid password")
        link_mapping = {}
        def map_children(children):
            for child_id, child_info in children.items():
                if child_info["type"] == "file":
                    filename = sanitize_filename(child_info["name"])
                    link_mapping[filename] = urllib.parse.unquote(child_info["link"])
        root_data = data["data"]
        if root_data["type"] == "folder":
            map_children(root_data.get("children", {}))
        else:
            filename = sanitize_filename(root_data["name"])
            link_mapping[filename] = urllib.parse.unquote(root_data["link"])
        for file_obj in files_list:
            if file_obj.name in link_mapping:
                file_obj.link = link_mapping[file_obj.name]

# =====================================================================================
# TELEGRAM BOT LOGIC
# =====================================================================================

client = TelegramClient('master_bot_session', API_ID, API_HASH)

def format_bytes(size: float) -> str:
    if size == 0: return "0B"
    power = 1024; n = 0
    power_labels = {0: 'B', 1: 'KB', 2: 'MB', 3: 'GB', 4: 'TB'}
    while size >= power and n < len(power_labels) - 1:
        size /= power; n += 1
    return f"{size:.2f} {power_labels[n]}"

def generate_progress_message(action: str, filename: str, progress: float, transferred: int, total: int, speed: float) -> str:
    bar_length = 10
    filled_length = int(bar_length * progress // 100)
    bar = '█' * filled_length + '░' * (bar_length - filled_length)
    return (f"**{action}**\n**File:** `{filename}`\n`{bar}`\n`{format_bytes(transferred)} / {format_bytes(total)}`\n`Speed: {format_bytes(speed)}/s`")

async def generate_thumbnail(video_path: str) -> str | None:
    thumb_path = video_path + ".jpg"
    try:
        process = await asyncio.create_subprocess_exec('ffmpeg', '-i', video_path, '-ss', '00:00:01.000', '-vframes', '1', '-y', thumb_path, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        _, stderr = await process.communicate()
        if process.returncode != 0:
            logger.error(f"ffmpeg thumb failed: {stderr.decode()}"); return None
        return thumb_path
    except FileNotFoundError: return None
    except Exception as e: logger.error(f"Thumb generation failed: {e}"); return None

async def get_video_attributes(video_path: str):
    try:
        process = await asyncio.create_subprocess_exec('ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_streams', video_path, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, _ = await process.communicate()
        if process.returncode != 0: return []
        data = json.loads(stdout)
        video_stream = next((s for s in data['streams'] if s['codec_type'] == 'video'), None)
        if video_stream:
            return [DocumentAttributeVideo(duration=int(float(video_stream.get('duration', 0))), w=int(video_stream.get('width', 0)), h=int(video_stream.get('height', 0)), supports_streaming=True)]
    except Exception as e: logger.error(f"Failed to get video attributes: {e}")
    return []

@client.on(events.NewMessage(pattern='/start'))
async def start(event):
    await event.respond(
        "**GoFile Master Bot**\n\n"
        "I can both upload files to GoFile and download folders from it.\n\n"
        "➡️ **To Upload:** Send me a file or a direct download link.\n"
        "➡️ **To Download:** Send me a GoFile folder link.\n"
        "➡️ **To Stop:** Use the /stop command to cancel the current operation."
    )

@client.on(events.NewMessage(pattern='/stop'))
async def stop_handler(event):
    if (task := USER_TASKS.get(event.sender_id)): task.cancel()
    else: await event.respond("You have no active process to stop.")

@client.on(events.NewMessage)
async def message_handler(event):
    if not event.is_private or event.text.startswith('/'): return
    if event.sender_id in USER_TASKS:
        await event.respond("You already have a process running. Please wait or use /stop."); return
    task = None
    if event.message.text:
        url = event.message.text
        if 'gofile.io/d/' in url or 'gofile.io/c/' in url: task = asyncio.create_task(process_gofile_folder(event, url))
        elif url.startswith(('http://', 'https://')): task = asyncio.create_task(upload_from_link(event, url))
    elif event.message.file: task = asyncio.create_task(handle_file_upload(event))
    if task:
        USER_TASKS[event.sender_id] = task
        task.add_done_callback(lambda t: USER_TASKS.pop(event.sender_id, None))

async def process_gofile_folder(event, url):
    status_message = await event.respond(f"✅ Link received. Inspecting folder...")
    temp_download_path = os.path.join(DOWNLOAD_DIR, f"gofile_{event.message.id}")
    os.makedirs(temp_download_path, exist_ok=True)
    try:
        loop = asyncio.get_event_loop()
        gofile_engine = GoFile()
        content_id = os.path.basename(urlparse(url).path)
        
        files_to_process = await loop.run_in_executor(None, gofile_engine.get_folder_contents, content_id, temp_download_path)
        if not files_to_process: raise Exception("No files found in the folder.")
        
        await status_message.edit(f"✅ Found {len(files_to_process)} file(s). Starting download process...")
        
        downloader = GoFileDownloader(token=gofile_engine.token)
        successful_downloads = 0
        failed_downloads = 0
        
        for i, file_obj in enumerate(files_to_process):
            if i > 0:
                await status_message.edit(f"🔄 Refreshing download links...")
                await loop.run_in_executor(None, gofile_engine.refresh_folder_links, content_id, files_to_process)
                await asyncio.sleep(1)
            
            last_update_time = time.time(); start_time = time.time()
            def progress_callback(downloaded, total):
                nonlocal last_update_time, start_time
                current_time = time.time()
                if current_time - last_update_time > 1.5:
                    elapsed = current_time - start_time; speed = downloaded / elapsed if elapsed > 0 else 0
                    percentage = (downloaded / total) * 100
                    text = generate_progress_message("Downloading to Server", file_obj.name, percentage, downloaded, total, speed)
                    asyncio.run_coroutine_threadsafe(status_message.edit(text), loop)
                    last_update_time = current_time
            
            download_success = await loop.run_in_executor(None, downloader.download, file_obj, progress_callback)
            
            if download_success:
                await upload_file_to_telegram(event, file_obj.dest, status_message)
                os.remove(file_obj.dest)
                successful_downloads += 1
            else:
                await status_message.edit(f"⚠️ Skipping '{file_obj.name}' - link may be broken.")
                await asyncio.sleep(2)
                failed_downloads += 1
            
        await status_message.delete()
        
        if successful_downloads > 0:
            summary_msg = f"✅ Process completed!\n\n📥 **Successfully sent:** {successful_downloads} file(s)"
            if failed_downloads > 0:
                summary_msg += f"\n⚠️ **Skipped (broken links):** {failed_downloads} file(s)"
            await event.respond(summary_msg)
        else:
            await event.respond("❌ No files could be downloaded. All links appear to be broken.")
            
    except CancelledError: await status_message.edit("🛑 Process cancelled by user.")
    except Exception as e: await status_message.edit(f"❌ An error occurred: {e}")
    finally:
        if os.path.exists(temp_download_path): shutil.rmtree(temp_download_path)

async def upload_file_to_telegram(event, filepath, status_message):
    if not os.path.exists(filepath) or os.path.getsize(filepath) == 0: return
    filename = os.path.basename(filepath)
    file_extension = os.path.splitext(filename)[1].lower()
    video_extensions = ['.mp4', '.mkv', '.avi', '.mov']
    is_video = file_extension in video_extensions
    
    thumb_path = None
    attributes = [DocumentAttributeFilename(filename)]

    try:
        if is_video:
            thumb_path = await generate_thumbnail(filepath)
            video_meta_attrs = await get_video_attributes(filepath)
            attributes.extend(video_meta_attrs)

        await status_message.edit(f"Uploading `{filename}`...")
        last_update_time = time.time(); start_time = time.time()
        async def progress_callback(uploaded, total):
            nonlocal last_update_time, start_time
            current_time = time.time()
            if current_time - last_update_time > 1.5:
                elapsed = current_time - start_time; speed = uploaded / elapsed if elapsed > 0 else 0
                percentage = (uploaded / total) * 100
                text = generate_progress_message("Uploading to You", filename, percentage, uploaded, total, speed)
                try: await status_message.edit(text)
                except Exception: pass
                last_update_time = current_time
        
        await event.client.send_file(
            event.chat_id,
            file=filepath,
            thumb=thumb_path,
            progress_callback=progress_callback,
            force_document=False,
            attributes=attributes
        )
    finally:
        if thumb_path and os.path.exists(thumb_path): os.remove(thumb_path)

async def handle_file_upload(event):
    status_message = None; filepath = None
    try:
        message = event.message
        if message.file.name: filename = message.file.name
        else:
            ext = message.file.mime_type.split('/')[-1] if message.file.mime_type else 'dat'
            filename = f"telegram_file_{message.id}.{ext}"
        status_message = await event.respond(f"📄 Received '{filename}'. Preparing to download...")
        filepath = os.path.join(DOWNLOAD_DIR, filename)
        if not os.path.exists(DOWNLOAD_DIR): os.makedirs(DOWNLOAD_DIR)
        await download_from_telegram(message, filepath, status_message)
        if not os.path.exists(filepath) or os.path.getsize(filepath) == 0: raise FileNotFoundError("File empty after download.")
        await status_message.edit("✅ TG download complete. Preparing to upload to GoFile...")
        await upload_to_gofile(filepath, filename, status_message)
    except CancelledError: await status_message.edit("🛑 Process cancelled by user.")
    except Exception as e:
        if status_message: await status_message.edit(f"❌ An error occurred: {e}")
    finally:
        if filepath and os.path.exists(filepath): os.remove(filepath)

async def download_from_telegram(message, filepath, status_message):
    last_update_time = time.time(); start_time = time.time()
    async def progress_callback(downloaded, total):
        nonlocal last_update_time, start_time
        current_time = time.time()
        if current_time - last_update_time > 1.5:
            elapsed = current_time - start_time; speed = downloaded / elapsed if elapsed > 0 else 0
            percentage = (downloaded / total) * 100
            text = generate_progress_message("Downloading from Telegram", os.path.basename(filepath), percentage, downloaded, total, speed)
            await status_message.edit(text)
            last_update_time = current_time
    await message.download_media(file=filepath, progress_callback=progress_callback)

async def upload_from_link(event, url):
    status_message = await event.respond(f"🔗 Received link. Preparing to download...")
    filepath = None
    try:
        if not os.path.exists(DOWNLOAD_DIR): os.makedirs(DOWNLOAD_DIR)
        filename = url.split('/')[-1].split('?')[0] or "downloaded_file"
        filepath = os.path.join(DOWNLOAD_DIR, filename)
        with requests.get(url, stream=True) as r:
            r.raise_for_status()
            with open(filepath, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192): f.write(chunk)
        if not os.path.exists(filepath) or os.path.getsize(filepath) == 0: raise FileNotFoundError("File empty after download.")
        await status_message.edit("✅ Download complete. Preparing to upload to GoFile...")
        await upload_to_gofile(filepath, filename, status_message)
    except CancelledError: await status_message.edit("🛑 Process cancelled by user.")
    except Exception as e:
        if status_message: await status_message.edit(f"❌ An error occurred: {e}")
    finally:
        if filepath and os.path.exists(filepath): os.remove(filepath)

async def upload_to_gofile(filepath, filename, status_message):
    server_response = requests.get("https://api.gofile.io/servers")
    server_response.raise_for_status()
    server = server_response.json()["data"]["servers"][0]["name"]
    upload_url = f"https://{server}.gofile.io/uploadFile"
    with open(filepath, 'rb') as f:
        files = {'file': (filename, f)}
        headers = {"Authorization": f"Bearer {GOFILE_TOKEN}"}
        response = requests.post(upload_url, headers=headers, files=files)
    response.raise_for_status()
    upload_result = response.json()
    if upload_result.get("status") != "ok": raise Exception(f"GoFile API error: {upload_result.get('data', {})}")
    download_page = upload_result.get("data", {}).get("downloadPage")
    await status_message.edit(f"🎉 **Upload successful!**\n\n{download_page}")

async def main():
    if not all([API_ID, API_HASH, GOFILE_TOKEN]):
        logger.critical("FATAL: One or more environment variables (API_ID, API_HASH, GOFILE_TOKEN) are not set.")
        return
    await client.start()
    logger.info("Master GoFile Bot has started successfully.")
    await client.run_until_disconnected()

if __name__ == '__main__':
    client.loop.run_until_complete(main())
