# Dynamic HAProxy for Kubernetes

by MuchLearning

Updates HAProxy's config based on Kubernetes pod changes.

## Introduction

This pod watches Kubernetes (or more specifically, the etcd2 used by
Kubernetes) for configuration changes and pod changes (creates, deletes).  When
a change is detected, the configuration is updated, and HAProxy is gracefully
reloaded if needed.  It uses etcd2's watch feature rather than polling, so
updates should be near-instantaneous.

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
- `STATISTICS_PASSWORD`: (optional) the password for accessing the server
  statistics.  Defaults to "IAmAnIdiotForNotChangingTheDefaultPassword".  Only
  needed if you are exposing server statistics in the template.

### ConfigMaps and Secrets

The HAProxy configuration is driven by some Kubernetes configmaps and secrets
in the `lb` namespace.  The pod watches these and updates the configuration
when they change.

- `services` configmap: each key defines a service to be exposed.  The value is
  a JSON object with the following keys:
  - `namespace`: (required) the namespace in which to search for pods to use as
    backends
  - `selector`: (required) a JSON object where the key/value pairs define the
    labels of the pods that will be used as backends
  - *: the configuration may contain any other keys.  The entire JSON object is
    passed to the template (see below) and may be used to control it.  Some
    recommended keys (depending on your template) are:
    - `hostnames`: an array of hostnames to be used for the service
    - `ports`: an array of ports that the pods listen to
    - `ssl`: (optional) whether or not HAProxy should provide HTTPS for the
      service
    - `path`: (optional) only redirect these URL paths to the pods
- `certificates` configmap and `keys` secrets: each key in these defines an SSL
  key and corresponding certificate (in PEM format)
- `config` configmap: the `template` key in this configmap defines a Jinja2
  template to use to generate the HAProxy configuration file.  The template is
  passed these replacements:
  - `services`: a list of services, each of which is a dict corresponding to
    the values given in the `services` configmap above.  The list is sorted in
    the order of the service name (the keys in the `services` configmap).  In
    addition to the keys given in the service's JSON object, each service has
    the following keys:
    - `name`: the name of the service
    - `pods`: a dict of pods where the key is the pod name and the value is a
      description of the pod, like you would get from `kubectl get pod xxxx -o
      json`.  Of particular interest is `pod.status.podIP`, which gives the
      pod's IP address.
  - `certificates`: a list of SSL certificate names.  The keys and
    certificates, in a format usable by HAProxy's `crt` option, are stored in
    `{{ssldir}}/{{name}}`, where `ssldir` is another replacement passed to the
    template.
  - `ssldir`: the base directory for the SSL certificates (see the
    `certificates` replacement)
  - `stats`: a dict with `username` (value is currently hardcoded to "stats")
    and `password` (value is the value of the `STATISTICS_PASSWORD`) keys.
    Intended to be used for controlling access to server statistics
  - `env`: a dict containing the process' environment variables

#### Examples

The `examples` directory has various examples for the configuration template.

- `base.yaml` is a basic HTTP-only configuration
- `ssl.yaml` add HTTPS support
- `varnish.yaml` passes requests to a Varnish cache for services that are
  configured to do so in a setup similar to the one described in
  http://blog.haproxy.com/2012/08/25/haproxy-varnish-and-the-single-hostname-website/
  (see also https://github.com/muchlearning/kubernetes-varnish)
