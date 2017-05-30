#!/usr/bin/python

import base64
import gevent
import gevent.event
import hashlib
import json
import jinja2
from operator import itemgetter
import os
import os.path
import requests
import subprocess
import sys

from gevent import monkey
monkey.patch_all()

K8SBASE = os.getenv("K8SBASE") or "http://127.0.0.1:8000"

change_event  = gevent.event.Event()

def pod_ready(pod):
    if "podIP" not in pod["status"] or not pod["status"]["podIP"]:
        return False
    if "containerStatuses" not in pod["status"]:
        return False
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
    return False

def get_pods(config, all_pods):
    namespace = config["namespace"]
    if namespace not in all_pods:
        return {}
    pods = {}
    for pod in all_pods[namespace].itervalues():
        if pod_matches(config["selector"], pod):
            pods[pod["metadata"]["name"]] = pod
    return pods

def load_services(services_nodes, all_pods):
    services = {}
    for key, service in services_nodes.iteritems():
        service_config = json.loads(service)
        set_service(services, key, service_config, all_pods)
    return services

def set_service(services, key, service_config, all_pods):
    service_config["pods"] = get_pods(service_config, all_pods)
    services[key] = service_config
    services[key]["name"] = key

def load_keys(keys):
    return {name: base64.b64decode(b64key) for name, b64key in keys.iteritems()}

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

class K8sWatcher(gevent.Greenlet):
    def _run(self):
        while True:
            req = requests.get(K8SBASE + "/api/v1/watch/" + self._path, stream=True)
            lines = req.iter_lines()
            for line in lines:
                self._process_line(line)

    def _process_line(self, line):
        data = json.loads(line)
        return self._process_json(data)

class PodWatcher(K8sWatcher):
    _path = "pods"

    def __init__(self):
        K8sWatcher.__init__(self)
        self.pods = {}

    def _process_json(self, json):
        if (json["object"] and json["object"]["kind"] == "Pod"):
            pod = json["object"]
            uid = pod["metadata"]["uid"]
            namespace = pod["metadata"]["namespace"]
            if json["type"] != "DELETED" and pod_ready(pod):
                if namespace not in self.pods:
                    self.pods[namespace] = {}
                self.pods[namespace][uid] = pod
                change_event.set()
            elif namespace in self.pods and uid in self.pods[namespace]:
                del self.pods[namespace][uid]
                change_event.set()

class ConfigWatcher(K8sWatcher):
    def __init__(self, namespace, configmap = None, configname = None):
        K8sWatcher.__init__(self)
        self._path = "namespaces/" + namespace + "/configmaps"
        self.configmap = configmap
        if configmap:
            self._path = self.path + "/" + configmap
        self.configname = configname
        if configname:
            self.config = None
        else:
            self.config = {}

    def _process_json(self, json):
        if (json["object"] and json["object"]["kind"] == "ConfigMap"):
            obj = json["object"]
            if self.configname:
                if "data" in obj and self.configname in obj["data"]:
                    self.config = obj["data"][self.configname]
            elif self.configmap:
                if "data" in obj:
                    self.config = obj["data"]
            else:
                if obj["metadata"]["name"] not in self.config:
                    self.config[obj["metadata"]["name"]] = {}
                if "data" in obj:
                    self.config[obj["metadata"]["name"]] = obj["data"]
            change_event.set()

class SecretsWatcher(K8sWatcher):
    def __init__(self, namespace, configmap, configname = None):
        K8sWatcher.__init__(self)
        self._path = "namespaces/" + namespace + "/secrets/" + configmap
        self.configname = configname
        if configname:
            self.config = None
        else:
            self.config = {}

    def _process_json(self, json):
        if (json["object"] and json["object"]["kind"] == "Secret"):
            obj = json["object"]
            if self.configname:
                if "data" in obj and self.configname in obj["data"]:
                    self.config = json["object"]["data"][self.configname]
            else:
                if "data" in obj:
                    self.config = json["object"]["data"]
            change_event.set()

pod_watcher = PodWatcher()
pod_watcher.start()
key_watcher = SecretsWatcher("lb", "keys")
key_watcher.start()
config_watcher = ConfigWatcher("lb")
config_watcher.start()

gevent.sleep(0.25) # wait a bit for the initial data

sys.stderr.write("Debug: starting\n")

if __name__ == "__main__":
    statspass = os.getenv("STATISTICS_PASSWORD")
    lasthash = None
    certhashes = {}
    while True:
        change_event.wait()
        change_event.clear()

        if "config" in config_watcher.config and "template" in config_watcher.config["config"] \
           and "services" in config_watcher.config:
            cfg = config_watcher.config
            serviceslist = load_services(cfg["services"], pod_watcher.pods).values()
            serviceslist.sort(key=itemgetter("name"))
            certs = merge_certs_and_keys(cfg["certificates"] if "certificates" in cfg else {},
                                         load_keys(key_watcher.config))
            certificatelist = [name for name, certdata in certs.iteritems() if "key" in certdata and "cert" in certdata]
            config = jinja2.Template(cfg["config"]["template"]).render(
                stats={"username": "stats", "password": statspass},
                services=serviceslist,
                certificates=certificatelist,
                ssldir=os.path.abspath("ssl"),
                env=os.environ)
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

            for name, certdata in certs.iteritems():
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
