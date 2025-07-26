# GoFile.io Telegram Uploader

A Telegram bot that runs on a personal account to upload files and direct links to GoFile.io.

## Setup

1.  **Install Libraries:**
    ```bash
    pip install telethon requests requests-toolbelt
    ```

2.  **Get Credentials:**
    -   `API_ID` & `API_HASH` from [my.telegram.org](https://my.telegram.org)
    -   `GOFILE_TOKEN` from your [gofile.io](https://gofile.io) account.

## Running the Bot

1.  **Set Environment Variables:**
    ```bash
    export API_ID="1234567"
    export API_HASH="0123456789abcdef0123456789abcdef"
    export GOFILE_TOKEN="your_gofile_token_here"
    ```

2.  **Run the Bot:**
    ```bash
    python3 gofile_bot.py
    ```

The first time you run it, you will be asked to log in with your phone number and a code from Telegram.
