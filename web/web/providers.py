__all__ =['get_experiment_from_provider']


def _osf(path):
    raise NotImplementedError  # TODO


def _figshare(path):
    raise NotImplementedError  # TODO


_PROVIDERS = {
    'osf.io': _osf,
    'figshare.com': _figshare,
}


def get_experiment_from_provider(session, provider, provider_path):
    try:
        getter = _PROVIDERS[provider]
    except KeyError:
        return None
    return getter(provider_path)
