import datetime
import re
import copy

from iso8601 import iso8601
from pytz import timezone
from checks import CheckException


class SplunkSavedSearch(object):
    def __init__(self, instance_config, saved_search_instance):
        if "name" in saved_search_instance:
            self.name = saved_search_instance['name']
            self.match = None
        elif "match" in saved_search_instance:
            self.match = saved_search_instance['match']
            self.name = None
        else:
            raise Exception("Neither 'name' or 'match' should be defined for saved search.")

        # maps from fields (as they go to output) to corresponding column names in results we get from Splunk
        self.critical_fields = None  # if absent, then fail the search
        self.required_fields = None  # if absent, then drop the item and continue with other items in this search
        self.optional_fields = None  # allowed to be absent
        self.fixed_fields = None  # fields that are filled in by the check

        self.parameters = dict(saved_search_instance.get("parameters", instance_config.default_parameters))
        self.request_timeout_seconds = int(saved_search_instance.get('request_timeout_seconds', instance_config.default_request_timeout_seconds))
        self.search_max_retry_count = int(saved_search_instance.get('search_max_retry_count', instance_config.default_search_max_retry_count))
        self.search_seconds_between_retries = int(saved_search_instance.get('search_seconds_between_retries', instance_config.default_search_seconds_between_retries))
        self.batch_size = int(saved_search_instance.get('batch_size', instance_config.default_batch_size))
        self.app = saved_search_instance.get("app", instance_config.default_app)

    def retrieve_fields(self, data):
        telemetry = {}

        # Critical fields - escalate any exceptions if missing a field
        if self.critical_fields:
            telemetry.update({
                field: take_required_field(field_column, data)
                for field, field_column in self.critical_fields.iteritems()
            })

        # Required fields - catch exceptions if missing a field
        try:
            if self.required_fields:
                telemetry.update({
                    field: take_required_field(field_column, data)
                    for field, field_column in self.required_fields.iteritems()
                })
        except CheckException as e:
            raise LookupError(e)  # drop this item, but continue with next

        # Optional fields
        if self.optional_fields:
            telemetry.update({
                field: take_optional_field(field_column, data)
                for field, field_column in self.optional_fields.iteritems()
            })

        # Fixed fields
        if self.fixed_fields:
            telemetry.update(self.fixed_fields)

        return telemetry


class SplunkInstanceConfig(object):
    def __init__(self, instance, init_config, defaults):
        self.defaults = defaults
        self.init_config = init_config

        self.default_request_timeout_seconds = self.get_or_default('default_request_timeout_seconds')
        self.default_search_max_retry_count = self.get_or_default('default_search_max_retry_count')
        self.default_search_seconds_between_retries = self.get_or_default('default_search_seconds_between_retries')
        self.default_verify_ssl_certificate = self.get_or_default('default_verify_ssl_certificate')
        self.default_batch_size = self.get_or_default('default_batch_size')
        self.default_saved_searches_parallel = self.get_or_default('default_saved_searches_parallel')
        self.default_app = self.get_or_default('default_app')
        self.default_parameters = self.get_or_default('default_parameters')

        self.verify_ssl_certificate = bool(instance.get('verify_ssl_certificate', self.default_verify_ssl_certificate))
        self.base_url = instance['url']
        # username and password to support backward compatibility
        self.username = instance.get('username')
        self.password = instance.get('password')
        authentication = instance.get("authentication")
        if authentication is not None:
            if 'basic_auth' in authentication:
                # this will override the credentials if someone using latest config section
                self.username = authentication.get("basic_auth").get("username")
                self.password = authentication.get("basic_auth").get("password")
            if 'token_auth' in authentication:
                self.audience = authentication.get("token_auth").get("audience")
                self.name = authentication.get("token_auth").get("name")
                self.token_expiration_days = authentication.get("token_auth").get("token_expiration_days", 90)
                self.renewal_days = authentication.get("token_auth").get("renewal_days", 10)

        self.ignore_saved_search_errors = instance.get('ignore_saved_search_errors', False)

    def get_or_default(self, field):
        return self.init_config.get(field, self.defaults[field])

    def get_auth_tuple(self):
        return self.username, self.password


class SplunkTelemetryInstanceConfig(SplunkInstanceConfig):
    def __init__(self, instance, init_config, defaults):
        super(SplunkTelemetryInstanceConfig, self).__init__(instance, init_config, defaults)

        self.default_unique_key_fields = self.get_or_default('default_unique_key_fields')


class SavedSearches(object):
    def __init__(self, saved_searches):
        self.searches = filter(lambda ss: ss.name is not None, saved_searches)
        self.matches = filter(lambda ss: ss.match is not None, saved_searches)

    def update_searches(self, log, saved_searches):
        """
        :param saved_searches: List of strings with names of observed saved searches
        """
        # Drop missing matches
        self.searches = filter(lambda s: s.match is None or s.name in saved_searches, self.searches)

        # Filter already instantiated searches
        new_searches = set(saved_searches).difference([s.name for s in self.searches])

        # Match new searches
        for new_search in new_searches:
            for match in self.matches:
                if re.match(match.match, new_search) is not None:
                    search = copy.deepcopy(match)
                    search.name = new_search
                    log.debug("Added saved search '%s'" % new_search)
                    self.searches.append(search)
                    break


def take_required_field(field, obj):
    """
    Get a field form an object, remove its value and remove the field form the object
    """
    if field not in obj:
        raise CheckException("Missing '%s' field in result data" % field)
    value = obj[field]
    del obj[field]
    return value


def take_optional_field(field, obj):
    """
    Get a field form an object, remove its value and remove the field form the object
    """
    if field not in obj:
        return None
    value = obj[field]
    del obj[field]
    return value


def get_utc_time(seconds):
    return datetime.datetime.utcfromtimestamp(seconds).replace(tzinfo=timezone("UTC"))


def get_time_since_epoch(utc_datetime):
    begin_epoch = get_utc_time(0)
    timestamp = (utc_datetime - begin_epoch).total_seconds()
    return timestamp


def time_to_seconds(str_datetime_utc):
    """
    Converts time in utc format 2016-06-27T14:26:30.000+00:00 to seconds
    """
    parsed_datetime = iso8601.parse_date(str_datetime_utc)
    return get_time_since_epoch(parsed_datetime)


def chunks(l, n):
    """Yield successive n-sized chunks from l."""
    for i in xrange(0, len(l), n):
        yield l[i:i + n]
