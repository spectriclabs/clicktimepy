QuickStart
============
This library is a Python interface to the clicktime.com REST API v2.

Here is how to get all time-entries for last month, resolving all
of the Jobs, Users, and Tasks.

```python
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

query = ct.reports(StartDate=start_date, EndDate=end_date)
response = query.resolve("JobID", "TaskID", "UserID").scroll()

for doc in response:
    print(doc)
```

The library can also be used as a command-line application.

```bash
$ python3 clicktime.py reports \
    --resolve JobID \
    --resolve UserID \
    --resolve TaskID \
    --scroll
```

Limitations
===
* only support read APIs, does not support any APIs that modify values

