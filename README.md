# Dynamic Varnish for Kubernetes

by MuchLearning

Updates Varnish config based on Kubernetes configuration changes.  Intended for
use with https://github.com/muchlearning/kubernetes-haproxy.

## Introduction

This pod watches Kubernetes (or more specifically, the etcd2 used by
Kubernetes) for configuration changes (creates, deletes).  When a change is
detected, the configuration is updated, and Varnish is gracefully reloaded if
needed.  It uses etcd2's watch feature rather than polling, so updates should
be near-instantaneous.

### Important Note

Previous versions pulled the configuration from etcd, but this failed when
Kubernetes used etcd3.  This version now pulls the configuration from the
Kubernetes server instead, but will require changes to the configuration.  In
particular, the `K8SBASE` environment variable needs to be set, pointing to the
URL of the Kubernetes API server.

## Configuration

### Environment variables

- `K8SBASE`: (required) the base URL for the Kubernetes API server (with no
  trailing slash).  The URL must be an HTTP URL; HTTPS is not (yet) supported.
  Defaults to `http://127.0.0.1:8080` (which will probably not work).

### ConfigMaps

The HAProxy configuration is driven by some Kubernetes configmaps and secrets
in the `lb` namespace.  The pod watches these and updates the configuration
when they change.

- `services` configmap: each key defines a service to be exposed.  The value is
  a JSON object as defined in
  https://github.com/muchlearning/kubernetes-haproxy, but with the following
  additional keys used by the example template
  - `varnish`: an object with the following keys:
    - `recv`, `backend_fetch`, `backend_response`, `deliver`: (optional) VCL to
    execute in the `vcl_*` subroutines when a request is made for the service
- `config` configmap: the `varnishtemplate` key in this configmap defines a
  Jinja2 template to use to generate the Varnish VCL file.  The template is
  passed these replacements:
  - `services`: a list of services, each of which is a dict corresponding to
    the values given in the `services` configmap above.  The list is sorted in
    the order of the service name (the keys in the `services` configmap).  In
    addition to the keys given in the service's JSON object, each service has
    the following keys:
    - `name`: the name of the service
  - `env`: a dict containing the process' environment variables

#### Examples

See `examples/varnish.yaml` in
https://github.com/muchlearning/kubernetes-haproxy.
