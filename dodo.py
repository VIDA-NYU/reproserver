from doit import get_var
from doit.exceptions import TaskFailed
from doit.tools import config_changed
import json
import os
import subprocess
import sys


DOIT_CONFIG = {
    'default_tasks': ['build', 'pull'],
    'continue': True,
}

PREFIX = get_var('prefix', 'reproserver-')
TAG = get_var('tag', '')
if TAG:
    TAG = ':%s' % TAG


def merge(*args):
    ret = {}
    for dct in args:
        ret.update(dct)
    return ret


def exists(object, type):
    def wrapped():
        proc = subprocess.Popen(['docker', 'inspect',
                                 '--type={0}'.format(type), '--', object],
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        _, _ = proc.communicate()
        return proc.wait() == 0

    return wrapped


def inspect(object, type):
    proc = subprocess.Popen(['docker', 'inspect', '--type={0}'.format(type),
                             '--', object],
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    stdout, _ = proc.communicate()
    if proc.wait() != 0:
        return None
    else:
        return json.loads(stdout.decode('ascii'))


def list_files(*directories):
    files = []
    for directory in directories:
        for dirpath, dirnames, filenames in os.walk(directory):
            for filename in filenames:
                files.append(os.path.join(dirpath, filename))
    return files


def task_build():
    for name in ['web', 'builder', 'runner']:
        image = PREFIX + name + TAG
        yield {
            'name': name,
            'actions': ['tar -cX {0}/.tarignore {0} common | '
                        'docker build -f {0}/Dockerfile -t {1} -'
                        .format(name, image)],
            'uptodate': [exists(image, 'image')],
            'file_dep': list_files(name, 'common'),
            'clean': ['docker rmi {0}'.format(image)],
        }


def task_push():
    for name in ['web', 'builder', 'runner']:
        args = dict(prefix=PREFIX, image=name,
                    registry=get_var('registry', 'unset.example.org'),
                    tag=TAG)
        yield {
            'name': name,
            'actions': [
                'docker tag {prefix}{image} {registry}/{image}{tag}'
                .format(**args),
                'docker push {registry}/{image}{tag}'
                .format(**args),
            ],
            'task_dep': ['build:{0}'.format(name)],
        }


def task_pull():
    for image in ['rabbitmq:3.6.9-management',
                  'minio/minio:RELEASE.2017-04-29T00-40-27Z',
                  'registry:2.6',
                  'postgres:9.6']:
        yield {
            'name': image.split(':', 1)[0].split('/', 1)[-1],
            'actions': ['docker pull {0}'.format(image)],
            'uptodate': [exists(image, 'image')],
            'clean': ['docker rmi {0}'.format(image)],
        }


def task_network():
    return {
        'actions': ['docker network create reproserver'],
        'uptodate': [exists('reproserver', 'network')],
        'clean': ['docker network rm reproserver'],
    }


def container_uptodate(container, image):
    def wrapped():
        container_info = inspect(container, 'container')
        if not container_info or not container_info[0]['State']['Running']:
            return False
        image_info = inspect(image, 'image')
        if not image_info:  # Shouldn't happen, container is running
            return False
        return container_info[0]['Image'] == image_info[0]['Id']

    return wrapped


def run(name, dct):
    container = PREFIX + name
    if inspect(container, 'container') is not None:
        subprocess.check_call(['docker', 'rm', '-f', '-v', '--', container])
    command = ['docker', 'run', '-d',
               '--name', container,
               '--network', 'reproserver']
    for v in dct.get('volumes', []):
        command.extend(['-v', v.format(p=PREFIX, d=os.getcwd())])
    for k, v in dct.get('env', {}).items():
        if v is not None:
            command.extend(['-e', '{0}={1}'.format(k, v)])
    for p in dct.get('ports', []):
        command.extend(['-p', p])
    if 'user' in dct:
        command.extend(['--user', dct.get('user')])
    command.append('--')
    command.append(dct['image'])
    if 'command' in dct:
        command.extend(dct['command'])
    subprocess.check_call(command)


def get_version():
    try:
        out = subprocess.check_output(['git', 'describe', '--tags'])
    except subprocess.CalledProcessError:
        return None
    else:
        return out.decode('utf-8').strip()


ADMIN_USER = 'reproserver'
ADMIN_PASSWORD = 'hackmehackme'
common_env = {
    'REPROSERVER_VERSION': get_version(),
    'SHORTIDS_SALT': 'thisisarandomstring',
    'AMQP_USER': ADMIN_USER,
    'AMQP_PASSWORD': ADMIN_PASSWORD,
    'AMQP_HOST': '%srabbitmq' % PREFIX,
    'S3_KEY': ADMIN_USER,
    'S3_SECRET': ADMIN_PASSWORD,
    'S3_URL': 'http://%sminio:9000' % PREFIX,
    'S3_BUCKET_PREFIX': PREFIX,
    'S3_CLIENT_URL': 'http://localhost:9000',
    'POSTGRES_USER': ADMIN_USER,
    'POSTGRES_PASSWORD': ADMIN_PASSWORD,
    'POSTGRES_HOST': '%spostgres' % PREFIX,
    'POSTGRES_DB': 'reproserver',
}
services = [
    ('web', {
        'image': PREFIX + 'web' + TAG,
        'deps': ['start:rabbitmq', 'build:web'],
        'command': ['debug'],
        'volumes': ['{d}/web/static:/usr/src/app/static',
                    '{d}/web/web:/usr/src/app/web'],
        'env': common_env,
        'ports': ['8000:8000'],
    }),
    ('builder', {
        'image': PREFIX + 'builder' + TAG,
        'deps': ['start:rabbitmq', 'start:registry', 'start:minio',
                 'build:builder'],
        'user': '0',
        'volumes': ['/var/run/docker.sock:/var/run/docker.sock'],
        'env': merge(common_env, {'REPROZIP_USAGE_STATS': 'off'}),
    }),
    ('runner', {
        'image': PREFIX + 'runner' + TAG,
        'deps': ['start:rabbitmq', 'start:registry', 'start:minio',
                 'build:runner'],
        'user': '0',
        'volumes': ['/var/run/docker.sock:/var/run/docker.sock'],
        'env': common_env,
    }),
    ('rabbitmq', {
        'image': 'rabbitmq:3.6.9-management',
        'deps': ['pull:rabbitmq'],
        'env': {'RABBITMQ_DEFAULT_USER': ADMIN_USER,
                'RABBITMQ_DEFAULT_PASS': ADMIN_PASSWORD},
        'ports': ['8080:15672'],
    }),
    ('minio', {
        'image': 'minio/minio:RELEASE.2017-04-29T00-40-27Z',
        'deps': ['pull:minio'],
        'command': ['server', '/export'],
        'env': {'MINIO_ACCESS_KEY': ADMIN_USER,
                'MINIO_SECRET_KEY': ADMIN_PASSWORD},
        'ports': ['9000:9000'],
    }),
    ('registry', {
        'image': 'registry:2.6',
        'deps': ['pull:registry'],
        'ports': ['5000:5000'],
    }),
    ('postgres', {
        'image': 'postgres:9.6',
        'deps': ['pull:postgres'],
        'env': {'PGDATA': '/var/lib/postgresql/data/pgdata',
                'POSTGRES_USER': ADMIN_USER,
                'POSTGRES_PASSWORD': ADMIN_PASSWORD},
        'ports': ['5432:5432'],
    }),
]


def task_start():
    for name, dct in services:
        container = PREFIX + name
        yield {
            'name': name,
            'actions': [(run, [name, dct])],
            'uptodate': [container_uptodate(container, dct['image']),
                         config_changed({'prefix': PREFIX, 'tag': TAG})],
            'task_dep': ['network'] + dct.get('deps', []),
            'clean': ['docker rm -f -v {0} || true'.format(container)],
        }


_k8s_config = None


def get_k8s_config():
    global _k8s_config

    if _k8s_config is not None:
        return dict(_k8s_config)

    tier = get_var('tier', None)
    storage_driver = get_var('storage_driver', 'overlay2')
    if tier is None:
        return {'error': "Please set the tier on the command-line, for "
                         "example `tier=dev` or `tier=prod`"}

    if os.path.exists('config.yml'):
        import yaml

        with open('config.yml', encoding='utf-8') as fp:
            config = yaml.safe_load(fp)

        if tier not in config:
            return {'error': "config.yml doesn't have an entry for tier=%s" %
                             tier}
        _k8s_config = config[tier]
    else:
        sys.stderr.write("config.yml doesn't exist, using default values\n")
        _k8s_config = {}
    _k8s_config['tier'] = tier
    _k8s_config['storage_driver'] = storage_driver
    if TAG:
        _k8s_config['tag'] = TAG
    return dict(_k8s_config)


def make_k8s_def():
    import jinja2

    config = get_k8s_config()

    if 'error' in config:
        raise ValueError(config['error'])

    context = {}

    registry = config.pop('image_registry', None)
    if registry:
        context['image_registry'] = '%s/' % registry

    for key in ['postgres', 'minio']:
        value = config.pop('%s_volume' % key, None)
        if isinstance(value, str):
            pass
        elif value:
            value = 'reproserver-{key}-{tier}'.format(key=key,
                                                      tier=config['tier'])
        else:
            value = ''
        context['%s_volume' % key] = value

    context['use_minio'] = use_minio = config.pop('use_minio', False)
    if use_minio:
        context['s3_url'] = 'http://reproserver-minio-{tier}:9000'.format(
            tier=config['tier'])
        context['s3_bucket_prefix'] = 'minio'
    else:
        context['s3_url'] = ''
        context['s3_bucket_prefix'] = config.pop('s3_bucket_prefix')
    context['s3_client_url'] = config.pop('s3_client_url', '')

    if 'tag' not in config:
        return TaskFailed("You must set a tag explicitly, either in "
                          "config.yml or on the command-line")
    context['tag'] = config.pop('tag')
    if context['tag'].startswith(':'):
        context['version'] = context['tag'][1:]
    else:
        context['version'] = context['tag']
    context['tier'] = config.pop('tier')
    context['postgres_db'] = config.pop('postgres_database', 'reproserver')
    context['storage_driver'] = config.pop('storage_driver')
    context['liveness_probe_period_seconds'] = config.pop(
        'liveness_probe_period_seconds', 30)

    if config:
        sys.stderr.write("Warning: unrecognized config options:\n")
        for k in config:
            sys.stderr.write("    %s\n" % k)

    env = jinja2.Environment(loader=jinja2.FileSystemLoader('.'))
    template = env.get_template('k8s.tpl.yml')
    with open('k8s.yml', 'w') as out:
        out.write(template.render(context))


def task_k8s():
    return {
        'actions': [make_k8s_def],
        'file_dep': ['k8s.tpl.yml'],
        'uptodate': [config_changed(get_k8s_config())],
        'targets': ['k8s.yml'],
    }
