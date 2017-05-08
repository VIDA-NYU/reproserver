from doit import get_var
import json
import os
import subprocess


DOIT_CONFIG = {
    'default_tasks': ['build', 'pull'],
    'continue': True,
}

PREFIX = get_var('prefix', 'reproserver-')


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
            'actions': ['tar -c {0} common | '
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
    subprocess.check_call('docker run -d --name {0} '
                          '--network reproserver {2} {3} {4} '
                          '{1} {5}'.format(
                              container,
                              dct['image'],
                              ' '.join('-v {0}'.format(PREFIX + v)
                                       for v in dct.get('volumes', [])),
                              ' '.join('-e {0}={1}'.format(*e)
                                       for e in dct.get('env', {}).items()),
                              ' '.join('-p {0}'.format(p)
                                       for p in dct.get('ports', [])),
                              dct.get('command', '')),
                          shell=True)


services = [
    ('web', {
        'image': PREFIX + 'web',
        'deps': ['start:rabbitmq', 'build:web'],
        'command': 'debug',
        'ports': ['8000:8000'],
    }),
    ('builder', {
        'image': PREFIX + 'builder',
        'deps': ['start:rabbitmq', 'start:registry', 'start:minio',
                 'build:builder'],
    }),
    ('runner', {
        'image': PREFIX + 'runner',
        'deps': ['start:rabbitmq', 'start:registry', 'start:minio',
                 'build:runner'],
    }),
    ('rabbitmq', {
        'image': 'rabbitmq:3.6.9-management',
        'volumes': ['rabbitmq:/var/lib/rabbitmq'],
        'deps': ['pull:rabbitmq', 'volume:rabbitmq'],
        'env': {'RABBITMQ_DEFAULT_USER': 'admin',
                'RABBITMQ_DEFAULT_PASS': 'hackme'},
        'ports': ['8080:15672'],
    }),
    ('minio', {
        'image': 'minio/minio:RELEASE.2017-04-29T00-40-27Z',
        'volumes': ['minio:/export'],
        'deps': ['pull:minio', 'volume:minio'],
        'command': 'server /export',
        'env': {'MINIO_ACCESS_KEY': 'admin',
                'MINIO_SECRET_KEY': 'hackmehackme'},
        'ports': ['9000:9000'],
    }),
    ('registry', {
        'image': 'registry:2.6',
        'deps': ['pull:registry'],
    }),
    ('postgres', {
        'image': 'postgres:9.6',
        'volumes': ['postgres:/var/lib/postgresql/data'],
        'deps': ['pull:postgres', 'volume:postgres'],
        'env': {'PGDATA': '/var/lib/postgresql/data/pgdata',
                'POSTGRES_USER': 'reproserver',
                'POSTGRES_PASSWORD': 'hackmehackme'},
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
