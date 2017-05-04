from doit import get_var
import os
import subprocess


DOIT_CONFIG = {
    'default_tasks': ['build', 'pull'],
    'continue': True,
}

PREFIX = get_var('prefix', 'reproserver_')


def exists(object, type):
    proc = subprocess.Popen(['docker', 'inspect', '--type={0}'.format(type),
                             object],
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    _, _ = proc.communicate()
    return proc.wait() == 0


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


def task_pull():
    for image in ['rabbitmq:3.6.9-management',
                  'registry:2.6',
                  'minio/minio:RELEASE.2017-04-29T00-40-27Z']:
        yield {
            'name': image.split(':', 1)[0].split('/', 1)[-1],
            'actions': ['docker pull {0}'.format(image)],
            'uptodate': [exists(image, 'image')],
            'clean': ['docker rmi {0}'.format(image)],
        }


def task_volume():
    for name in ['rabbitmq', 'minio']:
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
