from .base import RepositoryError
from .osf import OSF
from .zenodo import Zenodo
from .figshare import Figshare
from .mendeley import Mendeley


__all__ = [
    'RepositoryError',
    'get_experiment_from_repository', 'parse_repository_url',
]


_repositories = [OSF, Zenodo, Figshare, Mendeley]


_map = {}
_parse_domains = {}
for repo_class in _repositories:
    repo = repo_class()
    assert repo.IDENTIFIER
    _map[repo.IDENTIFIER] = repo
    for domain in repo.URL_DOMAINS:
        _parse_domains[domain] = repo


def get_experiment_from_repository(db, object_store, remote_addr,
                                   repo, repo_path):
    """Get a file from a reference in a repository.
    """
    try:
        repo_obj = _map[repo]
    except KeyError:
        raise RepositoryError("No such repository %s" % repo)
    return repo_obj.get_experiment(
        db, object_store, remote_addr,
        repo, repo_path,
    )


def parse_repository_url(url):
    """Parse a URL into a reference to a file in a repository.
    """
    if url.startswith('http://'):
        domain = url[7:]
    elif url.startswith('https://'):
        domain = url[8:]
    else:
        raise RepositoryError("Invalid URL")

    idx = domain.find('/')
    if idx != -1:
        domain = domain[:idx]

    try:
        repo = _parse_domains[domain.lower()]
    except KeyError:
        raise RepositoryError("Unrecognized URL")
    else:
        return repo.parse_url(url)
