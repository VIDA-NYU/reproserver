__all__ = ['ShortIDs']


CHARS = '023456789abcdefghijkmnopqrstuvwxyz'


def _encode(nb, min_chars, chars):
    nb_chars = len(chars)
    shortid = []
    idx = 0
    i = 0
    while nb or i < min_chars:
        idx = (idx + nb) % nb_chars
        shortid.append(chars[idx])
        idx += 1
        nb = nb // nb_chars
        i += 1
    return ''.join(reversed(shortid))


def _decode(shortid, cmap):
    nb_chars = len(cmap)
    nb = 0
    e = 1
    prev_idx = 0
    for c in reversed(shortid):
        try:
            idx = cmap[c]  # Can raise KeyError if invalid characters
        except KeyError:
            raise ValueError("shortid contains invalid character %r" % c)
        d = (idx - prev_idx) % nb_chars
        nb += d * e
        prev_idx = idx + 1
        e *= nb_chars
    return nb


class ShortIDs(object):
    """Encodes IDs as short strings.

    This turns a number into a short random-looking string like 'dxT2_x'.
    """
    def __init__(self, salt):
        chars = list(CHARS)
        salt_len = len(salt)
        nb_chars = len(chars)
        for i in range(nb_chars):
            s = ord(salt[i % salt_len])
            j = i + s % (nb_chars - i)
            chars[i], chars[j] = chars[j], chars[i]
        self.chars = ''.join(chars)
        self.cmap = dict((c, i) for i, c in enumerate(self.chars))

    def encode(self, nb, min_chars=5):
        """Encode a number into a random-looking short ID.
        """
        return _encode(nb, min_chars, self.chars)

    def decode(self, shortid):
        """Decode a random-looking short ID into the original number.
        """
        return _decode(shortid, self.cmap)


class MultiShortIDs(object):
    """Generates multiple sequences of short IDs.

    You probably don't want IDs for different things to follow the same
    sequence. Use this class to use multiple sequences, without having to keep
    multiple generators around (or provide multiple salts).
    """
    def __init__(self, salt, min_chars=5):
        self.salt = salt
        self.min_chars = min_chars
        self.shortids = {}

    def encode(self, key, nb):
        try:
            s = self.shortids[key]
        except KeyError:
            s = self.shortids[key] = ShortIDs(key + self.salt)
        return s.encode(nb, self.min_chars)

    def decode(self, key, shortid):
        try:
            s = self.shortids[key]
        except KeyError:
            s = self.shortids[key] = ShortIDs(key + self.salt)
        return s.decode(shortid)
