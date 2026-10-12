class BankWiseError(Exception):
    pass


class InvalidImageError(BankWiseError):
    pass


class ConfigError(BankWiseError):
    pass


class ReaderError(BankWiseError):
    pass