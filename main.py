import os
import asyncio
import re
import logging
from email.message import EmailMessage
import aiosmtplib
from openai import AsyncOpenAI
import imaplib
import email
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

if not EMAIL_ADDRESS or not EMAIL_PASSWORD or not OPENAI_API_KEY:
    raise ValueError("EMAIL_ADDRESS, EMAIL_PASSWORD, and OPENAI_API_KEY must be set in the environment.")

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


def fetch_all_sms(limit: int = 10) -> list:
    """
    Fetch up to `limit` SMS messages from the Gmail inbox.

    Args:
        limit: The maximum number of messages to fetch.

    Returns:
        A list of tuples (phone_number, carrier, body) for each message.
    """
    messages = []

    with imaplib.IMAP4_SSL(IMAP_HOST) as mail:
        mail.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        mail.select("inbox")

        # Search for all emails in the inbox
        _, search_data = mail.search(None, "ALL")
        email_ids = search_data[0].split()

        if not email_ids:
            raise ValueError("No emails found in the inbox.")

        # Process up to `limit` emails, starting with the most recent
        for email_id in reversed(email_ids[:limit]):
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
            except ValueError as e:
                logger.debug(f"Skipping email due to invalid format: {e}")
                continue

            # Get the email body
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        body = part.get_payload(decode=True).decode()
                        break
            else:
                body = msg.get_payload(decode=True).decode()

            if not body:
                logger.debug("Skipping email with empty body.")
                continue

            # Append the message data to the list
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
    match = re.match(r"^\+?(\d+)@([a-zA-Z.]+)$", from_email)
    if not match:
        raise ValueError(f"Invalid email address format: {from_email}")

    phone_number, domain = match.groups()
    for carrier, email_domain in CARRIER_MAP.items():
        if domain == email_domain:
            return phone_number, carrier

    raise ValueError(f"Unknown carrier for domain: {domain}")


async def get_chatgpt_response(user_message: str) -> str:
    """
    Get a response from ChatGPT for the provided user message.
    """
    try:
        chat_completion = await client.chat.completions.create(
            messages=[
                {
                    "role": "user",
                    "content": user_message,
                }
            ],
            model="gpt-4",
        )
        return chat_completion.choices[0].message.content
    except Exception as e:
        logger.error(f"Error with ChatGPT API: {e}")
        return "Sorry, I couldn't process your message."


async def handle_all_sms():
    """
    Fetch all SMS messages, combine them, process with ChatGPT, and send a single reply.
    """
    try:
        # Fetch all SMS messages
        messages = fetch_all_sms(limit=10)
        if not messages:
            logger.info("No new messages to process.")
            return

        combined_body = "\n".join([f"From {phone}: {body}" for phone, _, body in messages])
        logger.info(f"Combined message body:\n{combined_body}")

        # Get ChatGPT response
        reply = await get_chatgpt_response(combined_body)

        # Send the single reply back to each sender
        for phone_number, carrier, _ in messages:
            await send_sms(phone_number, carrier, reply)

    except Exception as e:
        logger.error(f"Error handling SMS: {e}")


if __name__ == "__main__":
    asyncio.run(handle_all_sms())