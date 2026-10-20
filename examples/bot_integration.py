"""Import in an existing Telegram bot; this file never starts polling."""
from datetime import datetime, timedelta
import voltage_data


def status_message(existing_light_status):
    return existing_light_status + voltage_data.status_text()


def voltage_today_message():
    return voltage_data.day_text(hourly=True)


def append_voltage_to_daily_report(existing_report, day):
    return existing_report + "\n\n" + voltage_data.day_text(day)


def menu_button():
    return {"text": "⚡ Напруга за день"}
