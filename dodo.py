from doit import get_var
import json
import os
import subprocess
import sys


DOIT_CONFIG = {
    'default_tasks': ['build', 'pull'],
    'continue': True,
}

PREFIX = get_var('prefix', 'reproserver-')


CONFIG = {
    'ADMIN_USER': 'reproserver',
    'ADMIN_PASSWORD': 'hackmehackme',
    'AMQP_HOST': '%srabbitmq' % PREFIX,
    'S3_URL': 'http://%sminio:9000' % PREFIX,
    'POSTGRES_HOST': '%spostgres' % PREFIX,
    'POSTGRES_DB': 'reproserver',
}
if os.path.exists('config.py'):
    with open('config.py') as f:
        code = compile(f.read(), 'config.py', 'exec')
        exec(code, CONFIG, CONFIG)
else:
    sys.stderr.write("config.py doesn't exist, using default values\n")
AMQP_USER = CONFIG.get('AMQP_USER') or CONFIG['ADMIN_USER']
AMQP_PASSWORD = CONFIG.get('AMQP_PASSWORD') or CONFIG['ADMIN_PASSWORD']
AMQP_HOST = CONFIG['AMQP_HOST']
S3_KEY = CONFIG.get('S3_KEY') or CONFIG['ADMIN_USER']
S3_SECRET = CONFIG.get('S3_SECRET') or CONFIG['ADMIN_PASSWORD']
S3_URL = CONFIG.get('S3_URL')
POSTGRES_USER = CONFIG.get('POSTGRES_USER') or CONFIG['ADMIN_USER']
POSTGRES_PASSWORD = CONFIG.get('POSTGRES_PASSWORD') or CONFIG['ADMIN_PASSWORD']
POSTGRES_HOST = CONFIG['POSTGRES_HOST']
POSTGRES_DB = CONFIG['POSTGRES_DB']


def merge(*args):
    ret = {}
    for dct in args:
        ret.update(dct)
    return ret


def exists(object, type):
    def wrapped():
        proc = subprocess.Popen(['docker', 'inspect',
                                 '--type={0}'.format(type), object],
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        _, _ = proc.communicate()
        return proc.wait() == 0

    return wrapped


def inspect(object, type):
    proc = subprocess.Popen(['docker', 'inspect', '--type={0}'.format(type),
                             object],
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
        image = PREFIX + name
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
                    tag=get_var('tag', 'git'))
        yield {
            'name': name,
            'actions': [
                'docker tag {prefix}{image} {registry}/{image}:{tag}'
                .format(**args),
                'docker push {registry}/{image}:{tag}'
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


def task_volume():
    for name in ['rabbitmq', 'minio', 'postgres']:
        volume = PREFIX + name
        yield {
            'name': name,
            'actions': ['docker volume create {0}'.format(volume)],
            'uptodate': [exists(volume, 'volume')],
            'clean': ['docker volume rm {0}'.format(volume)],
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
    info = inspect(container, 'container')
    if info and info[0]['State']['Running']:
        subprocess.check_call('docker stop {0}'.format(container),
                              shell=True)
    if info:
        subprocess.check_call('docker rm {0}'.format(container),
                              shell=True)
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
    command.append(dct['image'])
    if 'command' in dct:
        command.extend(dct['command'])
    subprocess.check_call(command)


common_env = {
    'AMQP_USER': AMQP_USER,
    'AMQP_PASSWORD': AMQP_PASSWORD,
    'AMQP_HOST': AMQP_HOST,
    'S3_KEY': S3_KEY,
    'S3_SECRET': S3_SECRET,
    'S3_URL': S3_URL,
    'POSTGRES_USER': POSTGRES_USER,
    'POSTGRES_PASSWORD': POSTGRES_PASSWORD,
    'POSTGRES_HOST': POSTGRES_HOST,
    'POSTGRES_DB': POSTGRES_DB,
}
services = [
    ('web', {
        'image': PREFIX + 'web',
        'deps': ['start:rabbitmq', 'build:web'],
        'command': ['debug'],
        'volumes': ['{d}/web/static:/usr/src/app/static',
                    '{d}/web/web:/usr/src/app/web'],
        'env': common_env,
        'ports': ['8000:8000'],
    }),
    ('builder', {
        'image': PREFIX + 'builder',
        'deps': ['start:rabbitmq', 'start:registry', 'start:minio',
                 'build:builder'],
        'volumes': ['/var/run/docker.sock:/var/run/docker.sock'],
        'env': merge(common_env, {'REPROZIP_USAGE_STATS': 'off'}),
    }),
    ('runner', {
        'image': PREFIX + 'runner',
        'deps': ['start:rabbitmq', 'start:registry', 'start:minio',
                 'build:runner'],
        'env': common_env,
    }),
    ('rabbitmq', {
        'image': 'rabbitmq:3.6.9-management',
        'deps': ['pull:rabbitmq', 'volume:rabbitmq'],
        'volumes': ['{p}rabbitmq:/var/lib/rabbitmq'],
        'env': {'RABBITMQ_DEFAULT_USER': AMQP_USER,
                'RABBITMQ_DEFAULT_PASS': AMQP_PASSWORD},
        'ports': ['8080:15672'],
    }),
    ('minio', {
        'image': 'minio/minio:RELEASE.2017-04-29T00-40-27Z',
        'deps': ['pull:minio', 'volume:minio'],
        'command': ['server', '/export'],
        'volumes': ['{p}minio:/export'],
        'env': {'MINIO_ACCESS_KEY': S3_KEY,
                'MINIO_SECRET_KEY': S3_SECRET},
        'ports': ['9000:9000'],
    }),
    ('registry', {
        'image': 'registry:2.6',
        'deps': ['pull:registry'],
        'ports': ['5000:5000'],
    }),
    ('postgres', {
        'image': 'postgres:9.6',
        'deps': ['pull:postgres', 'volume:postgres'],
        'volumes': ['{p}postgres:/var/lib/postgresql/data'],
        'env': {'PGDATA': '/var/lib/postgresql/data/pgdata',
                'POSTGRES_USER': POSTGRES_USER,
                'POSTGRES_PASSWORD': POSTGRES_PASSWORD},
        'ports': ['5432:5432'],
    }),
]


def task_start():
    for name, dct in services:
        container = PREFIX + name
        yield {
            'name': name,
            'actions': [(run, [name, dct])],
            'uptodate': [container_uptodate(container, dct['image'])],
            'task_dep': ['network'] + dct.get('deps', []),
            'clean': ['docker stop {0} || true'.format(container),
                      'docker rm {0} || true'.format(container)],
        }
