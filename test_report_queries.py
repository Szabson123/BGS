import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'BGS.settings')
django.setup()

from django.db import connection, reset_queries
from django.conf import settings
settings.DEBUG = True

from ur.views import BreakdownListViewToReport
from ur.models import Breakdown
from rest_framework.test import APIRequestFactory
from user.models import CustomUser

factory = APIRequestFactory()
# get a user with currentworkshop
user = CustomUser.objects.filter(currentworkshop__isnull=False).first()
if not user:
    user = CustomUser.objects.first()

request = factory.get('/api/ur/all-breakdowns-to-report/?page=1')
request.user = user

view = BreakdownListViewToReport.as_view()
reset_queries()

response = view(request)

print(f"Total queries: {len(connection.queries)}")
seen = {}
duplicates = 0
for i, q in enumerate(connection.queries, 1):
    sql = q['sql'].strip()
    if sql in seen:
        duplicates += 1
        print(f"\n[DUPLICATE #{duplicates}] (first seen at query #{seen[sql]}):")
        print(f"Query #{i}: {sql}")
    else:
        seen[sql] = i
        print(f"Query #{i}: {sql[:100]}...")

print(f"\nSummary: {len(connection.queries)} total, {duplicates} duplicates")
