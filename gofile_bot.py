import logging
import os
import requests
import time
import asyncio
from telethon import TelegramClient, events

# --- Configuration ---
# Load credentials from environment variables for security
API_ID = os.environ.get('API_ID')
API_HASH = os.environ.get('API_HASH')
GOFILE_TOKEN = os.environ.get('GOFILE_TOKEN')

DOWNLOAD_DIR = "downloads"

# --- Setup Logging ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Helper Functions ---

def format_bytes(size: float) -> str:
    if size == 0: return "0B"
    power = 1024
    n = 0
    power_labels = {0: '', 1: 'K', 2: 'M', 3: 'G', 4: 'T'}
    while size > power and n < len(power_labels) - 1:
        size /= power
        n += 1
    return f"{size:.2f} {power_labels[n]}B"

def generate_progress_message(action: str, filename: str, progress: float, transferred: int, total: int, speed: float) -> str:
    bar_length = 10
    filled_length = int(bar_length * progress // 100)
    bar = '█' * filled_length + '░' * (bar_length - filled_length)
    return (
        f"**{action}**\n"
        f"**File:** `{filename}`\n"
        f"`{bar}`\n"
        f"`{format_bytes(transferred)} / {format_bytes(total)}`\n"
        f"`Speed: {format_bytes(speed)}/s`"
    )

# --- Main Application ---

client = TelegramClient('bot_session', API_ID, API_HASH)

@client.on(events.NewMessage(pattern='/start'))
async def start(event):
    await event.respond(
        "**GoFile.io Uploader**\n\n"
        "I can upload files to GoFile.io for you.\n\n"
        "➡️ **To Upload:** Send me a file or a direct download link."
    )

@client.on(events.NewMessage(func=lambda e: e.message.text and e.message.text.startswith(('http://', 'https://'))))
async def handle_link(event):
    """Handles all incoming links."""
    url = event.message.text
    if 'gofile.io' in url:
        await event.respond("I can only upload *to* GoFile.io, not download from it. Please send me a direct download link or a file.")
    else:
        await upload_from_link(event, url)

@client.on(events.NewMessage(func=lambda e: e.message.file))
async def handle_file_upload(event):
    """Handles a file sent to the bot for uploading."""
    message = event.message
    
    if message.file.name:
        filename = message.file.name
    else:
        ext = message.file.mime_type.split('/')[-1] if message.file.mime_type else 'dat'
        filename = f"telegram_file_{message.id}.{ext}"
    
    status_message = await event.respond(f"📄 Received '{filename}'. Preparing to download from Telegram...")
    filepath = os.path.join(DOWNLOAD_DIR, filename)
    if not os.path.exists(DOWNLOAD_DIR):
        os.makedirs(DOWNLOAD_DIR)

    try:
        await download_from_telegram(message, filepath, status_message)
        
        if not os.path.exists(filepath) or os.path.getsize(filepath) == 0:
            raise FileNotFoundError("File was not created on disk or is empty after Telegram download.")

        await status_message.edit("✅ TG download complete. Preparing to upload to GoFile...")
        await upload_to_gofile(filepath, filename, status_message)
    except Exception as e:
        logger.error(f"An error occurred with file upload: {e}")
        await status_message.edit(f"❌ An error occurred: {e}")
    finally:
        if os.path.exists(filepath):
            os.remove(filepath)

# --- Download from Source Logic ---

async def download_from_telegram(message, filepath, status_message):
    """Downloads a file from Telegram to the VPS with progress."""
    last_update_time = time.time()
    start_time = time.time()
    async def progress_callback(downloaded, total):
        nonlocal last_update_time, start_time
        current_time = time.time()
        if current_time - last_update_time > 1.5:
            elapsed = current_time - start_time
            speed = downloaded / elapsed if elapsed > 0 else 0
            percentage = (downloaded / total) * 100
            text = generate_progress_message("Downloading from Telegram", os.path.basename(filepath), percentage, downloaded, total, speed)
            await status_message.edit(text)
            last_update_time = current_time
    await message.download_media(file=filepath, progress_callback=progress_callback)

async def download_file_from_url(status_message, url, filepath, filename):
    """Downloads a file from a direct URL to the VPS with progress."""
    loop = asyncio.get_event_loop()
    def download_blocking():
        with requests.get(url, stream=True) as r:
            r.raise_for_status()
            total_size = int(r.headers.get('content-length', 0))
            downloaded_size = 0
            last_update_time = time.time()
            start_time = time.time()
            with open(filepath, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
                    downloaded_size += len(chunk)
                    current_time = time.time()
                    if total_size > 0 and current_time - last_update_time > 1.5:
                        elapsed = current_time - start_time
                        speed = downloaded_size / elapsed if elapsed > 0 else 0
                        percentage = (downloaded_size / total_size) * 100
                        text = generate_progress_message("Downloading to Server", filename, percentage, downloaded_size, total_size, speed)
                        asyncio.run_coroutine_threadsafe(status_message.edit(text), loop)
                        last_update_time = current_time
    await loop.run_in_executor(None, download_blocking)

# --- Upload Logic ---

async def upload_from_link(event, url):
    """Handles the full process for a direct link."""
    status_message = await event.respond(f"🔗 Received link. Preparing to download...")
    filepath = None
    try:
        if not os.path.exists(DOWNLOAD_DIR):
            os.makedirs(DOWNLOAD_DIR)
        
        filename = url.split('/')[-1].split('?')[0] or "downloaded_file"
        filepath = os.path.join(DOWNLOAD_DIR, filename)
        
        await download_file_from_url(status_message, url, filepath, filename)
        
        if not os.path.exists(filepath) or os.path.getsize(filepath) == 0:
            raise FileNotFoundError("File was not created on disk or is empty after download from URL.")

        await status_message.edit("✅ Download complete. Preparing to upload to GoFile...")
        await upload_to_gofile(filepath, filename, status_message)

    except Exception as e:
        logger.error(f"An error occurred with link upload: {e}")
        await status_message.edit(f"❌ An error occurred: {e}")
        if filepath and os.path.exists(filepath):
            os.remove(filepath)

async def upload_to_gofile(filepath, filename, status_message):
    """Uploads a local file from the VPS to GoFile.io."""
    try:
        loop = asyncio.get_event_loop()
        last_update_time = time.time()
        start_time = time.time()

        def progress_callback(monitor):
            nonlocal last_update_time, start_time
            current_time = time.time()
            if current_time - last_update_time > 1.5:
                elapsed = current_time - start_time
                speed = monitor.bytes_read / elapsed if elapsed > 0 else 0
                percentage = (monitor.bytes_read / monitor.len) * 100
                text = generate_progress_message("Uploading to GoFile.io", filename, percentage, monitor.bytes_read, monitor.len, speed)
                asyncio.run_coroutine_threadsafe(status_message.edit(text), loop)
                last_update_time = current_time

        def upload_blocking():
            from requests_toolbelt.multipart.encoder import MultipartEncoder, MultipartEncoderMonitor
            server_response = requests.get("https://api.gofile.io/servers")
            server_response.raise_for_status()
            server = server_response.json()["data"]["servers"][0]["name"]
            upload_url = f"https://{server}.gofile.io/uploadFile"
            with open(filepath, 'rb') as f:
                encoder = MultipartEncoder(fields={'file': (filename, f, 'application/octet-stream')})
                monitor = MultipartEncoderMonitor(encoder, progress_callback)
                headers = {"Authorization": f"Bearer {GOFILE_TOKEN}", "Content-Type": monitor.content_type}
                response = requests.post(upload_url, data=monitor, headers=headers)
            response.raise_for_status()
            return response.json()

        upload_result = await loop.run_in_executor(None, upload_blocking)

        if upload_result.get("status") != "ok":
            raise Exception(f"GoFile API error: {upload_result.get('data', {})}")

        download_page = upload_result.get("data", {}).get("downloadPage")
        await status_message.edit(f"🎉 **Upload successful!**\n\n{download_page}")

    finally:
        if os.path.exists(filepath):
            os.remove(filepath)
            logger.info(f"Cleaned up file: {filepath}")

# --- Main Loop ---
async def main():
    """Checks for credentials and starts the client."""
    if not all([API_ID, API_HASH, GOFILE_TOKEN]):
        logger.critical("FATAL: One or more environment variables (API_ID, API_HASH, GOFILE_TOKEN) are not set.")
        return
    
    await client.start()
    logger.info("Client has started successfully.")
    await client.run_until_disconnected()

if __name__ == '__main__':
    client.loop.run_until_complete(main())
