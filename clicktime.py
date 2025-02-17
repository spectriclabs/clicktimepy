#!/usr/bin/env python
#
# A simple library to interfaces with the ClickTime API as documented
# at http://app.clicktime.com/api/1.3/help
#
# Copyright 2012 Michael Ihde
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import http.client
import base64
import copy
import json
import datetime
import urllib.parse
import logging
import contextlib
import requests

class Connection(object):
    """
    A basic ClickTime connection
    """

    SERVER = "api.clicktime.com"
    URL_BASE = "v2"

    def __init__(self, username=None, password=None, token=None):
        if username and password:
            auth = base64.encodestring(
                f"{username}:{password}".encode("utf-8")
            ).strip()  # remove the extra newline
            self.__headers = {"Authorization": "Basic %s" % auth.decode("utf-8")}
        elif token:
            self.__headers = {"Authorization": "Token %s" % token}
        else:
            raise AttributeError("either username/password or token must be provided")

    def get(self, url, *path, **params):
        """
        Helper function to make a generic GET request
        """
        # Prepare the headers, but take care not to manipulate the member varible
        if self.__headers is not None:
            headers = copy.copy(self.__headers)
        else:
            headers = {}
        headers["Content-Type"] = "application/json"

        # Create the connection
        with contextlib.closing(http.client.HTTPSConnection(self.SERVER)) as connection:

            # Encoding the query params
            q = urllib.parse.urlencode(params)

            # Build the full URL and log it
            if path:
                path = "/" + "/".join(path)
            else:
                path = ""
            full_url = f"/{self.URL_BASE}/{url}{path}?{q}"
            logging.debug(f"GET {full_url}")

            # Make the request
            connection.request("GET", full_url, headers=headers)

            # Get the response
            resp = connection.getresponse()
            data = resp.read()

        # Parse the data
        data = self._parse(data)

        # Return everything
        return data, resp.status, resp.reason

    def scroll(self, url, *path, **params):
        """
        Helper function to automatically scroll all documents
        """
        if "offset" in params:
            raise AttributeError("offset cannot be used with scroll_reports")

        while True:
            result, _, _ = self.get(url, *path, **params)
            if not result:
                logging.debug("no result returned")
                break
            if not result.get("data"):
                logging.debug("data is empty")
                break

            count = result.get("page", {}).get("count")
            limit = result.get("page", {}).get("limit")
            _next = result.get("page", {}).get("links", {}).get("next")

            # yeild every item in data
            for d in result.get("data"):
                yield d

            # see if there is a next page
            if _next is None:
                logging.debug("no next page")
                break
            if count == 0:
                break

            params["offset"] = params.get("offset", 0) + limit

    def post(self, url, data=None):
        """
        Internal helper method for POST requests.
        """
        if self.headers is not None:
            headers = copy.copy(self.headers)
        else:
            headers = {}
        headers["content-type"] = "application/json; charset=utf-8"
        connection = http.client.HTTPSConnection(self.SERVER)
        connection.request(
            "POST", "%s/%s" % (self.URL_BASE, url), headers=headers, body=data
        )
        resp = connection.getresponse()
        data = resp.read()
        connection.close()
        return data, resp.status, resp.reason

    def _parse(self, json_str, default=None):
        try:
            return json.loads(json_str)
        except ValueError:
            logging.error("Error parsing JSON '%s'", json_str)
            return default


##############################################################################


class Resolver(object):
    """
    The Resolver takes entires like "JobID" and then does
    a look up using the endpoint "Jobs/{identifer}" and placed
    the result into "Job"
    """

    def __init__(self, ct, *resolvers):
        self.ct = ct
        self.resolvers = resolvers
        self.cache = {}

    def nested_set(self, dic, value, *keys):
        for key in keys[:-1]:
            dic = dic.setdefault(key, {})
        dic[keys[-1]] = value

    def nested_get(self, dic, *keys):
        for key in keys[:-1]:
            dic = dic.get(key, {})
        return dic.get(keys[-1])

    def _resolve(self, ct, resolver, d):
        resolver_fields = tuple(resolver.split("."))

        identifier = self.nested_get(d, *resolver_fields)

        endpoint = resolver_fields[-1][0:-2]
        keys = resolver_fields[0:-1] + (endpoint,)

        cache_key = (keys, identifier)
        if cache_key in self.cache:
            data = self.cache[cache_key]
        else:
            value, _, _ = self.ct.connection.get(f"{endpoint}s/{identifier}")
            data = value.get("data")
            self.cache[cache_key] = data

        self.nested_set(d, data, *keys)

        return d

    def resolve_all(self, ct, d):
        for resolver in self.resolvers:
            self._resolve(ct, resolver, d)
            
    def _prefetch(self, ct, resolver):
        resolver_fields = tuple(resolver.split("."))
        endpoint = resolver_fields[-1][0:-2]
        keys = resolver_fields[0:-1] + (endpoint,)

        callable_endpoint = getattr(ct, f'{endpoint.lower()}s', None)
        if not callable(callable_endpoint):
            # Cannot find endpoint for pre-fetch
            logging.warning("Cannot prefetch %s", endpoint)
            return
        
        for vv in callable_endpoint().scroll():
            identifier = self.nested_get(vv, 'ID')
            cache_key = (keys, identifier)
            self.cache[cache_key] = vv
    
    def prefetch(self):
        for resolver in self.resolvers:
            self._prefetch(self.ct, resolver)
            
##############################################################################


class Result(object):
    """
    Result object
    """

    class ResultIterator(object):
        """
        Internal result iterator
        """

        def __init__(self, data):
            self.data = data
            self._index = 0

        def __next__(self):
            if self._index < len(self.data):
                v = self.data[self._index]
                self._index += 1
                return v
            raise StopIteration

    def __init__(self, result, status, reason):
        self.result = result
        self.status = status
        self.reason = reason

    def resolve(self, *resolvers):
        return Resolver(self.ct, self, *resolvers)

    @property
    def isiterable(self):
        return isinstance(self.data, list)

    @property
    def page(self):
        return self.result.get("page")

    @property
    def data(self):
        return self.result.get("data")

    def __iter__(self):
        if self.isiterable:
            return Result.ResultIterator(self.data)
        else:
            raise TypeError("result is not iterable")


class Endpoint(object):
    def __init__(self, ct, url, valid_params=None):
        self.ct = ct
        self.url = url
        if valid_params:
            self.valid_params = set(valid_params)
        else:
            self.valid_params = set()
        self.resolver = None
        self.parameters = {}
        self.path = []

    def resolve(self, *resolvers):
        self.resolver = Resolver(self.ct, *resolvers)

        return self

    def params(self, **params):
        invalid_params = set(params.keys()) - self.valid_params
        if invalid_params:
            raise ValueError(f"invalid params: {invalid_params}")

        self.parameters = dict(params)

        return self

    def check_params(self, params):
        invalid_params = set(params.keys()) - self.valid_params
        return invalid_params

    def execute(self):
        result, status, reason = self.ct.connection.get(
            self.url, *self.path, **self.parameters
        )

        result = Result(result, status, reason)
        if self.resolver:
            if result.isiterable:
                for d in result:
                    self.resolver.resolve_all(ct, d)
            else:
                self.resolver.resolve_all(ct, result.data)

        return result

    def get(self):
        return self.execute().data

    def scroll(self, *path, **params):
        raise TypeError("endpoint is not scrollable")


class ScrollableEndpoint(Endpoint):
    def scroll(self, **params):
        for d in self.ct.connection.scroll(self.url, *self.path, **self.parameters):
            if self.resolver:
                self.resolver.resolve_all(self.ct, d)
            yield d

##############################################################################
# Endpoints
##############################################################################
class AllocationsEndpoint(ScrollableEndpoint):
    def __init__(self, ct):
        super().__init__(
            ct,
            "Allocations",
            (
                "JobID",
                "UserID",
                "DivisionID",
                "StartMonth",
                "EndMonth",
                "JobIsActive",
                "UserIsActive",
                "limit",
                "offset",
            ),
        )


class CustomFieldsEndpoint(ScrollableEndpoint):
    def __init__(self, ct, base_url):
        super().__init__(ct, f"{base_url}/CustomFieldDefinitions", ("limit", "offset"))

    def params(self, **params):
        customFieldDefinitionID = params.pop("customFieldDefinitionID", None)
        if customFieldDefinitionID is not None:
            self.path = [customFieldDefinitionID]
        else:
            self.path = []
        return super().params(**params)


class ClientEndpoint(ScrollableEndpoint):
    def __init__(self, ct):
        super().__init__(
            ct,
            "Clients",
            ("ID", "IsActive", "Name", "ShortName", "ClientNumber", "limit", "offset"),
        )

    def custom_fields(self):
        return CustomFieldsEndpoint(self.ct, "Clients")

    def params(self, **params):
        clientID = params.pop("clientID", None)
        if clientID is not None:
            self.path = [clientID]
        else:
            self.path = []
        return super().params(**params)


class CompanyEndpoint(Endpoint):
    def __init__(self, ct):
        super().__init__(ct, "Company")


class CustomMessagesEndpoint(Endpoint):
    def __init__(self, ct):
        super().__init__(ct, "CustomMessages")

    def params(self, **params):
        customMessageID = params.pop("customMessageID", None)
        if customMessageID is not None:
            self.path = [customMessageID]
        else:
            self.path = []
        return super().params(**params)


class DivisionsEndpoint(ScrollableEndpoint):
    def __init__(self, ct):
        super().__init__(ct, "Divisions", ("ID", "IsActive", "Name", "limit", "offset"))

    def custom_fields(self):
        return CustomFieldsEndpoint(self.ct, "Divisions")

    def params(self, **params):
        clientID = params.pop("divisionID", None)
        if clientID is not None:
            self.path = [clientID]
        else:
            self.path = []
        return super().params(**params)


class ReportsEndpoint(ScrollableEndpoint):
    def __init__(self, ct):
        super().__init__(
            ct,
            "Reports/Time",
            (
                "JobID",
                "ClientID",
                "UserID",
                "TaskID",
                "DivisionID",
                "LabelID",
                "TimesheetID",
                "TimesheetStatus",
                "StartDate",
                "EndDate",
                "IsBillable",
                "limit",
                "offset",
                "verbose",
            ),
        )


class JobsEndpoint(ScrollableEndpoint):
    def __init__(self, ct):
        super().__init__(
            ct,
            "Jobs",
            (
                "ID",
                "ClientID",
                "Name",
                "JobNumber",
                "IsActive",
                "ProjectManagerID",
                "limit",
                "offset",
            ),
        )


class TimeEntriesEndpoint(ScrollableEndpoint):
    def __init__(self, ct):
        super().__init__(
            ct,
            "TimeEntries",
            ("JobID", "UserID", "TaskID", "StartDate", "EndDate", "limit", "offset"),
        )


class UsersEndpoint(ScrollableEndpoint):
    def __init__(self, ct):
        super().__init__(
            ct,
            "Users",
            (
                "ID",
                "IsActive",
                "Name",
                "Email",
                "DivisionID",
                "TimesheetApproverID",
                "ExpenseApproverID",
                "EmploymentTypeID",
                "JobID",
                "SecurityLevel",
                "ManagerPermission",
                "limit",
                "offset",
            ),
        )

    def custom_fields(self):
        return CustomFieldsEndpoint(self.ct, "Users")

    def params(self, **params):
        userID = params.pop("userID", None)
        if userID is not None:
            self.path = [userID]
        else:
            self.path = []
        return super().params(**params)

    def timeOffTypes(self, userID, timeOffTypeID=None):
        if timeOffTypeID is None:
            result = self.ct.connection.get(f"Users/{userID}/TimeOffTypes")
        else:
            result = self.ct.connection.get(f"Users/{userID}/TimeOffTypes/{timeOffTypeID}")
            
        if result[1] != 200:
            raise RuntimeError
        
        if self.resolver:
            if isinstance(result[0]['data'], list):
                for d in result:
                    self.resolver.resolve_all(self.ct, d)
            else:
                self.resolver.resolve_all(self.ct, result[0]['data'])
                
        return result[0]['data']

class TimeOffEndpoint(ScrollableEndpoint):
    def __init__(self, ct):
        super().__init__(
            ct,
            "TimeOff",
            (
                "ID",
                "TimeOffTypeID",
                "UserID",
                "FromDate",
                "ToDate",
                "Date",
                "limit",
                "offset",
            ),
        )

    def custom_fields(self):
        return CustomFieldsEndpoint(self.ct, "Users")

    def params(self, **params):
        timeOffID = params.pop("timeOffID", None)
        if timeOffID is not None:
            self.path = [timeOffID]
        else:
            self.path = []
        return super().params(**params)

class ExpenseSheetEndpoint(ScrollableEndpoint):
    def __init__(self, ct):
        super().__init__(
            ct,
            "ExpenseSheets",
            (
                "ID",
                "FromExpenseSheetDate",
                "ToExpenseSheetDate",
                "Status",
                "Paid",
                "ExpenseApproverID",
                "UserID",
                "UserIsActive",
                "DivisionID",
                "EmploymentTypeID",
                "ExpenseDate",
                "SortBy",
            ),
        )

class ExpenseItemsEndpoint(ScrollableEndpoint):
    def __init__(self, ct):
        super().__init__(
            ct,
            "ExpenseItems",
            (
                "ID",
                "ExpenseDate",
                "ExpenseSheetID",
                "ExpenseTypeID",
                "JobID",
                "UserID",
                "SortBy"
            ),
        )

class TimeOffEndpoint(ScrollableEndpoint):
    def __init__(self, ct):
        super().__init__(
            ct,
            "TimeOff",
            ("ID", "UserID", "TimeOffTypeID", "FromDate", "ToDate", "Date", "limit", "offset"),
        )

class TimeOffTypesEndpoint(ScrollableEndpoint):
    def __init__(self, ct):
        super().__init__(
            ct,
            "TimeOffTypes",
            ("ID","IsActive", "RequiresApproval", "Name", "DefaultApproverID", "limit", "offset"),
        )

class UserWorkTypeBalanceHistoryWithPayrollTime(Endpoint):
    pass

class WebSession():
    login_url = 'https://app.clicktime.com/Login/'

    def __init__(self , username, password):
        # Create a session to persist cookies across requests
        self.session = requests.Session()

        payload = {
            'username': username,
            'password': password,
            'provider': 'EmailPass',
            'SSLstatus': 'on'
        }

        response = self.session.post(WebSession.login_url, data=payload)

        if response.status_code != 200:
            raise RuntimeError
        
class ListView():
    
    web_url = "https://app.clicktime.com/App/"
    svc_path = "ListView/ListViewService.asmx/"

    columns = dict(
        PERSON = [
                "2-psJIfcwbMY", # Name
                "24uJJ2DoKtK0",
                "284Ri4fKHoMo",
                "2Bz2YU9H4DXA",
                "2CHK85057icc",
                "2EKI-5WyMpXQ",
                "2EMfL7A_pJi4",
                "2FBsWyHL46n4",
                "2FH6N7-ZL6zI",
                "2G1yR_AwyuY8",
                "2GSSTvHpOT0E",
                "2GhVd2qw9CGM",
                "2J35huSXUVtU",
                "2KvH3OFg5jyE",
                "2fQa-o-al2Jo",
                "2gVrDeJsbFXY",
                "2kPC_THyPR4o",
                "2kfZCtFD1ELY",
                "2ole_yntoaAA",
                "2vAPJ_Jfb1a8",
                "2wSQA8eGTnFM",
                "AccountingPackageID",
                "Costs",
                "DefaultTask",
                "Division",
                "EmailAddress",
                "EmployeeNumber",
                "EmployeePerformance",
                "EmploymentType",
                "EndDate",
                "ExpenseSheetApprovedBy",
                "FullName",
                "IncompleteTimesheetRule",
                "Notes",
                "NotesRequired",
                "PersonDetail",
                "PreventIncompleteTimesheetSubmission",
                "RequireStopwatch",
                "Role",
                "ScrambledUserID",
                "Security",
                "StartDate",
                "StartEndTimeEntryRequired",
                "Status",
                "TimeOffApprovedBy",
                "TimesheetApprovedBy"
            ]
        )

    def __init__(self, session):
        self.web = session

    def get_list(self, listType, sort=None, start=0, limit=100):
        headers = {
            'Content-Type': 'application/json',  # Example if you're sending JSON data
            'Accept': '*/*'
        }

        if listType not in ListView.columns:
            raise ValueError("unsupported listType")
        
        payload = {
            "start": start,
            "limit": limit,
            "listType":listType,
            "activeOnly":False,
            "inactiveOnly":False,
            "neverSignedIn":False,
            # it's not clear what this does, but if it is changed
            # the response will fail
            "columnIds": ListView.columns[listType],
            "filter":""
        }

        if sort:
            payload.update({
                "dir": sort[0],
                "sort":sort[1],
            })

        
        protected_url = ListView.web_url + ListView.svc_path + "GetList"

        protected_response = self.web.session.post(
            protected_url,
            headers=headers,
            json=payload
        )

        result = json.loads(protected_response.text)
        if result.get('d'):
            return result['d']['totalCount'], result['d']['rows']
        else:
            raise RuntimeError(result)

class LeaveType():
    web_url = "https://app.clicktime.com/App/"
    svc_path = "LeaveTypeControls/LeaveTypeService.asmx/"

    def __init__(self, session):
        self.web = session

    def list(self, targetUserId):
        headers = {
            'Content-Type': 'application/json',  # Example if you're sending JSON data
            'Accept': '*/*'
        }

        payload = {
            "scrUserID": targetUserId, # the typo 'scr' vs 'src' is actually required by the API
            "sortBy": "",
            "activeOnly": True,
            "offTimeOnly": True,
            "includeRequiredApproval": True,
            "workTypeIDs": ""
        }

        protected_url = LeaveType.web_url + LeaveType.svc_path + "ListUserWorkTypeBalance"

        response = self.web.session.post(
            protected_url,
            headers=headers,
            json=payload
        )

        result = json.loads(response.text)
        if result.get('d'):                
            return result['d']
        else:
            return None
             
class UpdateUserWorkTypeBalanceHistory():
    web_url = "https://app.clicktime.com/App/"
    svc_path = "Details/WorkType/WorkTypeService.asmx/"

    def __init__(self, session):
        self.web = session

    def adjust(self, changeType, targetUserId, timeOffType, value, date, note=""):
        headers = {
            'Content-Type': 'application/json',  # Example if you're sending JSON data
            'Accept': '*/*'
        }

        if isinstance(date, datetime.datetime):
            date = date.strftime("%m/%d/%Y")

        payload = {
            "targetUserID": targetUserId,
            "userWorkTypeBalanceID": timeOffType,
            "changeType": changeType, #3=Add, 4=Subtract, 5=Set
            "value": str(value),
            "date": date,
            "note": note
        }

        protected_url = UpdateUserWorkTypeBalanceHistory.web_url + UpdateUserWorkTypeBalanceHistory.svc_path + "UpdateUserWorkTypeBalanceHistory"

        response = self.web.session.post(
            protected_url,
            headers=headers,
            json=payload
        )

        result = json.loads(response.text)
        
        return result


    def add(self, targetUserId, timeOffType, value, date, note=""):
        return self.adjust(3, targetUserId, timeOffType, value, date, note)

    def sub(self, targetUserId, timeOffType, value, date, note=""):
        return self.adjust(4, targetUserId, timeOffType, value, date, note)

    def set(self, targetUserId, timeOffType, value, date, note=""):
        return self.adjust(5, targetUserId, timeOffType, value, date, note)

##############################################################################
# ClickTime
##############################################################################


class ClickTime(object):
    """
    Primary object to interact with the ClickTime API.

    Instantiate the object:

        >>> ct = ClickTime(token="your_api_token")

    Access an endpoint:

        >>> ct.reports().get()

    Or to scoll through all the records:

        >>> ct.reports().scroll()

    If you want to resolve identifiers (i.e. JobID, UserID)

            >>> ct.reports().resolve("JobID", "UserID").scroll()

    """

    def __init__(self, username=None, password=None, token=None):
        self.username = username
        self.password = password
        self.token = token

    @property
    def connection(self):
        return Connection(self.username, self.password, self.token)

    def allocations(self):
        return AllocationsEndpoint(self)

    def clients(self):
        """
        Also known as Project Groups
        """
        return ClientEndpoint(self)

    def company(self):
        return CompanyEndpoint(self)

    def custom_messages(self):
        return CustomMessagesEndpoint(self)

    def divisions(self):
        return DivisionsEndpoint(self)

    def jobs(self):
        return JobsEndpoint(self)

    def reports(self):
        return ReportsEndpoint(self)

    def time_entries(self):
        return TimeEntriesEndpoint(self)

    def users(self):
        return UsersEndpoint(self)

    def timeoff(self):
        return TimeOffEndpoint(self)


##############################################################################
if __name__ == "__main__":
    """
    Example implementation using the ClickTime class
    """
    from optparse import OptionParser
    from pprint import pprint
    import logging
    import os

    try:
        import elasticsearch
    except ImportError:
        pass

    logging.basicConfig()

    parser = OptionParser()
    parser.add_option("-u", "--username")
    parser.add_option("-p", "--password")
    parser.add_option("-t", "--token")
    parser.add_option("--debug", action="store_true", default=False)
    parser.add_option("--scroll", action="store_true", default=False)
    parser.add_option("--resolve", action="append", default=[], dest="resolvers")
    parser.add_option("--param", action="append", default=[], dest="params")
    opts, args = parser.parse_args()

    # If we are debugging, turn extra debugging on
    if opts.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    # If authentication wasn't provided, look for '.token'
    if (opts.username is None) and (opts.password is None) and (opts.token is None):
        if os.path.exists(".token"):
            with open(".token") as f:
                opts.token = f.read().strip()

    # Create the API class
    ct = ClickTime(opts.username, opts.password, opts.token)

    # See what Endpoint the user wants to calls
    if args:
        actions = args[0].lower().split(".")
        params = dict([p.split("=") for p in opts.params])

        endpoint = ct
        for action in actions:
            if hasattr(endpoint, action):
                endpoint_factory = getattr(endpoint, action, None)
                endpoint = endpoint_factory()
            else:
                print(f"invalid action {action}")
                raise SystemExit()

        if opts.scroll:
            for d in endpoint.params(**params).resolve(*opts.resolvers).scroll():
                pprint(d)

        else:
            pprint(endpoint.params(**params).resolve(*opts.resolvers).get())
