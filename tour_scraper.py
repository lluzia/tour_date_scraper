import os
import time
import re
import logging
from datetime import datetime
from typing import List, Dict, Set
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

# Load .env variables for local dev
load_dotenv()

# ─────────────────────────────────────────────
# Logging configuration
# ─────────────────────────────────────────────
os.makedirs("logs", exist_ok=True)
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
log_filename = f"logs/scraper_{timestamp}.log"

log_level = os.getenv("LOG_LEVEL", "INFO").upper()
numeric_level = getattr(logging, log_level, logging.INFO)

logging.basicConfig(
    level=numeric_level,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_filename, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Main Scraper
# ─────────────────────────────────────────────
class TourDateScraper:
    def __init__(self):
        # URLs loaded from environment for secrecy
        self.urls = [
            os.getenv("TOUR_URL_1"),
            os.getenv("TOUR_URL_2"),
            os.getenv("TOUR_URL_3"),
            os.getenv("TOUR_URL_4")
        ]
        self.recipient_email = os.getenv("RECIPIENT_EMAIL")
        self.all_shows: List[Dict] = []
        self.seen_shows: Set[str] = set()

    def scrape_tour_dates(self, url: str) -> List[Dict]:
        """Scrape tour dates using Selenium."""
        if not url:
            logger.warning("Skipping empty tour URL")
            return []

        options = Options()
        options.add_argument("--headless")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1920,1080")

        try:
            driver = webdriver.Chrome(
                service=webdriver.ChromeService(ChromeDriverManager().install()),
                options=options
            )

        except Exception as e:
            logger.error(f"Failed to initialize ChromeDriver: {e}")
            return []

        shows: List[Dict] = []
        try:
            driver.get(url)
            time.sleep(5)

            tour_elements = driver.find_elements(By.CSS_SELECTOR, "div.nectar-hor-list-item")
            logger.info(f"Found {len(tour_elements)} elements on {url}")

            month_re = re.compile(
                r'\b(January|February|March|April|May|June|July|August|September|October|November|December)\b.*?(\d{4})',
                re.I
            )

            for el in tour_elements:
                try:
                    parts = el.find_elements(By.CSS_SELECTOR, "div.nectar-list-item")
                    if len(parts) < 3:
                        continue

                    raw_day_text = parts[0].text.strip()
                    tokens = raw_day_text.split()
                    day_token = tokens[-1] if tokens and tokens[-1].isdigit() else None

                    month_year_text = None
                    try:
                        headings = el.find_elements(By.XPATH, "./preceding::h2 | ./preceding::h3 | ./preceding::h4")
                        for h in reversed(headings):
                            txt = h.text.strip()
                            m = month_re.search(txt)
                            if m:
                                month_year_text = f"{m.group(1)} {m.group(2)}"
                                break
                    except Exception:
                        month_year_text = None

                    show_date, date_text = None, None
                    if day_token and month_year_text:
                        try:
                            show_date = datetime.strptime(f"{int(day_token)} {month_year_text}", "%d %B %Y")
                            date_text = show_date.strftime("%d %B %Y")
                        except Exception as e:
                            logger.debug(f"Could not parse date from {day_token} {month_year_text}: {e}")

                    location_text = parts[1].text.strip()
                    venue = parts[2].text.strip()

                    city, country = "", ""
                    if "," in location_text:
                        left, right = [p.strip() for p in location_text.split(",", 1)]
                        country, city = left, right
                    else:
                        city = location_text

                    ticket_link = ""
                    try:
                        a = el.find_element(By.CSS_SELECTOR, "a.full-link")
                        ticket_link = a.get_attribute("href") or ""
                    except:
                        ticket_link = ""

                    if show_date:
                        show = {
                            "date": show_date,
                            "date_text": date_text,
                            "venue": venue,
                            "city": city,
                            "country": country,
                            "tour_region": self._get_tour_region(url),
                            "ticket_link": ticket_link,
                        }

                        fingerprint = self._create_show_fingerprint(show)
                        if fingerprint not in self.seen_shows:
                            self.seen_shows.add(fingerprint)
                            shows.append(show)
                            logger.debug(f"Parsed show: {show}")
                        else:
                            logger.debug(f"Duplicate skipped: {date_text} - {venue} ({city})")

                except Exception as e:
                    logger.warning(f"Error parsing element: {e}")
                    continue

        finally:
            driver.quit()

        return shows

    def _get_tour_region(self, url: str) -> str:
        if not url:
            return "Unknown"
        if 'uk-and-ireland' in url:
            return 'UK and Ireland'
        elif 'europe' in url:
            return 'Europe'
        elif 'asia-pacific' in url:
            return 'Asia/Pacific'
        else:
            return 'General'

    def _create_show_fingerprint(self, show: Dict) -> str:
        date_str = show["date"].strftime("%Y-%m-%d") if show.get("date") else "unknown"
        venue = show.get("venue", "")
        city = show.get("city", "")
        return f"{date_str}|{venue}|{city}".lower()

    def scrape_all_tours(self):
        for url in self.urls:
            if not url:
                continue
            logger.info(f"Scraping: {url}")
            shows = self.scrape_tour_dates(url)
            self.all_shows.extend(shows)

        logger.info(f"Total unique shows found: {len(self.all_shows)}")

    def get_upcoming_shows(self, days_before: int, window: int = 2) -> List[Dict]:
        today = datetime.now().date()
        return [
            s for s in self.all_shows
            if s.get("date") and days_before - window <= (s["date"].date() - today).days <= days_before + window
        ]

    def format_email_body(self, shows: List[Dict], days_before: int) -> str:
        if not shows:
            return f"No upcoming shows found for {days_before}-day reminder."
        body = "<html><body>"
        body += f"<h2>Tour Alerts - Shows in {days_before} Days!</h2>"
        for show in shows:
            body += "<div style='margin:15px 0;padding:10px;border-left:4px solid #1a73e8;'>"
            body += f"<h3>{show.get('venue','TBA')}</h3>"
            body += f"<p><strong>Date:</strong> {show.get('date_text','TBA')}</p>"
            body += f"<p><strong>Location:</strong> {show.get('city','TBA')}, {show.get('country','TBA')}</p>"
            if show.get("ticket_link"):
                body += f"<p><a href='{show['ticket_link']}'>Buy Tickets</a></p>"
            body += "</div>"
        body += f"<p style='color:#777;'>Automated reminder {days_before} days before the show.</p>"
        body += "</body></html>"
        return body

    def send_email(self, shows: List[Dict], days_before: int):
        if not shows:
            logger.info(f"No shows to notify for {days_before}-day reminder.")
            return

        smtp_server = os.getenv("SMTP_SERVER")
        smtp_port = int(os.getenv("SMTP_PORT", "587"))
        sender_email = os.getenv("SENDER_EMAIL")
        sender_password = os.getenv("SENDER_PASSWORD")

        if not all([smtp_server, sender_email, sender_password, self.recipient_email]):
            logger.error("Missing email configuration in environment variables.")
            return

        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"Tour Alert - {len(shows)} Show(s) in {days_before} Days"
        msg["From"] = sender_email
        msg["To"] = self.recipient_email

        html_body = self.format_email_body(shows, days_before)
        msg.attach(MIMEText(html_body, "html"))

        try:
            with smtplib.SMTP(smtp_server, smtp_port) as server:
                server.starttls()
                server.login(sender_email, sender_password)
                server.send_message(msg)
            logger.info(f"Email sent successfully for {days_before}-day reminder.")
        except Exception as e:
            logger.error(f"Error sending email: {e}")

    def run(self):
        logger.info("Starting tour date scraper...")
        self.scrape_all_tours()
        reminders = [177, 60]
        for days_before in reminders:
            shows = self.get_upcoming_shows(days_before)
            if shows:
                logger.info(f"Found {len(shows)} show(s) for {days_before}-day reminder.")
                self.send_email(shows, days_before)
            else:
                logger.info(f"No shows found that are {days_before} days away.")


if __name__ == "__main__":
    scraper = TourDateScraper()
    scraper.run()
