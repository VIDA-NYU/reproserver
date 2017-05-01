ReproServer
===========

Goals
-----

  - Import something we can build a Docker image from (Dockerfile, ReproZip package, ...)
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
