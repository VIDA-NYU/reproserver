version_settings(constraint='>=0.22.2')
load('ext://docker_build_sub', 'docker_build_sub')

# Rebuild image if those change
full_rebuild = ['pyproject.toml', 'poetry.lock', 'reprozip']
# Just sync those and let Tornado reload
just_sync = ['reproserver/repositories', 'reproserver/static', 'reproserver/templates', 'reproserver/web', 'reproserver/main.py', 'reproserver/proxy.py']
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
    only=['reprozip', 'reproserver', 'pyproject.toml', 'poetry.lock', 'README.md', 'LICENSE.txt'],
    live_update=[
        fall_back_on(full_rebuild),
    ] + [
        sync(path, '/usr/src/app/' + path)
        for path in just_sync
    ],
    # Update the OVERRIDE_RUNNER_IMAGE variable
    match_in_env_vars=True,
)

# Run Helm chart
yaml = helm('k8s/helm', name='reproserver', values=['k8s/minikube.values.yml'], set=['debugMode=true'])
k8s_yaml(yaml)

# Add links
k8s_resource(
    'reproserver-minio',
    links=[link('http://files.localhost:8000/minio/', 'Minio Browser')],
)
k8s_resource(
    'reproserver',
    links=[link('http://localhost:8000/', 'Frontend')],
)
