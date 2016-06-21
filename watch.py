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

def re_escape(text):
    # NOTE: for now, only escape the special characters that can show up in
    # domain names (i.e., just '.')
    # FIXME: escape other characters
    return re.sub('([.])', r'\\\1', text)

class FellBehind(Exception):
    pass

def etcd_open(path):
    return urllib2.urlopen(ETCD2BASE + path)

def load_services(configmap):
    services_nodes = configmap["data"]
    services = {}
    cache = {}
    for key, service in services_nodes.iteritems():
        service_config = json.loads(service)
        set_service(services, key, service_config, cache)
    return services

def set_service(services, key, service_config, cache = {}):
    services[key] = service_config
    services[key]["name"] = key

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

def update(data):
    services = data["services"]
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
                return
            elif node_key == "/registry/configmaps/lb/config":
                config = json.loads(event["node"]["value"])["data"]
                data["template"] = config["varnishtemplate"]
                return

if __name__ == "__main__":
    lasthash = None
    count = 0
    started = False
    templ_env = jinja2.Environment()
    templ_env.filters['re_escape'] = re_escape
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
            config = templ_env.from_string(data["template"]).render(services=serviceslist,
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

            try:
                update(data)
            except Exception as e:
                sys.stderr.write("Warning: Failed to update (%s).  Reloading config\n" % (str(e)))
                break
