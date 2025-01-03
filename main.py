import os
import asyncio
import re
import logging
from email.message import EmailMessage
import aiosmtplib
from openai import AsyncOpenAI
import imaplib
import email
import json
import sqlite3
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
IMAP_HOST = "imap.gmail.com"
EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS")  # Your Gmail address
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")  # Your Gmail app password
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")  # OpenAI API key
TARGET_PHONE_NUMBER = os.getenv("TARGET_PHONE_NUMBER")  # Your phone number

if not EMAIL_ADDRESS or not EMAIL_PASSWORD or not OPENAI_API_KEY or not TARGET_PHONE_NUMBER:
    raise ValueError("EMAIL_ADDRESS, EMAIL_PASSWORD, OPENAI_API_KEY, and TARGET_PHONE_NUMBER must be set in the environment.")

# Initialize OpenAI client
client = AsyncOpenAI(api_key=OPENAI_API_KEY)

CARRIER_MAP = {
    "verizon": "vtext.com",
    "tmobile": "tmomail.net",
    "sprint": "messaging.sprintpcs.com",
    "at&t": "txt.att.net",
    "boost": "smsmyboostmobile.com",
    "cricket": "sms.cricketwireless.net",
    "uscellular": "email.uscc.net",
}


def fetch_unread_sms() -> list:
    """
    Fetch unread SMS messages from the Gmail inbox that match the target phone number.

    Returns:
        A list of tuples (phone_number, carrier, body).
    """
    messages = []

    with imaplib.IMAP4_SSL(IMAP_HOST) as mail:
        mail.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        mail.select("inbox")

        # Search for unread emails
        _, search_data = mail.search(None, "UNSEEN")
        email_ids = search_data[0].split()

        if not email_ids:
            raise ValueError("No unread emails found in the inbox.")

        for email_id in email_ids:
            _, data = mail.fetch(email_id, "(RFC822)")

            # Parse the email
            msg = email.message_from_bytes(data[0][1])
            from_email = msg.get("From")
            body = ""

            if not from_email:
                logger.debug("Skipping email with missing 'From' field.")
                continue

            logger.info(f"Processing email from: {from_email}")

            # Validate the "From" field
            try:
                phone_number, carrier = parse_email_address(from_email.strip())
                if phone_number != TARGET_PHONE_NUMBER:
                    logger.debug(f"Skipping email from unrelated phone number: {phone_number}")
                    continue
            except ValueError as e:
                logger.debug(f"Skipping email due to invalid format: {e}")
                continue

            # Get the email body
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        try:
                            body = part.get_payload(decode=True).decode()
                        except Exception as e:
                            logger.debug(f"Failed to decode email body: {e}")
                            continue
                        break
            else:
                try:
                    body = msg.get_payload(decode=True).decode()
                except Exception as e:
                    logger.debug(f"Failed to decode single-part email body: {e}")
                    continue

            if not body:
                logger.debug("Skipping email with empty body.")
                continue

            logger.debug(f"Valid email body: {body}")

            # Append the valid message
            messages.append((phone_number, carrier, body.strip()))

        if not messages:
            raise ValueError("No valid SMS messages found in the inbox.")

        return messages

def parse_email_address(from_email: str) -> tuple:
    """
    Extract the phone number and carrier from the email address.

    Args:
        from_email (str): The "From" field of the email (e.g., "+15052897944@tmomail.net").

    Returns:
        phone_number (str): The extracted phone number.
        carrier (str): The carrier based on the domain.
    """
    logger.debug(f"Parsing email address: {from_email}")

    match = re.match(r"^\+?(\d+)@([a-zA-Z.]+)$", from_email)
    if not match:
        raise ValueError(f"Invalid email address format: {from_email}")

    phone_number, domain = match.groups()
    for carrier, email_domain in CARRIER_MAP.items():
        if domain == email_domain:
            logger.debug(f"Matched domain '{domain}' to carrier '{carrier}'")
            return phone_number, carrier
	
    logger.debug(f"Target phone number: {TARGET_PHONE_NUMBER}")
    logger.debug(f"Incoming phone number: {phone_number}")
    raise ValueError(f"Unknown carrier for domain: {domain}")


async def get_chatgpt_response(user_message: str) -> str:
    """
    Get a structured response from ChatGPT for the provided user message.
    """
    try:
        chat_completion = await client.chat.completions.create(
            messages=[
                {
                    "role": "system",
                    "content": "You are an assistant designed to parse plain-language updates into structured data for a database. \
                                Your goal is to classify messages into one of three categories: 'task', 'habit', or 'note'. \
                                For tasks, include a description and optional due date. For habits, include the habit name and optional frequency. \
                                For notes, simply log the content as freeform text. Always output data in JSON format."
                },
                {"role": "user", "content": user_message},
            ],
            model="gpt-4",
        )
        return chat_completion.choices[0].message.content
    except Exception as e:
        logger.error(f"Error with ChatGPT API: {e}")
        return '{"error": "Unable to process the message"}'


def log_message_to_db(phone_number, carrier, raw_message, parsed_intent, response):
    """
    Log the message and its parsed intent into the database.
    """
    conn = sqlite3.connect("assistant.db")
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO messages (phone_number, carrier, raw_message, parsed_intent, response)
        VALUES (?, ?, ?, ?, ?)
    """, (phone_number, carrier, raw_message, parsed_intent, response))
    conn.commit()
    conn.close()
    logger.info("Message logged to database successfully.")


async def handle_unread_sms():
    """
    Fetch unread SMS messages, process them with ChatGPT, and log them into the database.
    """
    try:
        messages = fetch_unread_sms()
        for phone_number, carrier, body in messages:
            logger.info(f"Processing SMS from {phone_number} ({carrier}): {body}")

            # Get ChatGPT response
            gpt_response = await get_chatgpt_response(body)

            # Log the response into the database
            log_message_to_db(phone_number, carrier, body, gpt_response, gpt_response)

            # Send confirmation SMS
            confirmation = "Your update has been logged. Thank you!"
            await send_sms(phone_number, carrier, confirmation)

    except Exception as e:
        logger.error(f"Error handling SMS: {e}")


async def send_sms(phone_number: str, carrier: str, message: str) -> None:
    """
    Send an SMS via Gmail SMTP.
    """
    if carrier not in CARRIER_MAP:
        logger.error(f"Unsupported carrier: {carrier}. Supported carriers: {', '.join(CARRIER_MAP.keys())}")
        return

    to_email = f"{phone_number}@{CARRIER_MAP[carrier]}"

    email_message = EmailMessage()
    email_message["From"] = EMAIL_ADDRESS
    email_message["To"] = to_email
    email_message.set_content(message)

    try:
        await aiosmtplib.send(
            email_message,
            hostname=SMTP_HOST,
            port=SMTP_PORT,
            username=EMAIL_ADDRESS,
            password=EMAIL_PASSWORD,
            start_tls=True,
        )
        logger.info(f"Message sent to {phone_number} ({carrier}) successfully.")
    except Exception as e:
        logger.error(f"Failed to send message to {phone_number} ({carrier}): {e}")


if __name__ == "__main__":
    asyncio.run(handle_unread_sms())