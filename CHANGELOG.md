CHANGELOG
=========

0.8 (???)
---------

Enhancements:
* Integration with Kubernetes, to run builds/runs in pods (which use Docker-in-Docker)
* Implement a proxy, allowing a user to connect to the running experiment (if it's web-based)
* Improve repository-handling code
* Expose metrics to Prometheus

0.7 (2019-08-28)
----------------

Rewrite from Flask/RabbitMQ to Tornado

Enhancements:
* No longer use a build queue, talk to Docker from main process
* Single container, no longer web/builder/runner
* Parse repository reference from a pasted-in URL
