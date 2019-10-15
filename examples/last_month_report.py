#/usr/bin/env python
import clicktime
import datetime
import calendar
import logging

logging.basicConfig(level=logging.DEBUG)

# Load the token
with open(".token") as f:
    token = f.read().strip()

# Connect to clicktime 
ct = clicktime.ClickTime(token=token)

# Figure out the query date range
today = datetime.date.today()
prev_month = (today.month - 1)
prev_year = today.year
if prev_month == 0:
    prev_month = 12
    prev_year = prev_year - 1
prev_last_day = calendar.monthrange(prev_year, prev_month)[1]
start_date = datetime.date(year=prev_year, month=prev_month, day=1)
end_date = datetime.date(year=prev_year, month=prev_month, day=prev_last_day)

query = ct.reports(
).resolve(
    "JobID", "TaskID", "UserID"
).params(
    StartDate=start_date,
    EndDate=end_date
)

for doc in query.scroll():
    print(doc)
