version_settings(constraint='>=0.22.2')
load('ext://docker_build_sub', 'docker_build_sub')

# Rebuild image if those change
full_rebuild = ['pyproject.toml', 'poetry.lock', 'reprozip']
# Just sync those and let Tornado reload
just_sync = ['reproserver/repositories', 'reproserver/static', 'reproserver/templates', 'reproserver/web']
# Have the rest of the code rebuild as well, the runner uses them
for path in listdir('reproserver', recursive=True):
    is_synced = False
    if path.endswith('.pyc'):
        continue
    for synced in just_sync:
        if path.startswith(os.path.join(os.getcwd(), synced)):
            is_synced = True
    if not is_synced:
        full_rebuild.append(path)

docker_build_sub(
    'reproserver_web',
    context='.',
    # chown files to allow live update to work
    extra_cmds=['USER root', 'RUN chown -R appuser /usr/src/app', 'USER appuser'],
    only=['reprozip', 'reproserver', 'pyproject.toml', 'poetry.lock', 'README.rst', 'LICENSE.txt'],
    live_update=[
        fall_back_on(full_rebuild),
    ] + [
        sync(path, '/usr/src/app/' + path)
        for path in just_sync
    ],
    # Update the OVERRIDE_RUNNER_IMAGE variable
    match_in_env_vars=True,
)

k8s_yaml([
    'k8s/volumes.yml',
    'k8s/sa.yml',
    'k8s/secrets.yml',
    'k8s/ingress.yml',
    'k8s/minio.yml',
    'k8s/postgres.yml',
    'k8s/registry.yml',
    'k8s/reproserver.yml',
])

k8s_resource(
    objects=['ingress-nginx-controller:Deployment:ingress-nginx'],
    new_name='ingress-nginx',
    port_forwards=[port_forward(8000, 80)],
)

# Add links
k8s_resource(
    'minio',
    links=[link('http://files.localhost:8000/minio/', 'Minio Browser')],
)
k8s_resource(
    'web',
    links=[link('http://localhost:8000/', 'Frontend')],
)
