#!/usr/bin/python

import base64
import hashlib
import json
import jinja2
from operator import itemgetter
import os
import os.path
import re
import subprocess
import sys
import time
import urllib2

ETCD2BASE = os.getenv("ETCD2BASE") or "http://127.0.0.1:2379"

generation = None

class FellBehind(Exception):
    pass

def etcd_open(path):
    return urllib2.urlopen(ETCD2BASE + path)

def pod_ready(pod):
    containerStatuses = pod["status"]["containerStatuses"]
    if len(containerStatuses) == 0:
        return False
    for status in containerStatuses:
        if not status["ready"]:
            return False
    return True

def pod_matches(selector, pod):
    if "metadata" in pod and "labels" in pod["metadata"] and pod_ready(pod):
        labels = pod["metadata"]["labels"]
        for key, value in selector.iteritems():
            if key not in labels or labels[key] != value:
                return False
        return True
    return len({}) == 0

def get_pods(config, cache):
    namespace = config["namespace"]
    if namespace not in cache:
        try:
            response = etcd_open("/v2/keys/registry/pods/" + namespace + "/")
            response_json = response.read()
        except urllib2.HTTPError as e:
            if e.code == 404:
                return ({}, cache)
            else:
                raise e
        finally:
            try:
                response.close()
            except:
                pass
        node = json.loads(response_json)["node"]
        pods = node["nodes"] if "nodes" in node else []
        cache[namespace] = [json.loads(pod["value"]) for pod in pods]
    pods = {}
    for pod in cache[namespace]:
        if pod_matches(config["selector"], pod):
            pods[pod["metadata"]["name"]] = pod
    return (pods, cache)

def load_services(configmap):
    services_nodes = configmap["data"]
    services = {}
    cache = {}
    for key, service in services_nodes.iteritems():
        service_config = json.loads(service)
        set_service(services, key, service_config, cache)
    return services

def set_service(services, key, service_config, cache = {}):
    service_config["pods"], cache = get_pods(service_config, cache)
    services[key] = service_config
    services[key]["name"] = key

def load_certs(configmap):
    return configmap["data"]

def load_keys(secrets):
    return {name: base64.b64decode(b64key) for name, b64key in secrets["data"].iteritems()}

def merge_certs_and_keys(certs, keys):
    result = {}
    for name, cert in certs.iteritems():
        result[name] = {"cert": cert}

    for name, key in keys.iteritems():
        if name in result:
            result[name]["key"] = key
        else:
            result[name] = {"key": key}

    return result

def refresh():
    global generation
    try:
        response = etcd_open("/v2/keys/registry/configmaps/lb/services")
        response_json = response.read()
        generation = int(response.info().getheader('X-Etcd-Index'))
    finally:
        try:
            response.close()
        except:
            pass

    services = load_services(json.loads(json.loads(response_json)["node"]["value"]))

    certs = {}
    try:
        response = etcd_open("/v2/keys/registry/configmaps/lb/certificates")
        response_json = response.read()
        certs = load_certs(json.loads(json.loads(response_json)["node"]["value"]))
    except urllib2.HTTPError as e:
        if e.code != 404:
            raise e
    finally:
        try:
            response.close()
        except:
            pass

    keys = {}
    try:
        response = etcd_open("/v2/keys/registry/secrets/lb/keys")
        response_json = response.read()
        keys = load_keys(json.loads(json.loads(response_json)["node"]["value"]))
    except urllib2.HTTPError as e:
        if e.code != 404:
            raise e
    finally:
        try:
            response.close()
        except:
            pass

    try:
        response = etcd_open("/v2/keys/registry/configmaps/lb/config")
        response_json = response.read()
    finally:
        try:
            response.close()
        except:
            pass

    node = json.loads(response_json)["node"]
    config = json.loads(node["value"])["data"]
    template = config["template"]
    return {
        "services": services,
        "certificates": merge_certs_and_keys(certs, keys),
        "template": template
    }

pod_re = re.compile("^/registry/pods/([^/]+)/([^/]+)$")

def update(data):
    services = data["services"]
    certificates = data["certificates"]
    global generation
    while True:
        try:
            response = etcd_open("/v2/keys?wait=true&recursive=true&waitIndex=%d" % (generation + 1))
            response_json = response.read()
            if generation < int(response.info().getheader('X-Etcd-Index')) - 10:
                raise FellBehind()
            generation = generation + 1
        finally:
            try:
                response.close()
            except:
                pass
        event = json.loads(response_json)
        if "node" in event:
            node_key = event["node"]["key"]
            if node_key == "/registry/configmaps/lb/services":
                configmap = json.loads(event["node"]["value"])
                data["services"] = load_services(configmap)
                set_service(services, key, json.loads(event["node"]["value"]))
                return
            elif node_key.startswith("/registry/pods/"):
                m = pod_re.match(node_key)
                pod = json.loads(event["node"]["value"])
                if m:
                    namespace, podname = m.group(1, 2)
                    if event["action"] == "delete" or not pod_ready(pod):
                        changed = False
                        for service_config in services.itervalues():
                            if service_config["namespace"] == namespace and podname in service_config.pods:
                                changed = True
                                del service_config["pods"][podname]
                        if changed:
                            return
                    else:
                        changed = False
                        for service_config in services.itervalues():
                            if service_config["namespace"] == namespace and pod_matches(service_config["selector"], pod):
                                changed = True
                                service_config["pods"][podname] = pod
                        if changed:
                            return
            elif node_key == "/registry/configmaps/lb/config":
                config = json.loads(event["node"]["value"])["data"]
                data["template"] = config["template"]
                return
            elif node_key == "/registry/configmaps/lb/certificates":
                keys = {k: v["key"] for k, v in data["certificates"].iteritems() if "key" in v}
                certs = load_certs(json.loads(event["node"]["value"]))
                data["certificates"] = merge_certs_and_keys(certs, keys)
                return
            elif node_key == "/registry/secrets/lb/keys":
                certs = {k: v["cert"] for k, v in data["certificates"].iteritems() if "cert" in v}
                keys = load_keys(json.loads(event["node"]["value"]))
                data["certificates"] = merge_certs_and_keys(certs, keys)
                return

if __name__ == "__main__":
    statspass = os.getenv("STATISTICS_PASSWORD")
    lasthash = None
    certhashes = {}
    while True:
        backoff = 1
        while True:
            try:
                data = refresh()
                break
            except Exception as e:
                sys.stderr.write("Error: Could not load configuration (%s).  Will try again in %d s\n" % (str(e), backoff))
                time.sleep(backoff)
                if backoff < 32:
                    backoff *= 2

        while True:
            serviceslist = data["services"].values()
            serviceslist.sort(key=itemgetter("name"))
            certificatelist = [name for name, certdata in data["certificates"].iteritems() if "key" in certdata and "cert" in certdata]
            config = jinja2.Template(data["template"]).render(stats={"username": "stats", "password": statspass},
                                                              services=serviceslist,
                                                              certificates=certificatelist,
                                                              ssldir=os.path.abspath("ssl"))
            changed = False
            currhash = hashlib.sha512(config).digest()
            if currhash != lasthash:
                changed = True
                sys.stderr.write("Debug: writing new config\n")
                with open("haproxy.cfg", "w") as f:
                    f.write(config)
            else:
                sys.stderr.write("Debug: config file did not change\n")
            lasthash = currhash

            for name, certdata in data["certificates"].iteritems():
                if "key" in certdata and "cert" in certdata:
                    sha512 = hashlib.sha512(certdata["key"])
                    sha512.update(certdata["cert"])
                    currhash = sha512.digest()
                    if name not in certhashes or certhashes[name] != currhash:
                        changed = True
                        sys.stderr.write("Debug: writing SSL certificate for %s\n" % name)
                        with open("ssl/%s.pem" % name, 'w') as f:
                            f.write(certdata["key"])
                            f.write(certdata["cert"])
                        certhashes[name] = currhash

            if changed:
                cmd = ["/usr/sbin/haproxy", "-D", "-p", "/run/haproxy.pid", "-f", os.path.abspath("haproxy.cfg")]
                try:
                    with open("/run/haproxy.pid", "r") as f:
                        pids = f.read().split()
                        cmd.append("-sf")
                        cmd.extend(pids)
                except:
                    pass
                sys.stderr.write("Debug: reloading HAProxy\n")
                subprocess.call(cmd)

            try:
                update(data)
            except Exception as e:
                sys.stderr.write("Warning: Failed to update (%s).  Reloading config\n" % (str(e)))
                break
