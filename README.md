[![Matrix](https://img.shields.io/badge/chat-matrix.org-blue.svg)](https://riot.im/app/#/room/#reprozip:matrix.org)

ReproServer
===========

An online service to run ReproZip bundles in the cloud. No need to install reprounzip on your machine, point ReproServer to a RPZ file or upload one, and interact with the reproduced environment from your browser.

Additionally, ReproServer can capture remote web assets referenced by a web application that has been packaged as a RPZ file. That way, you will always be able to get a consistent reproduction of the RPZ bundle, even if those remote assets disappear.

How to run this with Tilt
-------------------------

Make sure you have checked out the submodule with `git submodule init && git submodule update`

You will need [Tilt](https://docs.tilt.dev/install.html), [kubectl](https://kubernetes.io/docs/tasks/tools/), and a cluster with a local registry (that you can set up with [ctlptl](https://github.com/tilt-dev/ctlptl)).

For example, create a local cluster with:

```
minikube start --kubernetes-version=1.22.2 --driver=docker --nodes=1 --container-runtime=docker --ports=8000:30808
```

Install the ingress controller using:

```
kubectl apply -f k8s/nginx-ingress.yml
```

Start the application for development using:

```
tilt up
```

You can then open [`http://localhost:8000/`](http://localhost:8000/) in your browser. Tilt will automatically rebuild images and update Kubernetes as you make changes.

How to run this with docker compose
-----------------------------------

You will need [Docker](https://docs.docker.com/engine/install/) and [docker compose](https://docs.docker.com/compose/install/).

* Make sure you have checked out the submodule with `git submodule init && git submodule update`
* Copy `env.dist` to `.env` (you probably don't need to change the settings)
* Start services by running `docker compose up -d --build`
  * Alternatively, use the development mode (insecure, but displays debug info and autoreloads): `docker compose -f docker compose.dev.yml up -d --build`
* Open [`localhost:8000`](http://localhost:8000/) in your browser

How to stop it: `docker compose down -v`
