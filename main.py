import os
import asyncio
import re
from email.message import EmailMessage
import aiosmtplib
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Configuration
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
CARRIER_MAP = {
    "verizon": "vtext.com",
    "tmobile": "tmomail.net",
    "sprint": "messaging.sprintpcs.com",
    "at&t": "txt.att.net",
    "boost": "smsmyboostmobile.com",
    "cricket": "sms.cricketwireless.net",
    "uscellular": "email.uscc.net",
}

async def send_text_message(
    phone_number: str,
    carrier: str,
    sender_email: str,
    sender_password: str,
    message: str,
    subject: str = None  # Default to None
) -> None:
    """
    Send a single text message via email.

    Args:
        phone_number: The recipient's phone number as a string.
        carrier: The recipient's carrier (e.g., "verizon").
        sender_email: The sender's email address.
        sender_password: The sender's email password.
        message: The text message body.
        subject: The subject of the message (optional, None to exclude).
    """
    # Validate carrier
    if carrier not in CARRIER_MAP:
        raise ValueError(f"Unsupported carrier: {carrier}. Supported carriers are: {', '.join(CARRIER_MAP.keys())}")

    # Build recipient email address
    to_email = f"{phone_number}@{CARRIER_MAP[carrier]}"

    # Create the email message
    email_message = EmailMessage()
    email_message["From"] = sender_email
    email_message["To"] = to_email

    if subject:  # Only set the subject if it's not empty or None
        email_message["Subject"] = subject

    email_message.set_content(message)

    # Send the message
    try:
        await aiosmtplib.send(
            email_message,
            hostname=SMTP_HOST,
            port=SMTP_PORT,
            username=sender_email,
            password=sender_password,
            start_tls=True,
        )
        print(f"Message sent to {phone_number} ({carrier}) successfully.")
    except Exception as e:
        print(f"Failed to send message to {phone_number} ({carrier}): {e}")

if __name__ == "__main__":
    phone_number = "5052897944"  # Replace with recipient's phone number
    carrier = "tmobile"          # Replace with recipient's carrier
    sender_email = os.getenv("EMAIL_ADDRESS")  # Read email from environment variable
    sender_password = os.getenv("EMAIL_PASSWORD")  # Read password from environment variable
    message = "Hello, this is a test message!"  # Replace with your message
    subject = None  # Leave as None to exclude subject

    if not sender_email or not sender_password:
        raise EnvironmentError("Environment variables EMAIL_ADDRESS and EMAIL_PASSWORD must be set.")

    asyncio.run(send_text_message(phone_number, carrier, sender_email, sender_password, message, subject))