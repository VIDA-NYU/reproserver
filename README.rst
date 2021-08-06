.. image:: https://img.shields.io/badge/chat-matrix.org-blue.svg
   :alt: Matrix
   :target: https://riot.im/app/#/room/#reprozip:matrix.org

ReproServer
===========

Goals
-----

- Import something we can build a Docker image from (currently only a ReproZip package)
- Build a Docker image from it
- Allow the user to change experiment parameters and input files
- Run the experiment
- Show the log and output files to the user

How to run this with docker-compose
-----------------------------------

You will need `Docker <https://hub.docker.com/search/?type=edition&offering=community>`__ and `docker-compose <https://docs.docker.com/compose/install/>`__.

- Make sure you have checked out the submodule with ``git submodule init && git submodule update``
- Copy ``env.dist`` to ``.env`` (you probably don't need to change the settings)
- Start services by running ``docker-compose up -d --build``
- Open `localhost:8000 <http://localhost:8000/>`__ in your browser

How to stop it: ``docker-compose down -v``

How to run this with kind
-------------------------

You will need `kind <https://kind.sigs.k8s.io/docs/user/quick-start/>`__ and `kubectl <https://kubernetes.io/docs/tasks/tools/install-kubectl/>`__.

- Make sure you have checked out the submodule with ``git submodule init && git submodule update``
- Start a kind cluster using ``kind create cluster --config kind.yml``
- Set up ingress-nginx using ``kind apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v0.45.0/deploy/static/provider/kind/deploy.yaml``
- Create the volumes using ``kubectl apply -f k8s-volumes.yml``
- Create the secrets using ``kubectl apply -f k8s-secrets.yml``
- Create the minio deployment using ``kubectl apply -f k8s-minio.yml``
- Create the service account ReproServer will use to start pods using ``kubectl apply -f k8s-sa.yml``
- Create the ingress using ``kubectl apply -f k8s-ingress.yml``
- Build the image using ``docker-compose build web`` and load it into the kind node using ``kind load docker-images reproserver_web`` (might take a minute)
- Finally, create the services using ``kubectl apply -f k8s.yml``
- Open `http://localhost:8000/ <http://localhost:8000/>`__ in your browser
