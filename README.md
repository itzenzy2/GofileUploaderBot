cat << 'EOF' > README.md
# GoFile Master Bot

A Telegram bot that runs on a personal account to both upload files to GoFile.io and download entire folders from it.

## Features

-   **Upload & Download:** Send a direct link or a telegram file to upload it directly to Gofile.io and get the uploaded link.
-   **Folder Downloader:** Send a GoFile folder link to download all its contents.

## Setup

1.  **Install System Tools:**
    ```bash
    sudo apt update && sudo apt install ffmpeg
    ```

2.  **Install Python Libraries:**
    ```bash
    pip install -r requirements.txt
    ```

3.  **Get Your Keys:**
    -   `API_ID` & `API_HASH` from **[my.telegram.org](https://my.telegram.org)**
    -   `GOFILE_TOKEN` from your **[gofile.io](https://gofile.io)** account profile.

## Running the Bot

1.  **Set Your Keys:**
    ```bash
    export API_ID="1234567"
    export API_HASH="0123456789abcdef0123456789abcdef"
    export GOFILE_TOKEN="your_gofile_token_here"
    ```

2.  **Run the Script:**
    ```bash
    python3 master_bot.py
    ```
    The first time, you'll have to log in with your phone number and the code Telegram sends you.
