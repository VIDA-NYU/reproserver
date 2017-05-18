ReproServer
===========

Goals
-----

  - Import something we can build a Docker image from (currently only a ReproZip package)
  - Build a Docker image from it
  - Allow the user to change experiment parameters and input files
  - Run the experiment
  - Show the log and output files to the user

Components
----------

### Frontend

Web application allowing users to select or upload an experiment, edit parameters, upload input files. After running, shows the log and lets the user download output files.

### Builder

From the experiment file, builds a Docker image and cache it for the runnners.

### Runner

From the cached Docker image, input files, and parameters, run the experiment and store the results.

How to use
----------

This doesn't use docker-compose, because it has [serious limitations](https://github.com/moby/moby/issues/18789). To have better control over the build, and to have efficient & automatic builds, [pydoit](http://pydoit.org/) is used to drive Docker. It can also generate the Kubernetes configuration for you.

Using `doit build` will build the images (`reproserver-*`). Using `doit start` will start all the containers locally. You can use `doit auto start` to automatically rebuild and restart containers when you change their code.

The ports are:

  - [`8000`](http://localhost:8000/) is the frontend web server;
  - [`8080`](http://localhost:8080/) is RabbitMQ's web interface;
  - [`9000`](http://localhost:9000/) is Minio's web interface;
  - [`5000`](http://localhost:5000/) is the Docker registry;
  - `5432` is PostgreSQL, but using the ORM from the `web` container is probably easier (`docker exec -ti reproserver-web python`).
