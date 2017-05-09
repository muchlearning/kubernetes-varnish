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
import re
import requests
import subprocess
import sys
import time

from gevent import monkey
monkey.patch_all()

K8SBASE = os.getenv("K8SBASE") or "http://127.0.0.1:8000"

change_event  = gevent.event.Event()

generation = None

def re_escape(text):
    # NOTE: for now, only escape the special characters that can show up in
    # domain names (i.e., just '.')
    # FIXME: escape other characters
    return re.sub('([.])', r'\\\1', text)

def load_services(services_nodes):
    services = {}
    cache = {}
    for key, service in services_nodes.iteritems():
        service_config = json.loads(service)
        set_service(services, key, service_config)
    return services

def set_service(services, key, service_config):
    services[key] = service_config
    services[key]["name"] = key

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
                self.config = obj["data"][self.configname]
            elif self.configmap:
                self.config = obj["data"]
            else:
                if obj["metadata"]["name"] not in self.config:
                    self.config[obj["metadata"]["name"]] = {}
                self.config[obj["metadata"]["name"]] = obj["data"]
                change_event.set()

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
    template = config["varnishtemplate"]
    return {
        "services": services,
        "template": template
    }

config_watcher = ConfigWatcher("lb")
config_watcher.start()

if __name__ == "__main__":
    lasthash = None
    count = 0
    started = False
    templ_env = jinja2.Environment()
    templ_env.filters['re_escape'] = re_escape
    while True:
        change_event.wait()
        change_event.clear()
        if "config" in config_watcher.config and "varnishtemplate" in config_watcher.config["config"]:
            cfg = config_watcher.config
            serviceslist = load_services(cfg["services"]).values()
            serviceslist.sort(key=itemgetter("name"))
            config = templ_env.from_string(cfg["config"]["varnishtemplate"]).render(
                services=serviceslist,
                env=os.environ)
            changed = False
            currhash = hashlib.sha512(config).digest()
            if currhash != lasthash:
                changed = True
                sys.stderr.write("Debug: writing new config\n")
                with open("varnish.vcl", "w") as f:
                    f.write(config)
            else:
                sys.stderr.write("Debug: config file did not change\n")
            lasthash = currhash

            if changed:
                count += 1
                vclname = "%s-%d" % (time.strftime("%Y-%m-%dT%H:%M:%S"), count)
                if started:
                    cmd = ["/usr/bin/varnishadm", "vcl.load", vclname, os.path.abspath("varnish.vcl")]
                    sys.stderr.write("Debug: compiling new VCL (%s)\n" % vclname)
                    if subprocess.call(cmd):
                        sys.stderr.write("ERROR: could not compile VCL")
                    else:
                        cmd = ["/usr/bin/varnishadm", "vcl.use", vclname]
                        sys.stderr.write("Debug: using new VCL\n")
                        subprocess.call(cmd)
                else:
                    cmd = ["/usr/sbin/varnishd", "-f", os.path.abspath("varnish.vcl"), "-a", ":80"]
                    storage = (os.getenv("STORAGE") or "malloc").split(";")
                    for s in storage:
                        cmd += ["-s", s]

                    sys.stderr.write("Debug: starting varnish\n")
                    subprocess.call(cmd)
                    started = True
