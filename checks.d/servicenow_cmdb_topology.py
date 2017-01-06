"""
    StackState / Logicalis.
    ServiceNow CMDB topology extraction
"""

# 3rd party
import requests

# project
from checks import AgentCheck, CheckException


class ServiceNowCMDBTopology(AgentCheck):
    INSTANCE_TYPE = "servicenow_cmdb"
    SERVICE_CHECK_NAME = "servicenow.cmdb.topology_information"
    service_check_needed = True

    def check(self, instance):
        if 'url' not in instance:
            raise Exception('ServiceNow CMDB topology instance missing "url" value.')
        # TODO check that other mandatory config fields exist

        basic_auth = instance['basic_auth']
        basic_auth_user = basic_auth['user']
        basic_auth_password = basic_auth['password']
        auth = (basic_auth_user, basic_auth_password)
        # print auth
        # exit(0)

        # url = instance['url']
        # url = instance['url'] + '/api/now/table/cmdb_ci'
        base_url = instance['url']

        instance_key = {
            "type": self.INSTANCE_TYPE,
            "url": base_url
        }

        instance_tags = instance.get('tags', []) # TODO use tags

        default_timeout = self.init_config.get('default_timeout', 5)
        timeout = float(instance.get('timeout', default_timeout))

        self.collect_components(instance_key, base_url, timeout, auth)

    def collect_components(self, instance_key, base_url, timeout, auth):
        """
        collect components from ServiceNow CMDB's cmdb_ci table
        :param instance_key: dict, key to be used to make multiple instances of this check unique
        (the same check can be used for different clusters)
        :param base_url: string, ServiceNow CMDB server to connect to
        :param timeout: connection timeout
        :param auth: basic http authentication
        """
        url = base_url + '/api/now/table/cmdb_ci?sysparm_fields=name,sys_id,sys_class_name,sys_created_on'

        state = self._get_state(url, timeout, auth)

        for component in state['result']:
            id = component['sys_id']
            type = {
                "name": component['sys_class_name']
            }
            data = {
                "name": component['name']
            }

            self.component(instance_key, id, type, data)


    def jsonPrint(self, js): # TODO remove
        import json
        print json.dumps(js, sort_keys=False, indent=2, separators=(',', ': '))

    # TODO fix https warning
    def _get_json(self, url, timeout, auth=None, verify=True):
        tags = ["url:%s" % url]
        msg = None
        status = None
        try:
            r = requests.get(url, timeout=timeout, auth=auth, verify=verify)
            if r.status_code != 200:
                status = AgentCheck.CRITICAL
                msg = "Got %s when hitting %s" % (r.status_code, url)
            else:
                status = AgentCheck.OK
                msg = "ServiceNow CMDB instance detected at %s " % url
        except requests.exceptions.Timeout as e:
            # If there's a timeout
            msg = "%s seconds timeout when hitting %s" % (timeout, url)
            status = AgentCheck.CRITICAL
        except Exception as e:
            msg = str(e)
            status = AgentCheck.CRITICAL
        finally:
            if self.service_check_needed:
                self.service_check(self.SERVICE_CHECK_NAME, status, tags=tags,
                                   message=msg)
                self.service_check_needed = False
            if status is AgentCheck.CRITICAL:
                self.service_check(self.SERVICE_CHECK_NAME, status, tags=tags,
                                   message=msg)
                raise CheckException("Cannot connect to ServiceNow CMDB, please check your configuration.")

        if r.encoding is None:
            r.encoding = 'UTF8'

        return r.json()


    def _get_state(self, url, timeout, auth=None, verify=False):
        return self._get_json(url, timeout, auth, verify)