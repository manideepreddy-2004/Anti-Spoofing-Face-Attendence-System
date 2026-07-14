"""
Management command: mark_absentees

Backfills 'Absent' rows for every registered user, for every past date and
session (Morning/Evening) where no attendance was recorded.

Usage:
    python manage.py mark_absentees

This is safe to run multiple times (it only inserts rows that don't already
exist). For a "real" deployment you'd schedule this to run once daily via
cron (Linux) or Task Scheduler (Windows), e.g. once at 11:55 PM. For this
project, it is also auto-triggered whenever the admin or a user views the
attendance pages, so you don't strictly need to schedule it for the demo -
but it's here so the design is complete and explainable in the viva.
"""

import pymysql
from django.core.management.base import BaseCommand
from attendanceapp.views import mark_absentees_for_past_dates, DB_NAME


class Command(BaseCommand):
    help = "Marks Absent for any registered user who has no attendance record for a past date/session"

    def handle(self, *args, **options):
        connection = pymysql.connect(
            host='localhost', user='root', password='root',
            port=3306, database=DB_NAME
        )
        self.stdout.write("Running absentee backfill...")
        mark_absentees_for_past_dates(connection)
        connection.close()
        self.stdout.write(self.style.SUCCESS("Done. Missed sessions have been marked Absent."))
